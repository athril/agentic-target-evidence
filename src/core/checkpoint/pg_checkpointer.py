# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import cast

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

# Pydantic models and enums from src/schemas/ that flow through PipelineState
# (and therefore get msgpack-serialized into checkpoints). LangGraph's default
# serializer allows-with-warning any module/class it doesn't recognize, and
# will start blocking them once LANGGRAPH_STRICT_MSGPACK becomes the default —
# allowlisting here keeps checkpointing working without relying on that warning.
_ALLOWED_MSGPACK_MODULES = [
    ("schemas.evidence", "Direction"),
    ("schemas.evidence", "EvidenceType"),
    ("schemas.evidence", "DataClass"),
    ("schemas.evidence", "LensTopic"),
    ("schemas.evidence", "CoreClaim"),
    ("schemas.evidence", "Evidence"),
    ("schemas.verdicts", "LensVerdict"),
    ("schemas.messages", "AgentMessage"),
]


def _checkpointer_conn_string() -> str:
    """Convert DATABASE_URL to a psycopg-compatible connection string.

    SQLAlchemy uses ``postgresql+asyncpg://`` scheme; psycopg expects
    ``postgresql://`` (or plain ``postgres://``).  The driver prefix is the
    only difference we need to normalise.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    # Strip any SQLAlchemy driver prefix (e.g. +asyncpg, +psycopg2)
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url)


# A run holds its checkpointer for hours while single nodes run for many minutes. One
# long-lived connection meant a connection the server dropped mid-run (seen: "invalid
# message length" → closed) failed the run at its next checkpoint write. A small pool
# validates each connection on checkout and replaces a dead one; TCP keepalives keep an
# idle connection alive across a long node and expose a dead one early.
_POOL_MAX_SIZE = int(os.getenv("CHECKPOINT_POOL_MAX_SIZE", "4"))
_CONNECTION_KWARGS = {
    # What AsyncPostgresSaver.from_conn_string configures on its single connection.
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
    "keepalives": 1,
    "keepalives_idle": 60,
    "keepalives_interval": 15,
    "keepalives_count": 4,
}


@asynccontextmanager
async def _pooled_saver() -> AsyncIterator[AsyncPostgresSaver]:
    serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES)
    async with AsyncConnectionPool(
        _checkpointer_conn_string(),
        min_size=1,
        max_size=max(1, _POOL_MAX_SIZE),
        kwargs=_CONNECTION_KWARGS,
        check=AsyncConnectionPool.check_connection,
        open=False,
    ) as pool:
        # row_factory=dict_row above makes these dict-row connections.
        yield AsyncPostgresSaver(
            conn=cast("AsyncConnectionPool[AsyncConnection[DictRow]]", pool), serde=serde
        )


def get_checkpointer() -> AbstractAsyncContextManager[AsyncPostgresSaver]:
    """Return an async context manager yielding an AsyncPostgresSaver configured
    from DATABASE_URL.

    The caller is responsible for running ``await saver.setup()`` once before
    the first graph invocation so LangGraph can create its checkpoint tables.

    Uses its own psycopg connection pool (validated on checkout, see above),
    separate from the SQLAlchemy asyncpg pool, because LangGraph's checkpointer
    requires psycopg3 specifically. Both pools connect to the same DATABASE_URL,
    so there is still a single source of truth for connection configuration.
    """
    return _pooled_saver()
