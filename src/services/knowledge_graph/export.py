# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Knowledge graph export stubs.

Exports subgraphs as artifact files under results/data/.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID


async def export_subgraph(run_id: UUID, gene_id: str, output_dir: Path) -> Path:
    """Export the gene-centric subgraph for a run as a JSON artifact.

    Returns the artifact path (stored as artifact_uri in Postgres).
    """
    raise NotImplementedError("export_subgraph not yet implemented")
