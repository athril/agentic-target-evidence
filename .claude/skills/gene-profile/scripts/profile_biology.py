# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Biology records read for a biologist: does a mouse model the human gene.

Deterministic, no LLM. Every reading comes from the records and states its source.

Orthology: NCBI's curated ortholog set decides whether a mouse ortholog exists (it covers
non-coding genes and leaves out distant relatives); Ensembl adds the homology type and the
percentage identity when there is one. An Ensembl match NCBI does not curate is a distant
homologue, never "the mouse ortholog", and its knockout phenotypes are not this gene's.
When neither finds one, the gene is "not found" in either, not "absent from mouse".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MOUSE = "Mus musculus"
MOUSE_TAXON = 10090


def _best(homologues: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    return (
        dict(max(homologues, key=lambda h: h.get("identity_target") or 0)) if homologues else None
    )


def ortholog_reading(
    ncbi: Sequence[Mapping[str, Any]], ensembl: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The mouse counterpart of the gene from NCBI's curated orthologs (``ncbi``: gene_id,
    symbol, type) and Ensembl's homologies (``ensembl``: id, type, identity_target,
    identity_source).

    ``reading`` is one of ``ortholog`` (NCBI curates one; Ensembl's type and identity added
    when it has a match), ``distant_homologues`` (Ensembl only) or ``not_found``."""
    best = _best(ensembl)
    if ncbi:
        o = ncbi[0]
        return {
            "reading": "ortholog",
            "ortholog": {
                "symbol": o.get("symbol", ""),
                "ncbi_gene_id": str(o.get("gene_id") or ""),
                "gene_type": o.get("type", ""),
                "ensembl_id": best.get("id", "") if best else "",
                "ensembl_type": best.get("type", "") if best else "",
                "identity_target": best.get("identity_target") if best else None,
                "identity_source": best.get("identity_source") if best else None,
            },
            "distant_homologues": [],
        }
    if ensembl:
        return {
            "reading": "distant_homologues",
            "ortholog": None,
            "distant_homologues": sorted(
                (dict(h) for h in ensembl), key=lambda h: -(h.get("identity_target") or 0)
            ),
        }
    return {"reading": "not_found", "ortholog": None, "distant_homologues": []}


def _pct(value: Any) -> str:
    return f"{float(value):.0f}%" if isinstance(value, int | float) else "not available"


def ortholog_summary(data: Mapping[str, Any]) -> str:
    """One sentence a reader can take at face value, with the releases queried."""
    ncbi_rel = (data.get("ncbi") or {}).get("release") or "?"
    ens_rel = (data.get("ensembl") or {}).get("release") or "?"
    releases = f"NCBI Datasets {ncbi_rel}, Ensembl {ens_rel}"
    reading = data.get("reading")
    if reading == "ortholog":
        o = data.get("ortholog") or {}
        kind = (o.get("ensembl_type") or "").replace("ortholog_", "").replace("2", "-to-")
        if o.get("ensembl_type"):
            identity = f"{_pct(o.get('identity_target'))} identity (Ensembl {kind})"
        elif (data.get("ensembl") or {}).get("error"):
            identity = "identity not checked (the Ensembl lookup failed)"
        else:
            identity = "identity not available (no Ensembl match)"
        return f"Mouse ortholog {o.get('symbol') or '?'} (NCBI curated); {identity}. {releases}."
    if reading == "distant_homologues":
        hs = data.get("distant_homologues") or []
        kind = (hs[0].get("type") or "").replace("ortholog_", "").replace("2", "-to-")
        return (
            f"No mouse ortholog in NCBI's curated set; Ensembl lists {len(hs)} distant "
            f"homologue(s) ({kind}, best identity {_pct(hs[0].get('identity_target'))}), "
            f"whose phenotypes are not this gene's. {releases}."
        )
    if (data.get("ensembl") or {}).get("error"):
        return (
            f"No mouse ortholog in NCBI's curated set ({releases}); Ensembl not checked "
            "(the lookup failed)."
        )
    return f"No mouse ortholog found in NCBI or Ensembl ({releases}): not found, not shown absent."


# ── immune block trigger ──────────────────────────────────────────────────────

HPO_IMMUNE = "HP:0002715"  # Abnormality of the immune system
MP_IMMUNE = "MP:0005387"  # immune system phenotype
# HPA blood-cell specificity categories that single out immune cells.
_HPA_IMMUNE = ("immune cell enriched", "immune cell enhanced", "group enriched")


def immune_trigger(
    hpa: Mapping[str, Any], hpo_terms: Mapping[str, Any], ot_mouse: Mapping[str, Any]
) -> list[str]:
    """Why the profile carries the immune block, from the data: HPA calls the gene's blood
    expression immune-cell enriched, enhanced or group enriched; an HPO term of its
    Mendelian phenotypes descends from Abnormality of the immune system; or a mouse
    phenotype falls under the MP immune system class. [] when none holds (no gene list)."""
    reasons = []
    blood = str(hpa.get("RNA blood cell specificity") or "")
    if blood.lower() in _HPA_IMMUNE:
        reasons.append(f"HPA blood cells: {blood}")
    immune = [t for t in hpo_terms.get("terms") or [] if t.get("immune")]
    if immune:
        names = ", ".join(t.get("label", t.get("id", "")) for t in immune[:3])
        reasons.append(f"{len(immune)} HPO phenotype(s) under {HPO_IMMUNE}: {names}")
    mouse = [
        p
        for p in ot_mouse.get("phenotypes") or []
        if any(c.get("id") == MP_IMMUNE for c in p.get("phenotype_classes") or [])
    ]
    if mouse:
        reasons.append(f"{len(mouse)} mouse phenotype(s) under {MP_IMMUNE} (immune system)")
    return reasons


# ── non-coding genes ────────────────────────────────────────────────────────────────


def protein_coding(hgnc: Mapping[str, Any]) -> bool:
    """HGNC's locus group decides the layout; a record without one is taken as coding."""
    group = str(hgnc.get("locus_group") or "protein-coding gene")
    return group == "protein-coding gene"


def not_protein_line(hgnc: Mapping[str, Any], cite: str) -> str:
    kind = hgnc.get("locus_type") or hgnc.get("locus_group") or "non-coding"
    return f"*Not a protein-coding gene (HGNC locus type: {kind}).* {cite}".rstrip()


# ── tables ──────────────────────────────────────────────────────────────────────────


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "/").replace("\n", " ").strip()


def _cite(keys: Sequence[str], available: set[str]) -> str:
    keep = [k for k in dict.fromkeys(keys) if k in available]
    return "[" + "; ".join(f"@{k}" for k in keep) + "]" if keep else ""


MAX_CLASSES = 8


def phenotype_classes(ot_mouse: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Mouse phenotypes grouped by their MP top-level class, most phenotypes first; the
    "normal phenotype" class (no abnormality detected) is kept as its own row."""
    groups: dict[str, dict[str, Any]] = {}
    for p in ot_mouse.get("phenotypes") or []:
        for c in p.get("phenotype_classes") or [{"id": "", "label": "unclassified"}]:
            g = groups.setdefault(c.get("id") or c.get("label", ""), {
                "id": c.get("id", ""), "label": c.get("label", ""), "phenotypes": [], "alleles": set(),
            })  # fmt: skip
            g["phenotypes"].append(p.get("phenotype_label", ""))
            g["alleles"].update(p.get("allelic_compositions") or [])
    return sorted(groups.values(), key=lambda g: (-len(g["phenotypes"]), g["label"]))


def models_md(
    orthologs: Mapping[str, Any],
    ot_mouse: Mapping[str, Any],
    impc: Mapping[str, Any],
    hpo: Mapping[str, Any],
    lof: Mapping[str, Any],
    available: set[str],
    burden: Mapping[str, Any] | None = None,
) -> str:
    """Does the mouse model the human: the ortholog (NCBI curated, Ensembl type and
    identity), knockout phenotypes by MP top-level class — shown as this gene's only when a
    curated ortholog exists — and, beside them, what human loss of function shows."""
    if not orthologs:
        return ""
    o_ref = _cite(["orthologs"], available)
    rows: list[tuple[str, str, str]] = [("Mouse ortholog", orthologs.get("summary", ""), o_ref)]
    reading = orthologs.get("reading")
    m_ref = _cite(["ot_mouse"], available)
    if reading == "ortholog":
        classes = phenotype_classes(ot_mouse)
        for g in classes[:MAX_CLASSES]:
            names = list(dict.fromkeys(g["phenotypes"]))
            shown = "; ".join(names[:3]) + (f"; … {len(names) - 3} more" if len(names) > 3 else "")
            rows.append((f"Knockout: {g['label']}", f"{len(names)} phenotype(s): {shown}", m_ref))
        if len(classes) > MAX_CLASSES:
            rows.append(
                ("Knockout: other classes", f"{len(classes) - MAX_CLASSES} more MP classes", m_ref)
            )
        if not classes:
            rows.append(
                ("Knockout phenotypes", "No knockout phenotyped in MGI/IMPC (Open Targets)", m_ref)
            )
        if impc.get("viability") and impc.get("viability") != "unknown":
            rows.append(("IMPC viability", str(impc["viability"]), _cite(["impc"], available)))
    elif reading == "distant_homologues":
        rows.append((
            "Knockout phenotypes",
            "Not shown: the mouse genes are distant homologues, not orthologs, and their "
            "phenotypes are not this gene's",
            m_ref,
        ))  # fmt: skip
    else:
        rows.append(
            ("Knockout phenotypes", "No mouse ortholog found, so no knockout to read", o_ref)
        )
    human = []
    if hpo.get("phenotype_count"):
        inh = ", ".join(hpo.get("inheritance_modes") or []) or "inheritance not recorded"
        human.append(
            (
                "Human: Mendelian phenotypes (HPO)",
                f"{hpo['phenotype_count']} phenotypes ({inh}); e.g. "
                + ", ".join((hpo.get("top_phenotypes") or [])[:4]),
                _cite(["hpo"], available),
            )
        )
    reported = lof.get("reported_variants") or []
    hom = sum(int(v.get("homozygote_count") or 0) for v in reported)
    if lof:
        # A sum of genotype counts over the reported variants, not a count of people.
        human.append(
            (
                "Human: homozygous pLoF genotypes (gnomAD)",
                f"{hom} genotype counts summed over {len(reported)} reported pLoF variants "
                "(not individuals); phenotypes not recorded",
                _cite(["gnomad_lof"], available),
            )
        )
    human.append(
        (
            "Human: loss-of-function burden",
            burden_line(burden or {}),
            _cite(["genebass"], available) if burden else "",
        )
    )
    out = ["| Evidence | Reading | Refs |", "|---|---|---|"]
    out += [f"| {a} | {_cell(b)} | {c} |" for a, b, c in [*rows, *human]]
    return "\n".join(out)


# STRING's own medium-confidence cut-off on a channel score (STRING help, "scores").
STRING_CHANNEL_MIN = 0.4


def partners_md(string: Mapping[str, Any], available: set[str]) -> str:
    """STRING partners with their channels apart: experiments and curated databases (which
    can record binding or a shared complex or pathway) versus text mining and the rest
    (co-mention, co-expression). The combined score is a functional association, never
    evidence of binding by itself."""
    partners = list(string.get("partners") or [])
    if not partners:
        return ""
    ref = _cite(["string"], available)
    out = [
        "| Partner | Combined | Experiments | Curated databases | Text mining | Reading | Refs |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in partners:
        exp, db, tm = (float(p.get(k) or 0) for k in ("experimental", "database", "textmining"))
        if exp >= STRING_CHANNEL_MIN and db >= STRING_CHANNEL_MIN:
            reading = "experiments and curated databases"
        elif exp >= STRING_CHANNEL_MIN:
            reading = "experiments"
        elif db >= STRING_CHANNEL_MIN:
            reading = "curated databases"
        else:
            reading = "associated only (text mining, co-expression)"
        out.append(
            f"| {_cell(p.get('partner'))} | {float(p.get('combined_score') or 0):.2f} | {exp:.2f} "
            f"| {db:.2f} | {tm:.2f} | {reading} | {ref} |"
        )
    out += [
        "",
        f"Partners with combined score ≥ {string.get('min_combined_score', 0.7)}; a channel counts "
        f"at ≥ {STRING_CHANNEL_MIN} (STRING's medium confidence). Experiments and curated "
        "databases can record binding, a complex or a shared pathway; text mining records "
        f'co-mention. Say "binds" only with the paper or structure that shows it {ref}.',
    ]
    return "\n".join(out)


def _antibodies(hpa: Mapping[str, Any]) -> list[str]:
    return [str(a) for a in hpa.get("Antibody") or [] if a]


def reagents_md(
    hpa: Mapping[str, Any],
    ot_mouse: Mapping[str, Any],
    orthologs: Mapping[str, Any],
    chembl: Mapping[str, Any],
    available: set[str],
    coding: bool = True,
) -> str:
    """What a researcher can get: antibodies (HPA, with its reliability calls), cell lines
    expressing the gene (HPA cell-line RNA), mouse alleles (MGI/IMPC via Open Targets, when a
    curated ortholog exists) and tool compounds (ChEMBL potency counts). Each row states its
    empty state."""
    h_ref, c_ref = _cite(["hpa"], available), _cite(["chembl"], available)
    rows: list[tuple[str, str, str]] = []
    if coding:
        abs_ = _antibodies(hpa)
        rel = "; ".join(
            f"{k.removeprefix('Reliability ')} {hpa[k]}"
            for k in ("Reliability (IH)", "Reliability (IF)")
            if hpa.get(k)
        )
        rrids = [v for v in (hpa.get("Antibody RRID") or {}).values() if v]
        rows.append((
            "Antibodies (HPA)",
            (f"{len(abs_)} validated by HPA: {', '.join(abs_[:6])}" + (f"; reliability {rel}" if rel else "")
             + (f"; RRIDs {', '.join(rrids[:4])}" if rrids else ""))
            if abs_ else "No HPA antibody listed",
            h_ref,
        ))  # fmt: skip
    lines = hpa.get("RNA cell line specific nTPM") or {}
    spec = hpa.get("RNA cell line specificity")
    if lines or spec:
        top = ", ".join(f"{k} {v}" for k, v in list(lines.items())[:5])
        rows.append(
            (
                "Cell lines expressing it (HPA RNA)",
                "; ".join(x for x in (str(spec or ""), f"{top} nTPM" if top else "") if x),
                h_ref,
            )
        )
    else:
        rows.append(("Cell lines expressing it (HPA RNA)", "No HPA cell-line RNA record", h_ref))
    if orthologs.get("reading") == "ortholog":
        alleles = sorted(
            {
                a
                for p in ot_mouse.get("phenotypes") or []
                for a in p.get("allelic_compositions") or []
            }
        )
        rows.append(
            (
                "Mouse alleles (MGI/IMPC)",
                f"{len(alleles)} phenotyped allele compositions, e.g. {'; '.join(alleles[:3])}"
                if alleles
                else "No phenotyped mouse allele",
                _cite(["ot_mouse"], available),
            )
        )
    if coding:
        if chembl.get("num_bioactivities"):
            rows.append((
                "Tool compounds (ChEMBL)",
                f"{chembl.get('num_potent', 0)} measurements ≤ 100 nM and "
                f"{chembl.get('num_highly_potent', 0)} ≤ 10 nM of {chembl.get('num_quantitative', 0)} "
                f"quantitative; {chembl.get('num_mechanisms', 0)} annotated drug mechanism(s)",
                c_ref,
            ))  # fmt: skip
        else:
            rows.append(("Tool compounds (ChEMBL)", "No ChEMBL bioactivity for the target", c_ref))
    out = ["| Reagent | Available | Refs |", "|---|---|---|"]
    out += [f"| {a} | {_cell(b)} | {c} |" for a, b, c in rows]
    return "\n".join(out)


def immune_md(
    hpa: Mapping[str, Any],
    hpo_terms: Mapping[str, Any],
    ot_mouse: Mapping[str, Any],
    available: set[str],
) -> str:
    """The immune block: shown when the data trigger it (immune_trigger), else one line
    naming what was checked. Blood-lineage RNA (HPA), the immune HPO terms and the mouse
    immune-system phenotypes, each with its source."""
    reasons = immune_trigger(hpa, hpo_terms, ot_mouse)
    refs = _cite(["hpa", "hpo_terms", "ot_mouse"], available)
    if not reasons:
        blood = hpa.get("RNA blood cell specificity") or "no blood-cell record"
        return (
            f'*No immune trigger: HPA blood cells "{blood}"; no HPO phenotype under '
            f"{HPO_IMMUNE}; no mouse phenotype under {MP_IMMUNE} (immune system).* {refs}"
        ).rstrip()
    rows: list[tuple[str, str, str]] = [("Why shown", "; ".join(reasons), refs)]
    lineage = hpa.get("RNA blood lineage specific nTPM") or {}
    cells = hpa.get("RNA blood cell specific nTPM") or {}
    if lineage or cells:
        text = "; ".join(
            x
            for x in (
                ", ".join(f"{k} {v}" for k, v in lineage.items()),
                ", ".join(f"{k} {v}" for k, v in cells.items()),
            )
            if x
        )
        rows.append(("Blood lineages and cells (HPA RNA, nTPM)", text, _cite(["hpa"], available)))
    immune = [t for t in hpo_terms.get("terms") or [] if t.get("immune")]
    if immune:
        rows.append((
            "Human immune phenotypes (HPO)",
            f"{len(immune)} of {hpo_terms.get('count', len(immune))} terms: "
            + ", ".join(t["label"] for t in immune[:6]),
            _cite(["hpo_terms"], available),
        ))  # fmt: skip
    mouse = [
        p.get("phenotype_label", "")
        for p in ot_mouse.get("phenotypes") or []
        if any(c.get("id") == MP_IMMUNE for c in p.get("phenotype_classes") or [])
    ]
    if mouse:
        rows.append((
            "Mouse immune-system phenotypes (MGI/IMPC)",
            f"{len(mouse)}: " + "; ".join(list(dict.fromkeys(mouse))[:5]),
            _cite(["ot_mouse"], available),
        ))  # fmt: skip
    out = ["| Immune evidence | Reading | Refs |", "|---|---|---|"]
    out += [f"| {a} | {_cell(b)} | {c} |" for a, b, c in rows]
    return "\n".join(out)


# ── human loss-of-function burden ───────────────────────────────────────────────────

# Genebass's exome-wide thresholds and QC (Karczewski et al. 2022, PMID 36778668): about 0.05
# expected false positives per phenotype; group tests need ≥ 2 variants, ≥ 20× coverage and
# an expected allele count (CAF × cases) of at least 50.
P_SKATO = 2.5e-7
P_BURDEN = 6.7e-7
MIN_VARIANTS = 2
MIN_COVERAGE = 20.0
MIN_EXPECTED_AC = 50.0
MAX_BURDEN_ROWS = 12


def burden_hits(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Rows past the paper's thresholds that pass its QC, strongest first; each says
    whether QC could be applied (the gene's CAF and coverage were available)."""
    out = []
    for m in data.get("masks") or []:
        caf, coverage = m.get("caf"), m.get("mean_coverage")
        for r in m.get("rows") or []:
            skato, burden = r.get("p_skato"), r.get("p_burden")
            passes = (skato is not None and skato < P_SKATO) or (
                burden is not None and burden < P_BURDEN
            )
            if not passes or (r.get("total_variants") or 0) < MIN_VARIANTS:
                continue
            if coverage is not None and coverage < MIN_COVERAGE:
                continue
            if caf is not None and caf * (r.get("n_cases") or 0) < MIN_EXPECTED_AC:
                continue
            out.append({**r, "mask": m.get("mask"), "qc_applied": caf is not None})
    return sorted(out, key=lambda r: min(r.get("p_skato") or 1.0, r.get("p_burden") or 1.0))


def _carriers(row: Mapping[str, Any]) -> str:
    beta = row.get("beta_burden")
    if not isinstance(beta, int | float) or beta == 0:
        return "not recorded"
    return "lower" if beta < 0 else "higher"


def _phenotype_name(row: Mapping[str, Any]) -> str:
    return ": ".join(str(x) for x in (row.get("phenotype"), row.get("detail")) if x)


def burden_line(data: Mapping[str, Any]) -> str:
    """One line for the models table."""
    masks = data.get("masks") or []
    if not masks:
        return "not checked (no Genebass record in this run)"
    plof = next((m for m in masks if m.get("mask") == "pLoF"), masks[0])
    hits = [h for h in burden_hits(data) if h["mask"] == plof.get("mask")]
    tested = plof.get("phenotypes_tested", 0)
    if not hits:
        return f"No pLoF burden association among {tested:,} phenotypes (Genebass); may be underpowered"
    names = "; ".join(f"{_phenotype_name(h)} ({_carriers(h)})" for h in hits[:3])
    return f"{len(hits)} of {tested:,} phenotypes in pLoF carriers (Genebass): {names}"


def burden_md(data: Mapping[str, Any], available: set[str]) -> str:
    """Phenotypes of human carriers: the associations past Genebass's thresholds, per mask,
    with what the mask's carrier frequency allows one to say."""
    masks = data.get("masks") or []
    if not masks:
        return ""
    ref = _cite(["genebass", "pmid:36778668"], available)
    hits = burden_hits(data)
    lines = []
    if hits:
        lines = [
            "| Phenotype | Mask | SKAT-O p | Burden p | Trait in carriers | Variants | Cases | Refs |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for h in hits[:MAX_BURDEN_ROWS]:
            lines.append(
                f"| {_cell(_phenotype_name(h))} | {_cell(h['mask'])} | {h.get('p_skato') or 0:.2g} "
                f"| {h.get('p_burden') or 0:.2g} | {_carriers(h)} | {h.get('total_variants', '—')} "
                f"| {(h.get('n_cases') or 0):,} | {ref} |"
            )
        lines.append("")
    scope = "; ".join(
        f"{m.get('mask')}: {m.get('phenotypes_tested', 0):,} phenotypes, CAF "
        + (f"{m['caf']:.2g}" if m.get("caf") is not None else "not recorded")
        for m in masks
    )
    unfiltered = sum(1 for h in hits if not h["qc_applied"])
    shown = min(len(hits), MAX_BURDEN_ROWS)
    lines.append(
        (
            f"{len(hits)} association(s) past the thresholds (top {shown} shown) in {scope}. "
            if hits
            else f"No association past the thresholds in {scope}. "
        )
        + (f"{unfiltered} without QC (the gene has no QC record). " if unfiltered else "")
        + f"Thresholds and QC as published: SKAT-O p < {P_SKATO:.1e}, burden p < {P_BURDEN:.1e}, "
        f"≥ {MIN_VARIANTS} variants, ≥ {MIN_COVERAGE:.0f}× coverage, expected allele count ≥ "
        f"{MIN_EXPECTED_AC:.0f}. Carriers are almost all heterozygous, so this does not show the "
        "effect of complete loss; no association at a low CAF is a lack of power, not evidence of "
        f"tolerance. UK Biobank Resource, projects 26041 and 48511; Genebass results CC BY 4.0 {ref}."
    )
    return "\n".join(lines)


# ── CRISPR screens beyond fitness ───────────────────────────────────────────────────

MAX_SCREEN_ROWS = 10


def crispr_md(data: Mapping[str, Any], available: set[str]) -> str:
    """The published CRISPR screens that scored the gene, by phenotype and setup, the ones
    that called it a hit first: what perturbing it does beyond cancer-cell fitness. Each
    screen's HIT is its own authors' call."""
    screens = data.get("screens") or []
    if not screens:
        return ""
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for s in screens:
        groups.setdefault(
            (s.get("phenotype") or "unspecified", s.get("experimental_setup") or ""), []
        ).append(s)

    def hits(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        return [r for r in rows if r.get("hit") == "YES"]

    ranked = sorted(groups.items(), key=lambda kv: (-len(hits(kv[1])), -len(kv[1]), kv[0]))
    out = [
        "| Screen phenotype | Setup | Hit in / screens | Conditions and cell lines of the hits | Refs |",
        "|---|---|---|---|---|",
    ]
    for (phenotype, setup), rows in ranked[:MAX_SCREEN_ROWS]:
        hit = hits(rows)
        where = "; ".join(
            dict.fromkeys(
                " · ".join(x for x in (r.get("condition"), r.get("cell_line")) if x) for r in hit
            )
        )
        where = "; ".join(where.split("; ")[:4]) or "—"
        papers = list(dict.fromkeys(f"pmid:{r['pmid']}" for r in hit if r.get("pmid")))
        refs = _cite(papers[:4], available) or _cite(["biogrid_orcs"], available)
        out.append(
            f"| {_cell(phenotype)} | {_cell(setup) or '—'} | {len(hit)} / {len(rows)} | {_cell(where)} | {refs} |"
        )
    total = len(hits(screens))
    out += [
        "",
        f"Hit in {total} of {len(screens)} human screens in BioGRID ORCS (top {min(len(ranked), MAX_SCREEN_ROWS)} of "
        f"{len(ranked)} phenotype groups). A hit is each screen's own call, with its own score "
        "and threshold, so counts are not effect sizes and are not comparable across screens; "
        "a screen that did not call it is not evidence of no role, and a pooled screen reads a "
        "cell-autonomous phenotype in the line it used " + _cite(["biogrid_orcs"], available) + ".",
    ]
    return "\n".join(out)
