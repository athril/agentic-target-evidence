# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Target prioritization capability — placeholder."""

from __future__ import annotations

from typing import Any, NoReturn


def build_target_prioritization_graph(*args: Any, **kwargs: Any) -> NoReturn:
    raise NotImplementedError(
        "target_prioritization is not yet implemented. "
        "It will rank a list of gene candidates by integrating scores from "
        "all six lens perspectives and the knowledge_graph service."
    )
