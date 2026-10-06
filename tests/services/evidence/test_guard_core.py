# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the shared guard primitives (segmentation, negation, annotation)."""

from __future__ import annotations

import re

from services.evidence.guard_core import (
    Violation,
    annotate,
    has_unnegated_match,
    is_negated,
    iter_clauses,
    predicate_denied,
    split_clauses,
    split_sentences,
    strip_annotations,
)


class TestSegmentation:
    def test_sentences_split_on_terminal_punctuation_not_decimals(self):
        text = "LOEUF=0.759 is tolerant. Missense OEUF is 0.928! Done?"
        assert split_sentences(text) == [
            "LOEUF=0.759 is tolerant.",
            "Missense OEUF is 0.928!",
            "Done?",
        ]

    def test_abbreviation_does_not_end_a_sentence(self):
        assert len(split_sentences("Expressed in podocytes, e.g. Kidney cortex. Next.")) == 2

    def test_newlines_separate_table_cells_and_paragraphs(self):
        assert split_sentences("First line\nSecond line") == ["First line", "Second line"]

    def test_contrastive_connective_starts_a_new_clause(self):
        clauses = split_clauses(
            "The field is underserved at the target level but crowded in the indication space."
        )
        assert clauses == [
            "The field is underserved at the target level",
            "crowded in the indication space.",
        ]

    def test_consequential_participle_and_semicolon_split(self):
        clauses = split_clauses("Low TPM in lung, indicating relevance; kidney is key — podocytes")
        assert clauses == ["Low TPM in lung", "relevance", "kidney is key", "podocytes"]

    def test_iter_clauses_flattens_sentences(self):
        assert iter_clauses("A but B. C; D.") == ["A", "B.", "C", "D."]


class TestNegation:
    def test_preceding_cue_negates(self):
        clause = "TRPC6 is NOT haploinsufficient"
        assert is_negated(clause, clause.index("haplo"))

    def test_non_prefix_negates(self):
        clause = "low TPM in non-disease-relevant tissues"
        assert is_negated(clause, clause.index("disease-relevant"))

    def test_following_negation_does_not_count(self):
        clause = "haploinsufficiency is not an issue"
        assert not is_negated(clause, 0)

    def test_negation_covers_or_coordinated_items(self):
        clause = "does not indicate haploinsufficiency or severe missense constraints"
        assert is_negated(clause, clause.index("missense"))

    def test_negation_does_not_leak_across_clauses(self):
        pattern = re.compile(r"haploinsufficien\w*")
        assert has_unnegated_match(
            pattern, "TRPC6 is not constrained; haploinsufficiency is likely"
        )

    def test_denying_predicate(self):
        clause = "dependency across cancer cell lines is negligible"
        assert predicate_denied(clause, clause.index("lines") + len("lines"))
        assert not predicate_denied("dependency across many lines drives growth", 25)


class TestAnnotation:
    _V = Violation(rule_id="x.y", kind="contradiction", note="[⚠ X GUARD: bad.]")

    def test_annotate_appends_without_rewriting(self):
        assert annotate("Text.", [self._V]) == "Text.\n[⚠ X GUARD: bad.]"

    def test_annotate_is_idempotent_and_deduplicates(self):
        once = annotate("Text.", [self._V, self._V])
        assert once.count("X GUARD") == 1
        assert annotate(once, [self._V]) == once

    def test_annotate_without_violations_returns_text_unchanged(self):
        assert annotate("Text.", []) == "Text."

    def test_strip_annotations_removes_every_note(self):
        text = "Claim one.\n[⚠ A GUARD: 'underserved' quoted.]\n[⚠ B GUARD: other.]"
        assert strip_annotations(text) == "Claim one."
