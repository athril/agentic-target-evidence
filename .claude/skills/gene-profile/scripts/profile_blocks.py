# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Programmatic blocks of a profile: tables and figures the scripts write, not the model.

``draft.md`` names a block on a line of its own:

    {{table:allelic_series}}     the allelic series (profile_tables.py), with the tally of
                                 what the disease variants do to the target
    {{table:variant_spectrum}}   ClinVar P/LP by consequence and region; gnomAD pLoF
    {{table:structures}}         PDB entries by resolution and the AlphaFold model
    {{table:constraint}}         gnomAD constraint with bands, release and transcript
    {{table:essentiality}}       each CRISPR screen's gene-level call and per-line rule
    {{table:tissue_evidence}}    expression by kind: RNA, protein, secretion, localisation
    {{table:faers}}              FAERS per approved drug, with what spontaneous reports
                                 cannot show
    {{table:indication}}         gene × disease evidence: association, L2G, approved drugs
                                 and trials in the disease, the gene's trials and papers in
                                 it, prevalence
    {{figure:protein_map}}       the protein map (profile_figures.py)
    {{figure:expression}}        GTEx tissues and HPA single-cell types

Each block declares (``BLOCKS``) the records it reads, the section it must sit in when it
is required, whether its content is built from records or chosen by the LLM (its caption
ends "From records: …" or "Chosen by the LLM from: …"), and its empty state: a block whose
records hold nothing renders a sentence saying so and why — it never vanishes.

``check_profile.py`` validates the names and requires the blocks ``required`` lists in their
sections; ``render.py`` replaces each line with its markdown. Table rows and figure captions
carry their own citations, so they number like the prose.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from profile_schema import TEMPLATE_VERSION, load_manifest, load_raw

BLOCK_RE = re.compile(r"^\s*\{\{\s*((?:table|figure):[a-z_]+)\s*\}\}\s*$")


@dataclass(frozen=True)
class BlockSpec:
    keys: tuple[str, ...]  # raw records the block reads, the main one first
    section: str = ""  # the section it must sit in when required
    source: str = "records"  # "records" (built from the records) or "llm" (chosen by the LLM)
    empty: str = ""  # what the block says when its records hold nothing


BLOCKS: dict[str, BlockSpec] = {
    "table:allelic_series": BlockSpec(
        ("allelic_series", "uniprot_extra", "clinvar", "gnomad_lof", "gwas", "pubtator_map"),
        "genetics",
        empty="No variant in the series: no UniProt disease variant, ClinVar pathogenic / "
        "likely pathogenic call, gnomAD pLoF, GWAS lead or literature variant for this gene.",
    ),
    "table:variant_spectrum": BlockSpec(
        ("allelic_series", "clinvar", "gnomad_lof"),
        empty="No ClinVar pathogenic / likely pathogenic variant or gnomAD pLoF recorded.",
    ),
    "table:structures": BlockSpec(
        ("uniprot_extra", "alphafold"), empty="No PDB entry and no AlphaFold model."
    ),
    "table:indication": BlockSpec(
        ("ot_association_disease",), "indication", empty="No record scoped to the disease."
    ),
    "table:faers": BlockSpec(("faers",), empty="No FAERS report for the gene's approved drugs."),
    "table:constraint": BlockSpec(
        ("gnomad_constraint",), empty="No gnomAD constraint for the gene's transcript."
    ),
    "table:essentiality": BlockSpec(
        ("depmap", "project_score"), empty="Not in the DepMap or Project Score screens."
    ),
    "table:tissue_evidence": BlockSpec(("hpa", "gtex"), empty="No HPA or GTEx expression record."),
    "table:how_we_know": BlockSpec(
        ("pubmed_abstracts", "pubtator_map"),
        "snapshot",
        source="llm",
        empty="No how_we_know.json: the writer has not listed the core claims and their sources.",
    ),
    "table:models": BlockSpec(
        ("orthologs", "ot_mouse", "impc", "hpo", "gnomad_lof"),
        "biology",
        empty="No mouse ortholog record.",
    ),
    "table:partners": BlockSpec(
        ("string",), "biology", empty="No STRING partners above the score floor."
    ),
    "table:reagents": BlockSpec(
        ("hpa", "ot_mouse", "orthologs", "chembl"), "toolkit", empty="No reagent record."
    ),
    "table:immune": BlockSpec(
        ("hpa", "hpo_terms", "ot_mouse"), "biology", empty="No immune-cell record."
    ),
    "table:variant_effects": BlockSpec(
        ("allelic_series", "gwas", "variant_frequencies"),
        "genetics",
        empty="No series variant with a GWAS association or a gnomAD frequency.",
    ),
    "table:somatic": BlockSpec(
        ("somatic", "uniprot_extra"),
        "genetics",
        empty="No mutation or copy-number event in the TCGA PanCancer Atlas studies.",
    ),
    "figure:phenome": BlockSpec(
        ("gwas", "gwas_support"), "genetics", empty="No GWAS association at this gene."
    ),
    "figure:allelic_spectrum": BlockSpec(
        ("allelic_series", "variant_frequencies", "gwas"),
        "genetics",
        empty="No variant with both a frequency and an effect.",
    ),
    "figure:protein_map": BlockSpec(
        ("uniprot_extra", "clinvar", "gnomad_lof", "alphafold"),
        empty="No protein map: UniProt gives no length or features for this gene.",
    ),
    "figure:expression": BlockSpec(
        ("gtex", "hpa"), empty="No GTEx tissue or HPA single-cell expression to plot."
    ),
}
# Blocks that template-2 drafts must carry, with their section; each renders its empty
# state when its records or writer file hold nothing.
REQUIRED_V2: dict[str, str] = {
    "table:how_we_know": "snapshot",
    "table:models": "biology",
    "table:partners": "biology",
    "table:immune": "biology",
    "table:reagents": "toolkit",
    "table:variant_effects": "genetics",
    "figure:phenome": "genetics",
    "figure:allelic_spectrum": "genetics",
    "table:somatic": "genetics",
}
# Blocks about the protein: a non-coding gene renders one line in their place.
PROTEIN_BLOCKS = {"figure:protein_map", "table:structures", "table:partners"}
FIGURE_DIR = "figures"
# Records scoped to the run's disease, in the order the indication table shows them.
INDICATION_KEYS = (
    "ot_association_disease",
    "ot_l2g",
    "openfda_indication",
    "ct_condition",
    "ct_gene_disease",
    "pubmed_disease",
    "orphanet_prevalence",
    "gbd",
)


def find_blocks(draft: str) -> list[tuple[int, str]]:
    """(line number, block name) of every block line in the draft."""
    return [
        (i, m.group(1))
        for i, line in enumerate(draft.splitlines(), start=1)
        if (m := BLOCK_RE.match(line))
    ]


def blank_blocks(draft: str) -> str:
    """The draft with block lines emptied (line numbers kept) for the prose checks."""
    return "\n".join("" if BLOCK_RE.match(line) else line for line in draft.splitlines())


def _data(run_dir: Path, key: str) -> dict[str, Any]:
    rec = load_raw(run_dir, key)
    return rec.data if rec and rec.status == "ok" and isinstance(rec.data, dict) else {}


def _indication_records(run_dir: Path) -> dict[str, tuple[str, Any, str]]:
    out: dict[str, tuple[str, Any, str]] = {}
    for key in INDICATION_KEYS:
        rec = load_raw(run_dir, key)
        if rec is not None:
            out[key] = (rec.status, rec.data, rec.note)
    return out


def template(run_dir: Path) -> int:
    """The draft template the run was gathered for (1 for runs without a manifest field)."""
    path = run_dir / "manifest.json"
    return load_manifest(run_dir).template if path.exists() else TEMPLATE_VERSION


def required(run_dir: Path, disease: str = "") -> dict[str, str]:
    """Blocks the draft must contain, with their section: the allelic series when it has
    rows, the indication table when the run is scoped to a disease and holds its records."""
    out: dict[str, str] = {}
    series = (_data(run_dir, "allelic_series").get("series") or {}).get("rows") or []
    if series:
        out["table:allelic_series"] = "genetics"
    if disease and _indication_records(run_dir):
        out["table:indication"] = "indication"
    if template(run_dir) >= 2:
        out.update(REQUIRED_V2)
    return out


def load_functions(run_dir: Path) -> Any:
    """The writer's functional-study entries (profile_tables.FUNCTION_FILE), or [] when the
    run has none; malformed JSON is returned as None for the checker to report."""
    from profile_tables import FUNCTION_FILE

    path = run_dir / FUNCTION_FILE
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def clinvar_key(run_dir: Path) -> str:
    """The ClinVar record the series and spectrum were built from: ClinVar itself, or
    gnomAD's mirror when ClinVar was not fetched (older runs)."""
    spectrum = _data(run_dir, "allelic_series").get("spectrum") or {}
    return spectrum.get("clinvar_key") or "gnomad_clinvar"


def _cite(keys: list[str], available: set[str]) -> str:
    keep = [k for k in dict.fromkeys(keys) if k in available]
    return "[" + "; ".join(f"@{k}" for k in keep) + "]" if keep else ""


# ── provenance and empty states ─────────────────────────────────────────────────────


def provenance(source: str, keys: list[str]) -> str:
    """The caption ending that says whether a block's content comes from the records or
    was chosen by the LLM, naming the records or sources it rests on."""
    named = ", ".join(dict.fromkeys(keys)) or "none"
    return f"From records: {named}." if source == "records" else f"Chosen by the LLM from: {named}."


def _keys(run_dir: Path, spec: BlockSpec) -> list[str]:
    """The block's records, with the ClinVar record the series was built from."""
    ck = clinvar_key(run_dir)
    return list(dict.fromkeys(ck if k == "clinvar" else k for k in spec.keys))


def _found(run_dir: Path, keys: list[str]) -> list[str]:
    """The records among ``keys`` that returned data in this run (the main one at least)."""
    return [
        k for k in keys if (r := load_raw(run_dir, k)) is not None and r.status == "ok"
    ] or keys[:1]


def empty_state(run_dir: Path, name: str, available: set[str]) -> str:
    """What a block says when it has nothing to show: the reason a record was not checked
    (skipped, failed, not gathered), or the block's own "nothing found" sentence citing the
    records that were queried."""
    spec = BLOCKS[name]
    if spec.source == "llm":
        return f"*{spec.empty}*"
    keys = _keys(run_dir, spec)
    recs = {k: load_raw(run_dir, k) for k in keys}
    main = recs[keys[0]]
    if main is None:
        return f"*Not checked: `{keys[0]}` was not gathered in this run.*"
    if main.status in ("skipped", "error"):
        why = f" ({main.note})" if main.note else ""
        return f"*Not checked: {main.db_name} {main.status}{why}.*"
    queried = [k for k, r in recs.items() if r is not None and r.status in ("ok", "empty")]
    return f"*{spec.empty}* {_cite(queried, available)}".rstrip()


def variant_records(run_dir: Path) -> list[dict[str, Any]]:
    """The series' top rows harmonised (profile_variants.harmonise) with the GWAS hits,
    gnomAD frequencies and the writer's functional studies."""
    from profile_tables import MAX_ROWS, functions_by_variant
    from profile_variants import harmonise

    rows = ((_data(run_dir, "allelic_series").get("series") or {}).get("rows") or [])[:MAX_ROWS]
    papers = {
        v: next(iter({str(e.get("direction")) for e in found} - {"neutral"}), "")
        for v, found in functions_by_variant(load_functions(run_dir)).items()
    }
    return harmonise(
        rows,
        _data(run_dir, "gwas").get("hits") or [],
        _data(run_dir, "variant_frequencies"),
        {v: d for v, d in papers.items() if d},
    )


# ── figures ─────────────────────────────────────────────────────────────────────────


def _protein_map(run_dir: Path, gene: str, path: Path) -> Path | None:
    from profile_figures import pdb_ranges, protein_map
    from profile_tables import consequence_class, plp_variants, protein_change

    uni = _data(run_dir, "uniprot_extra")
    clinvar = _data(run_dir, clinvar_key(run_dir))
    lof = _data(run_dir, "gnomad_lof")
    series = (_data(run_dir, "allelic_series").get("series") or {}).get("rows") or []
    missense, truncating = [], []
    for v in plp_variants(clinvar):
        c = protein_change(v.get("hgvsp") or "")
        kind = consequence_class(v.get("major_consequence") or "")
        if c and kind == "missense":
            missense.append(c[1])
        elif c and kind == "truncating":
            truncating.append(c[1])
    lof_pos = [
        c[1]
        for v in lof.get("reported_variants") or []
        if (c := protein_change(v.get("hgvsp") or ""))
    ]
    labels: dict[int, str] = {}
    for r in series:
        if r.get("position") and r.get("change") and r["position"] not in labels:
            labels[r["position"]] = r["change"]
        if len(labels) >= 10:
            break
    from profile_variants import effect_class

    effects: dict[int, str] = {}
    for r in series:
        kind = effect_class(r.get("effect") or "")
        if r.get("position") and kind:
            effects.setdefault(r["position"], kind)
    somatic, somatic_labels = {}, {}
    if (rec := load_raw(run_dir, "somatic")) is not None and rec.status == "ok":
        from profile_somatic import residue_counts, somatic_pattern

        pattern = somatic_pattern(rec.data, int(uni.get("length") or 0))
        somatic = residue_counts(rec.data)
        somatic_labels = dict(list(pattern["recurrent_names"].items())[:6])
    return protein_map(
        path,
        length=int(uni.get("length") or 0),
        features=uni.get("features") or [],
        missense=missense,
        truncating=truncating,
        uniprot_disease=[v["position"] for v in uni.get("variants") or [] if v.get("diseases")],
        lof=lof_pos,
        plddt=_data(run_dir, "alphafold").get("plddt") or [],
        pdb=pdb_ranges(uni.get("pdb") or []),
        labels=labels,
        effects=effects,
        somatic=somatic,
        somatic_labels=somatic_labels,
    )


def _expression(run_dir: Path, gene: str, path: Path) -> Path | None:
    from profile_figures import expression

    gtex = _data(run_dir, "gtex").get("gtex_expressions") or []
    tissues = sorted(
        (
            (t["tissue"].replace("_", " "), float(t["median_tpm"]))
            for t in gtex
            if t.get("median_tpm") is not None
        ),
        key=lambda x: -x[1],
    )[:12]
    cells: list[tuple[str, float]] = []
    for name, value in (
        _data(run_dir, "hpa").get("RNA single cell type specific nCPM") or {}
    ).items():
        try:
            cells.append((name, float(value)))
        except (TypeError, ValueError):
            continue
    cells = sorted(cells, key=lambda x: -x[1])[:12]
    hpa = _data(run_dir, "hpa")
    blood: list[tuple[str, float]] = []
    for field_name in ("RNA blood lineage specific nTPM", "RNA blood cell specific nTPM"):
        for name, value in (hpa.get(field_name) or {}).items():
            try:
                blood.append((name, float(value)))
            except (TypeError, ValueError):
                continue
    blood = sorted(dict(blood).items(), key=lambda x: -x[1])[:12]
    return expression(path, tissues=tissues, cell_types=cells, blood=blood)


def _phenome(run_dir: Path, gene: str, path: Path) -> Path | None:
    from profile_figures import phenome
    from profile_variants import phenome_points

    return phenome(
        path,
        phenome_points(_data(run_dir, "gwas").get("hits") or [], _data(run_dir, "gwas_support")),
    )


def _allelic_spectrum(run_dir: Path, gene: str, path: Path) -> Path | None:
    import math

    from profile_figures import allelic_spectrum
    from profile_variants import lead_association

    points = []
    for r in variant_records(run_dir):
        for allele in r["alleles"]:
            if not allele.get("found") or not allele.get("af"):
                continue
            mine = [a for a in r["associations"] if a["harmonised"] != "unknown"]
            label = r["change"] or r["rsid"]
            lead = lead_association(mine)
            if lead:
                size = (
                    abs(math.log(lead["odds_ratio"])) if lead.get("odds_ratio")
                    else abs(lead["beta"]) if lead.get("beta") is not None else None
                )  # fmt: skip
                points.append({"af": allele["af"], "effect": size, "direction": lead["direction"],
                               "function": r["function"], "label": label})  # fmt: skip
            elif r["disease_variant"]:
                points.append({"af": allele["af"], "effect": None, "direction": None,
                               "function": r["function"], "label": label})  # fmt: skip
    return allelic_spectrum(path, points)


# Text a figure block renders before its figure (a tally the figure only partly draws).
_FIGURE_TEXT: dict[str, Callable[[Context], str]] = {}

_FIGURES: dict[str, tuple[Callable[[Path, str, Path], Path | None], str]] = {
    "figure:phenome": (
        _phenome,
        "{gene} phenome: GWAS Catalog associations (top 100 by p) grouped by EFO therapeutic "
        "area, measurements by their EFO parent; filled when Open Targets ties the association "
        "to {gene} (L2G ≥ 0.5 or colocalisation H4 ≥ 0.8 with its QTL), hollow when mapped by "
        "position only; triangles give the direction for the reported effect allele.",
    ),
    "figure:allelic_spectrum": (
        _allelic_spectrum,
        "{gene} allelic spectrum: gnomAD allele frequency against the strongest GWAS effect per "
        "alt allele (|β| or |ln OR|, in each study's units, so heights compare within a trait "
        "only); disease variants without an estimate in the band above. Colour = what the "
        "variant does to the protein (grey: not typed); the variants are in the variant-effects "
        "table.",
    ),
    "figure:protein_map": (
        _protein_map,
        "{gene} protein map: UniProt features; ClinVar pathogenic / likely pathogenic "
        "variants (≥1★, not conflicting) and UniProt disease variants, shaped by source and "
        "coloured by what the series types them to do to the protein (grey: not typed); gnomAD "
        "high-confidence pLoF positions; TCGA PanCancer Atlas somatic mutations per residue "
        "(recurrent residues labelled), when the run has them; AlphaFold per-residue pLDDT and "
        "PDB coverage. The variants are listed in the allelic-series, spectrum and somatic tables.",
    ),
    "figure:expression": (
        _expression,
        "{gene} expression: GTEx median TPM (top tissues), Human Protein Atlas "
        "single-cell-type nCPM (enriched cell types) and, when HPA singles them out, blood "
        "lineages and immune cells (nTPM).",
    ),
}


# ── tables ──────────────────────────────────────────────────────────────────────────


@dataclass
class Context:
    """What a table builder reads: the run, the citable keys, the disease, and the writer's
    files loaded once."""

    run_dir: Path
    gene: str
    available: set[str]
    disease: str = ""
    cache: dict[str, Any] = field(default_factory=dict)

    def data(self, key: str) -> dict[str, Any]:
        return _data(self.run_dir, key)

    def strengths(self) -> dict[str, dict[str, Any]]:
        if "strengths" not in self.cache:
            self.cache["strengths"] = paper_strengths(self.run_dir)
        return self.cache["strengths"]


def paper_strengths(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Design, species, last author and stated n of every paper the run holds metadata
    for (profile_strength.paper_strength), keyed by PMID."""
    from profile_strength import paper_strength

    papers = _data(run_dir, "pubmed_abstracts").get("papers") or {}
    mapped = _data(run_dir, "pubtator_map").get("papers") or {}
    return {p: paper_strength(p, v, mapped.get(p)) for p, v in papers.items()}


def _how_we_know(c: Context) -> str | tuple[str, list[str]]:
    from profile_writer import HOW_FILE, how_we_know_md, load_writer_file

    entries = load_writer_file(c.run_dir, HOW_FILE)
    return how_we_know_md(entries if isinstance(entries, list) else [], c.strengths(), c.available)


def _allelic_series(c: Context) -> str:
    from profile_tables import allelic_series_md, functions_by_variant

    functions = functions_by_variant(load_functions(c.run_dir))
    return allelic_series_md(c.data("allelic_series").get("series") or {}, c.available, functions)


def _variant_spectrum(c: Context) -> str:
    from profile_tables import variant_spectrum_md

    return variant_spectrum_md(c.data("allelic_series").get("spectrum") or {}, c.available)


def _structures(c: Context) -> str:
    from profile_tables import structures_md

    return structures_md(c.data("uniprot_extra"), c.data("alphafold"), c.available)


def _tissue_evidence(c: Context) -> str:
    from profile_tables import tissue_evidence_md

    return tissue_evidence_md(c.data("hpa"), c.data("gtex"), c.available)


def _essentiality(c: Context) -> str:
    from profile_tables import essentiality_md

    return essentiality_md(c.data("depmap"), c.data("project_score"), c.available)


def _constraint(c: Context) -> str:
    from profile_tables import constraint_md

    return constraint_md(c.data("gnomad_constraint"), c.available)


def _faers(c: Context) -> str:
    from profile_tables import faers_md

    rec = load_raw(c.run_dir, "faers")
    if rec is None or rec.status != "ok" or not isinstance(rec.data, dict):
        return ""
    return faers_md(rec.data, rec.retrieved_at or "", c.available)


def _indication(c: Context) -> str:
    from profile_tables import indication_md

    return indication_md(_indication_records(c.run_dir), c.disease, c.available)


def _coding(c: Context) -> bool:
    from profile_biology import protein_coding

    return protein_coding(c.data("hgnc"))


def _models(c: Context) -> str:
    from profile_biology import models_md

    rec = load_raw(c.run_dir, "orthologs")
    orth = rec.data if rec and rec.status in ("ok", "empty") and isinstance(rec.data, dict) else {}
    return models_md(
        orth,
        c.data("ot_mouse"),
        c.data("impc"),
        c.data("hpo"),
        c.data("gnomad_lof"),
        c.available,
        c.data("genebass"),
    )


def _partners(c: Context) -> str:
    from profile_biology import partners_md

    return partners_md(c.data("string"), c.available)


def _reagents(c: Context) -> str:
    from profile_biology import reagents_md

    rec = load_raw(c.run_dir, "orthologs")
    orth = rec.data if rec and isinstance(rec.data, dict) else {}
    return reagents_md(
        c.data("hpa"), c.data("ot_mouse"), orth, c.data("chembl"), c.available, _coding(c)
    )


def _immune(c: Context) -> str:
    from profile_biology import immune_md

    return immune_md(c.data("hpa"), c.data("hpo_terms"), c.data("ot_mouse"), c.available)


def _somatic(c: Context) -> str:
    from profile_somatic import somatic_md

    return somatic_md(
        c.data("somatic"), int(c.data("uniprot_extra").get("length") or 0), c.available
    )


def _variant_effects(c: Context) -> str:
    from profile_variants import variant_effects_md

    return variant_effects_md(variant_records(c.run_dir), c.available)


_TABLES: dict[str, Callable[[Context], str | tuple[str, list[str]]]] = {
    "table:variant_effects": _variant_effects,
    "table:somatic": _somatic,
    "table:models": _models,
    "table:partners": _partners,
    "table:reagents": _reagents,
    "table:immune": _immune,
    "table:how_we_know": _how_we_know,
    "table:allelic_series": _allelic_series,
    "table:variant_spectrum": _variant_spectrum,
    "table:structures": _structures,
    "table:tissue_evidence": _tissue_evidence,
    "table:essentiality": _essentiality,
    "table:constraint": _constraint,
    "table:faers": _faers,
    "table:indication": _indication,
}


# ── expansion ───────────────────────────────────────────────────────────────────────


def expand(
    draft: str,
    run_dir: Path,
    gene: str,
    available: set[str],
    *,
    figures: bool = True,
    disease: str = "",
) -> tuple[str, list[str]]:
    """The draft with every block line replaced by its markdown, and notes on blocks that
    rendered their empty state or no figure (figures off, matplotlib missing)."""
    notes: list[str] = []
    out: list[str] = []
    figure_no = 0
    ctx = Context(run_dir, gene, available, disease)
    for line in draft.splitlines():
        m = BLOCK_RE.match(line)
        if not m or m.group(1) not in BLOCKS:
            out.append(line)
            continue
        name = m.group(1)
        spec = BLOCKS[name]
        keys = _found(run_dir, _keys(run_dir, spec))
        if name in PROTEIN_BLOCKS and not _coding(ctx):
            from profile_biology import not_protein_line

            notes.append(f"{name}: not a protein-coding gene")
            out.append(not_protein_line(ctx.data("hgnc"), _cite(["hgnc"], available)))
            continue
        lead = _FIGURE_TEXT[name](ctx) if name in _FIGURE_TEXT else ""
        if name in _FIGURES and lead:
            out.append(lead + "\n")
        if name in _FIGURES:
            if not figures:
                notes.append(f"{name}: figures off")
                out.append("")
                continue
            draw, caption = _FIGURES[name]
            path = run_dir / FIGURE_DIR / f"{name.split(':', 1)[1]}.svg"
            try:
                drawn = draw(run_dir, gene, path)
            except ImportError as exc:
                notes.append(f"{name}: not drawn ({exc})")
                out.append("")
                continue
            if drawn is None:
                if lead:  # the tally stands alone: no outcome has papers enough for a plot
                    out.append(f"*{provenance(spec.source, keys)}*")
                    continue
                notes.append(f"{name}: empty state")
                out.append(empty_state(run_dir, name, available))
                continue
            figure_no += 1
            rel = drawn.relative_to(run_dir).as_posix()
            out.append(
                f"![Figure {figure_no}]({rel})\n\n"
                f"*Figure {figure_no}.* {caption.format(gene=gene)} "
                f"{provenance(spec.source, keys)} {_cite(keys, available)}".rstrip()
            )
            continue
        built = _TABLES[name](ctx)
        text, keys = built if isinstance(built, tuple) else (built, keys)
        if not text:
            notes.append(f"{name}: empty state")
            out.append(empty_state(run_dir, name, available))
            continue
        out.append(f"{text}\n\n*{provenance(spec.source, keys)}*")
    return "\n".join(out), notes
