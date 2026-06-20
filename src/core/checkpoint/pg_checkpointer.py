# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import re

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

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


def get_checkpointer() -> AsyncPostgresSaver:
    """Return an AsyncPostgresSaver configured from DATABASE_URL.

    The caller is responsible for running ``await saver.setup()`` once before
    the first graph invocation so LangGraph can create its checkpoint tables.

    Uses its own psycopg connection pool, separate from the SQLAlchemy asyncpg
    pool, because LangGraph's checkpointer requires psycopg3 specifically.
    Both pools connect to the same DATABASE_URL, so there is still a single
    source of truth for connection configuration.
    """
    conn_string = _checkpointer_conn_string()
    serde = JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES)
    return AsyncPostgresSaver.from_conn_string(conn_string, serde=serde)
