# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""One FDA label, parsed once from its untruncated text.

Every reader of a label used to apply its own heuristic to its own copy cut at 2,000
characters, and no reader parsed warning headings. This module turns one openFDA label result
into the record the tables read: its boxed warning, its warning sections, its
contraindications and its mechanism of action.

Formats differ, so the parser degrades rather than guesses: warnings are split at numbered
headings (``5.1 Hepatotoxicity``) in PLR labels; in older labels, at the title-case headings
and ``Heading:`` lead-ins they use; one untitled section when neither is found.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

# ── models ──────────────────────────────────────────────────────────────────────────


class LabelSection(BaseModel):
    number: str = ""  # "5.1"; "" in labels without numbered headings
    title: str = ""
    text: str


class ParsedLabel(BaseModel):
    drug_name: str
    brand_names: list[str] = []
    product_type: str = ""
    application_number: str = ""
    set_id: str = ""
    effective_date: str = ""  # ISO, the label version's date
    format: str = ""  # "PLR" (numbered sections) or "non-PLR"
    boxed_warning: str = ""
    boxed_titles: list[str] = []
    warning_sections: list[LabelSection] = []
    contraindications: str = ""
    mechanism_of_action: str = ""
    source_link: str = ""


# ── text normalisation ──────────────────────────────────────────────────────────────

_DASHES = re.compile(r"[‐‑‒–−­]")
_MARKS = re.compile(r"[®™]")
# "HLA ‑ B*5701", "HLA -B*1502": a hyphen inside a gene name, spaced in the label's XML.
_SPACED_GENE_HYPHEN = re.compile(r"\b([A-Z]{2,})\s*-\s*([A-Z0-9]{1,3}\*)")
# "KRAS G12C -mutated", "BRCA -mutated": a hyphen spaced away from its marker.
_SPACED_SUFFIX = re.compile(r"(?<=[A-Za-z0-9])\s+-\s*(?=[a-z])")
# "RAS- Mutant", "G12C- mutated": a hyphen spaced away from the word it joins.
_SPACED_JOIN = re.compile(r"(?<=[A-Za-z0-9])-\s+(?=[A-Za-z])")
_REFS = re.compile(r"\(\s*\d+(?:\.\d+)*(?:\s*,\s*\d+(?:\.\d+)*)*\s*\)|\[\s*see\b[^\]]*\]", re.I)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """One line of label text: unicode dashes as hyphens, section cross-references dropped."""
    text = _MARKS.sub("", _DASHES.sub("-", text or ""))
    text = _SPACED_GENE_HYPHEN.sub(r"\1-\2", text)
    text = _SPACED_SUFFIX.sub("-", text)
    text = _SPACED_JOIN.sub("-", text)
    text = _REFS.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return re.sub(r"\s+([.,;:])", r"\1", text)


def _joined(value: Any) -> str:
    if not value:
        return ""
    return " ".join(str(v) for v in value) if isinstance(value, list) else str(value)


# ── headings ────────────────────────────────────────────────────────────────────────

# Words that join a heading's capitalised words ("Lack of Benefit in Patients with").
_CONNECTORS = {
    "and", "or", "of", "with", "in", "to", "the", "for", "on", "at", "by", "from", "during",
    "after", "including", "vs", "versus", "a", "an", "&", "/",
}  # fmt: skip
_MAX_TITLE_WORDS = 30


def _capitalised(word: str) -> bool:
    """A heading word: capitalised, a number, or an acronym written lower-first ("(mCRC)")."""
    stripped = word.lstrip("(\"'[")
    if not stripped:
        return False
    return stripped[0].isupper() or stripped[0].isdigit() or sum(map(str.isupper, stripped)) >= 2


def split_title(text: str) -> tuple[str, str]:
    """(heading, body) of a section that opens with a title-case heading.

    The body starts at the capitalised word that begins the first run of lowercase words:
    "Hepatotoxicity LUMAKRAS can cause …" → "Hepatotoxicity"; "Embryo-Fetal Toxicity Based on
    animal data …" → "Embryo-Fetal Toxicity". A heading that cannot be told from its text
    comes back empty.
    """
    words = text.split(" ")
    start = None
    for i, word in enumerate(words):
        if not word:
            continue
        if not _capitalised(word) and word.lower().strip(",") not in _CONNECTORS:
            start = i
            break
    if start is None:
        return "", text
    # Walk back to the capitalised word that opens the sentence; a number ("Study 20020408,
    # 4% of patients") does not open one.
    j = start - 1
    while j >= 0 and not (_capitalised(words[j]) and words[j].lstrip("(\"'[")[:1].isalpha()):
        j -= 1
    if j <= 0:
        return "", text
    head = words[:j]
    while head and head[-1].lower().strip(",") in _CONNECTORS:
        head.pop()
    if not head or len(head) > _MAX_TITLE_WORDS:
        return "", text
    return " ".join(head).strip(" ,:"), " ".join(words[j:]).strip()


def _numbered(text: str, section: int) -> list[tuple[str, str]]:
    """(number, text) of each numbered subsection ("5.1 …") of a section, in label order.
    A number counts only before a capitalised word and as one of the next few numbers, so
    "14.2 months" or "RECIST 1.1" in the text is not a heading; a table can end just before
    a heading, so no sentence boundary is required."""
    pattern = re.compile(rf"(?:^|(?<=\s))({section}\.(\d{{1,2}}))\s+(?=[A-Z])")
    found: list[tuple[int, int, str]] = []
    last = 0
    for m in pattern.finditer(text):
        n = int(m.group(2))
        if last < n <= last + _NUMBER_GAP:
            found.append((m.start(), m.end(), m.group(1)))
            last = n
    out = []
    for k, (_start, end, number) in enumerate(found):
        stop = found[k + 1][0] if k + 1 < len(found) else len(text)
        out.append((number, text[end:stop].strip()))
    return out


_NUMBER_GAP = 3  # a label may drop a subsection and keep the numbers of the rest
_WARNINGS_HEAD = re.compile(r"^\s*(?:5\s+)?WARNINGS(?:\s*AND\s*PRECAUTIONS)?\s*:?\s*", re.I)


def _sections(text: str) -> list[LabelSection]:
    """The numbered subsections of the warnings, or the whole section as one when it has
    none."""
    body = _WARNINGS_HEAD.sub("", text)
    if body and not _numbered(body, 5):
        return [LabelSection(text=body)]
    out = []
    for number, chunk in _numbered(body, 5):
        title, rest = split_title(chunk)
        out.append(LabelSection(number=number, title=title, text=rest))
    return out


# Old-format warnings: "Mortality: In the …", or a title-case heading after a sentence end.
_COLON_HEAD = re.compile(r"(?:^|(?<=[.]\s))([A-Z][\w/()-]*(?:\s+[A-Za-z][\w/()*-]*){0,6}):\s")
_SENTENCE_END = re.compile(r"(?<=[.])\s+(?=[A-Z])")
# Words that open a sentence, not a heading ("Many HLA-B*1502-positive patients …").
_OPENERS = {
    "the", "this", "these", "those", "many", "most", "some", "over", "across", "in", "prior",
    "if", "when", "although", "because", "however", "since", "all", "any", "each", "a", "an",
    "table", "figure", "for", "patients",
}  # fmt: skip
_AFTER_TABLE = re.compile(r"(?:Table|Figure)\s+\d+\.?\s*$")


def _old_warning_sections(text: str) -> list[LabelSection]:
    """Headed sections of a label without numbered warnings, or one untitled section."""
    body = _WARNINGS_HEAD.sub("", text)
    starts: list[tuple[int, str, int]] = []  # (heading start, title, body start)
    for m in _COLON_HEAD.finditer(body):
        starts.append((m.start(), m.group(1).strip(), m.end()))
    if not starts:
        cuts = [0, *(m.end() for m in _SENTENCE_END.finditer(body))]
        for cut in cuts:
            title, rest = split_title(body[cut:])
            first = rest.split(" ", 1)[0]
            # Without a colon a heading needs two words and no comma: "South Asians, including
            # Indians, appear …" opens a sentence, not a section.
            if (
                len(title.split()) >= 2
                and "," not in body[cut : len(body) - len(rest)]
                and rest[:1].isupper()
                and not first.endswith(",")
                and title.split()[0].lower() not in _OPENERS
                and not _AFTER_TABLE.search(body[:cut])
            ):
                starts.append((cut, title, len(body) - len(rest)))
    if not starts:
        return [LabelSection(text=body)] if body else []
    out = []
    for k, (_start, title, begin) in enumerate(starts):
        stop = starts[k + 1][0] if k + 1 < len(starts) else len(body)
        chunk = body[begin:stop].strip()
        out.append(LabelSection(title=title, text=chunk))
    return out


# Upper-case words, joined by lower-case "and"/"of" ("HYPERSENSITIVITY REACTIONS and
# EXACERBATIONS OF HEPATITIS B"), up to the first sentence-case word.
_BOXED_TITLE = re.compile(
    r"WARNINGS?\s*:\s*((?:(?:[A-Z0-9][A-Z0-9*,;:/()'-]*|and|or|of|with|in|for)\s+)+?)"
    r"(?=[A-Z][a-z])"
)


def boxed_titles(text: str) -> list[str]:
    """The upper-case titles of a PLR boxed warning ("WARNING: HYPERSENSITIVITY REACTIONS")."""
    titles = [m.group(1).strip(" ,;:") for m in _BOXED_TITLE.finditer(text)]
    return list(dict.fromkeys(titles))  # the Highlights repeat the box


# ── the label ───────────────────────────────────────────────────────────────────────

_SALT_WORDS = {
    "SODIUM", "POTASSIUM", "CALCIUM", "MAGNESIUM", "HYDROCHLORIDE", "HCL", "HYDROBROMIDE",
    "BROMIDE", "CHLORIDE", "MESYLATE", "TOSYLATE", "BESYLATE", "MALEATE", "FUMARATE",
    "SUCCINATE", "TARTRATE", "CITRATE", "SULFATE", "ACETATE", "PHOSPHATE", "ANHYDROUS",
    "MONOHYDRATE", "DIHYDRATE", "DIMETHYL", "SULFOXIDE", "GLUCONATE", "AND",
    # dosage forms some generic names carry ("QUINIDINE SULFATE TABLET")
    "TABLET", "TABLETS", "CAPSULE", "CAPSULES", "INJECTION", "SOLUTION", "ORAL",
}  # fmt: skip


def ingredients(name: str) -> frozenset[str]:
    """A drug name as its set of ingredient words, without salts, order or a biologic suffix:
    "ELEXACAFTOR, TEZACAFTOR, AND IVACAFTOR" is "ELEXACAFTOR, IVACAFTOR, TEZACAFTOR"."""
    words = re.findall(r"[A-Z0-9]+(?:-[A-Z0-9]+)*", str(name).upper())
    words = [re.sub(r"-[A-Z]{4}$", "", w) for w in words]
    return frozenset(w for w in words if w not in _SALT_WORDS)


def _iso(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}" if re.fullmatch(r"\d{8}", day or "") else ""


def parse_label(result: dict[str, Any], drug_name: str = "") -> ParsedLabel:
    """One openFDA drug/label result as a parsed record. ``drug_name`` names a label openFDA
    has not harmonised (no ``openfda.generic_name``)."""
    ot = result.get("openfda") or {}
    name = ((ot.get("generic_name") or [""])[0] or drug_name).upper()
    warnings_raw = _joined(result.get("warnings_and_cautions") or result.get("warnings"))
    # PLR labels have a numbered "Warnings and Precautions" section.
    plr = bool(result.get("warnings_and_cautions")) or bool(re.match(r"\s*5\s+WARN", warnings_raw))
    warnings = normalize(warnings_raw)
    sections = _sections(warnings) if plr else []
    if not any(s.number for s in sections) and warnings:
        sections = _old_warning_sections(warnings)
    boxed = normalize(_joined(result.get("boxed_warning")))
    set_id = str(result.get("set_id") or "")
    return ParsedLabel(
        drug_name=name,
        brand_names=[str(b).upper() for b in ot.get("brand_name") or []][:5],
        product_type=(ot.get("product_type") or [""])[0],
        application_number=(ot.get("application_number") or [""])[0],
        set_id=set_id,
        effective_date=_iso(str(result.get("effective_time") or "")),
        format="PLR" if plr else "non-PLR",
        boxed_warning=boxed,
        boxed_titles=boxed_titles(boxed),
        warning_sections=sections,
        contraindications=normalize(_joined(result.get("contraindications"))),
        mechanism_of_action=normalize(_joined(result.get("mechanism_of_action"))),
        source_link=(
            f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={set_id}" if set_id else ""
        ),
    )
