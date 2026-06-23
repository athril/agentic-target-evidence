# SPDX-FileCopyrightText: 2026 Patryk Orzechowski <patryk.orzechowski@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Langfuse per-run project provisioning.

Creates a Langfuse project for each gene/disease title if one doesn't already
exist, writing directly to the Langfuse Postgres DB (the /api/public/projects
endpoint is gated behind a paid plan on self-hosted).

Results are cached in config/langfuse_projects.json so each title only
triggers one DB write ever.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import bcrypt

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_CACHE_FILE = Path(__file__).parents[3] / "config" / "langfuse_projects.json"
_cache: dict[str, dict[str, Any]] | None = None  # title → {project_id, public_key, secret_key}


def _load_cache() -> dict[str, dict[str, Any]]:
    global _cache
    if _cache is None:
        _cache = json.loads(_CACHE_FILE.read_text()) if _CACHE_FILE.exists() else {}
    return _cache


def _save_cache(cache: dict[str, dict[str, Any]]) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _langfuse_db_url() -> str:
    """Derive the Langfuse Postgres URL.

    Prefers LANGFUSE_DATABASE_URL; falls back to replacing the DB name in
    DATABASE_URL (both DBs share the same postgres instance).
    """
    explicit = os.environ.get("LANGFUSE_DATABASE_URL")
    if explicit:
        return explicit
    app_url = os.environ.get("DATABASE_URL", "")
    if app_url:
        # e.g. postgresql+asyncpg://user:pass@host:port/gene_target_validation
        # → postgresql+asyncpg://user:pass@host:port/langfuse
        base, _, _ = app_url.rpartition("/")
        return f"{base}/langfuse"
    raise RuntimeError("Set LANGFUSE_DATABASE_URL or DATABASE_URL to provision Langfuse projects")


async def _insert_project(
    conn: asyncpg.Connection,
    project_id: str,
    title: str,
    org_id: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO projects (id, name, org_id, created_at, updated_at)
        VALUES ($1, $2, $3, NOW(), NOW())
        ON CONFLICT (id) DO NOTHING
        """,
        project_id,
        title,
        org_id,
    )


async def _insert_api_key(
    conn: asyncpg.Connection,
    project_id: str,
    public_key: str,
    raw_secret: str,
) -> None:
    hashed = bcrypt.hashpw(raw_secret.encode(), bcrypt.gensalt(rounds=11)).decode()
    fast_hashed = hashlib.sha256(raw_secret.encode()).hexdigest()
    display = raw_secret[:12] + "..." + raw_secret[-4:]
    key_id = "cprj" + secrets.token_hex(10)

    await conn.execute(
        """
        INSERT INTO api_keys
            (id, public_key, hashed_secret_key, fast_hashed_secret_key,
             display_secret_key, scope, project_id)
        VALUES ($1, $2, $3, $4, $5, 'PROJECT', $6)
        ON CONFLICT (public_key) DO NOTHING
        """,
        key_id,
        public_key,
        hashed,
        fast_hashed,
        display,
        project_id,
    )


async def _key_exists(conn: asyncpg.Connection, public_key: str) -> bool:
    row = await conn.fetchrow("SELECT 1 FROM api_keys WHERE public_key = $1", public_key)
    return row is not None


async def ensure_langfuse_project(title: str) -> tuple[str, str]:
    """Return (public_key, secret_key) for the Langfuse project named *title*.

    Creates the project and a key pair on first call; subsequent calls for the
    same title return the cached keys if they still exist in the DB, otherwise
    re-creates them (handles DB resets / clean-volumes).
    """
    import asyncpg  # imported lazily so the module loads without asyncpg on PATH

    cache = _load_cache()

    if title in cache:
        entry = cache[title]
        db_url = _langfuse_db_url()
        asyncpg_url = db_url.replace("postgresql+asyncpg://", "postgresql://").replace(
            "postgres+asyncpg://", "postgresql://"
        )
        conn = await asyncpg.connect(asyncpg_url)
        try:
            if await _key_exists(conn, entry["public_key"]):
                return entry["public_key"], entry["secret_key"]
            logger.warning(
                "[telemetry] cached project keys for %r not found in DB (DB was reset?), re-creating",
                title,
            )
        finally:
            await conn.close()
        del cache[title]

    org_id = os.environ.get("LANGFUSE_INIT_ORG_ID", "gtv-org")
    base_url = os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")

    project_id = "cprj-" + str(uuid.uuid4())
    public_key = f"pk-lf-{uuid.uuid4()}"
    secret_key = f"sk-lf-{uuid.uuid4()}"

    db_url = _langfuse_db_url()
    # asyncpg needs a plain postgresql:// URL, not postgresql+asyncpg://
    asyncpg_url = db_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres+asyncpg://", "postgresql://"
    )

    conn = await asyncpg.connect(asyncpg_url)
    try:
        async with conn.transaction():
            await _insert_project(conn, project_id, title, org_id)
            await _insert_api_key(conn, project_id, public_key, secret_key)
    finally:
        await conn.close()

    cache[title] = {
        "project_id": project_id,
        "public_key": public_key,
        "secret_key": secret_key,
        "base_url": base_url,
    }
    _save_cache(cache)
    logger.info("[telemetry] provisioned Langfuse project: %r  id=%s", title, project_id)
    return public_key, secret_key
