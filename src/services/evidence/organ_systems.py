# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Organ-system vocabulary shared by the liability builder and the omission guard.

Each system pairs a pattern for structured phenotype labels (MGI/IMPC mouse terms,
Open Targets safety events) with a pattern for the words prose would use to name it.
Patterns are word-bounded where a bare stem would match inside another word
("renal" in "adrenal", "ear" in "early"). Pure module: no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# (system, label pattern, prose pattern) — order is the order systems are reported in.
_SYSTEMS: tuple[tuple[str, str, str], ...] = (
    (
        "lethality / development",
        r"\blethal|mortality|\bdeath\b|embryo|prenatal|perinatal|preweaning|postnatal growth"
        r"|growth retard|decreased body size|\bdwarf",
        r"\blethal|death|surviv|viab|embryo|developmental|perinatal|preweaning|stunt|body size",
    ),
    (
        "nervous system",
        r"neur|brain|cerebr|cerebell|hippocamp|nervous|behavio|locomot|seizure"
        r"|synap|dopamin|motor coordination|cognit|learning|spatial memory|memory (?:deficit|impair)|gait|tremor|ataxia|myelin",
        r"neur|brain|cns\b|cerebr|nervous|behavio|cognit|seizure|motor|locomot|dopamin|myelin",
    ),
    (
        "heart",
        r"\bheart|cardi|myocard|ventric|\batri(?:al|um)|arrhythm|\bqt\b|cardiomyocyte",
        r"\bheart|cardi|myocard|ventric|arrhythm",
    ),
    (
        "blood vessels / blood pressure",
        r"blood pressure|hypertens|hypotens|vascul|vaso|arter|aort|angiogen|endotheli|vessel",
        r"blood pressure|hypertens|hypotens|vascul|vaso|arter|vessel|endotheli",
    ),
    (
        "lung",
        r"\blung|pulmonar|pneumocyte|alveol|bronch|respirat",
        r"\blung|pulmonar|pneumocyte|alveol|bronch|respirat",
    ),
    (
        "kidney",
        r"kidney|(?<![a-z])renal|nephr|glomerul|podocyt|proteinur|albuminur",
        r"kidney|(?<![a-z])renal|nephr|glomerul|podocyt|proteinur|albuminur",
    ),
    (
        "liver",
        r"\bliver|hepat|\bbile\b|biliar|cholesta",
        r"\bliver|hepat|biliar|bile\b",
    ),
    (
        "metabolism (glucose / lipids)",
        r"cholesterol|lipoprotein|triglycer|\blipid|glucose|insulin|glycem|diabet|\bobes|adipos"
        r"|body fat|energy expenditure|\bketon",
        r"cholesterol|lipid|triglycer|glucose|insulin|glyc|diabet|metabol|obes|adipos",
    ),
    (
        "immune / blood cells",
        r"immun|lymph|\bt cell|\bb cell|leukocyte|neutrophil|macrophage|monocyte|eosinophil"
        r"|infection|antibod|thym|spleen|splen|hemat|haemat|erythro|anemi|anaemi"
        r"|platelet|bleed|hemorrh|haemorrh|coagul|thromb|inflammatory cell|immune cell infiltrat",
        r"immun|lymph|leukocyte|neutrophil|macrophage|infection|inflamm|spleen|splen|hemat"
        r"|haemat|erythro|anemi|anaemi|platelet|bleed|coagul|thromb",
    ),
    (
        "reproductive",
        r"fertil|sperm|testis|testes|testic|ovar|uter|oocyte|estrous|oestrous|seminiferous"
        r"|mammary|lactat",
        r"fertil|reproduct|sperm|testi|ovar|uter|oocyte|lactat",
    ),
    (
        "endocrine",
        r"adrenal|thyroid|pituitar|corticoster|aldosteron|hormone|parathyroid",
        r"adrenal|thyroid|pituitar|endocrin|hormone|corticoster|aldosteron",
    ),
    (
        "gastrointestinal",
        r"intestin|\bcolon|\bgut\b|stomach|gastr|esophag|oesophag|digest|\bbowel|pancrea",
        r"intestin|colon|\bgut\b|stomach|gastr|esophag|oesophag|digest|bowel|pancrea",
    ),
    (
        "skeleton",
        r"\bbone|skelet|osteo|cartilag|\bjoint|\blimb|\btooth|\bteeth|craniofac|vertebra",
        r"\bbone|skelet|osteo|cartilag|joint",
    ),
    (
        "skeletal muscle",
        r"skeletal muscle|muscle fib|myopath|muscle weakness|muscle strength|grip strength"
        r"|muscle degenerat|myofib",
        r"skeletal muscle|muscle|myopath",
    ),
    (
        "skin",
        r"\bskin|\bhair|epiderm|dermis|dermal|\bcoat\b|keratinocyte",
        r"\bskin|\bhair|epiderm|derm",
    ),
    (
        "eye",
        r"\beyes?\b|retina|\blens\b|cornea|vision|visual|optic|photorecept|cataract",
        r"\beyes?\b|retina|\blens\b|cornea|vision|visual|ocular|optic",
    ),
    (
        "hearing",
        r"hearing|cochlea|\bears?\b|auditory|deaf|vestibul",
        r"hearing|cochlea|\bears?\b|auditory|deaf|vestibul",
    ),
)

SYSTEMS: tuple[str, ...] = tuple(name for name, _, _ in _SYSTEMS)
_LABEL_RE = {name: re.compile(p, re.IGNORECASE) for name, p, _ in _SYSTEMS}
_PROSE_RE = {name: re.compile(p, re.IGNORECASE) for name, _, p in _SYSTEMS}


def systems_of(label: str) -> list[str]:
    """Organ systems a structured phenotype or adverse-event label names."""
    return [name for name in SYSTEMS if _LABEL_RE[name].search(label)]


def group_by_system(labels: Iterable[str]) -> dict[str, list[str]]:
    """System → the labels naming it (a label can name more than one), in system order."""
    grouped: dict[str, list[str]] = {}
    for label in dict.fromkeys(str(x).strip() for x in labels if x and str(x).strip()):
        for name in systems_of(label):
            grouped.setdefault(name, []).append(label)
    return {name: grouped[name] for name in SYSTEMS if name in grouped}


def prose_names_system(text: str, system: str) -> bool:
    """Whether prose names ``system`` in any of the words a writer would use for it."""
    return bool(_PROSE_RE[system].search(text))
