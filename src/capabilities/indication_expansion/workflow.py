# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Indication expansion capability — placeholder."""

from __future__ import annotations

from typing import Any, NoReturn


def build_indication_expansion_graph(*args: Any, **kwargs: Any) -> NoReturn:
    raise NotImplementedError(
        "indication_expansion is not yet implemented. "
        "It will reuse retrieval + clinical_lens + commercial_lens agents "
        "and the target_validation subgraphs once those are decomposed."
    )
