"""Apply SQL migrations from /migrations/*.sql at app startup.

Looks for the migrations directory in two places (in order):
1. `T2R_MIGRATIONS_DIR` env var (when set)
2. `/migrations` (default mount inside the docker image)
3. `<repo>/migrations` (fallback for local non-docker runs)

A migration is identified by the leading integer in its file name and recorded
in `schema_migrations(version int primary key)` created by 0001_init.sql.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import asyncpg

from t2r.logging import get_logger

logger = get_logger("migrations")

_FILENAME_RE = re.compile(r"^(\d+)_(.+)\.sql$")


def _resolve_dir() -> Path:
    if env := os.environ.get("T2R_MIGRATIONS_DIR"):
        return Path(env)
    docker_path = Path("/migrations")
    if docker_path.is_dir():
        return docker_path
    # repo-relative fallback: backend/src/t2r/infra/db/migrations.py → ../../../../../migrations
    return Path(__file__).resolve().parents[4] / "migrations"


def _to_asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


async def apply_pending(dsn: str) -> int:
    """Apply any migration not yet recorded in schema_migrations. Returns count applied."""
    directory = _resolve_dir()
    if not directory.is_dir():
        logger.warning("migrations directory not found", path=str(directory))
        return 0

    raw_dsn = _to_asyncpg_dsn(dsn)
    conn = await asyncpg.connect(raw_dsn)
    applied = 0
    try:
        # If schema_migrations doesn't exist yet, the first migration creates it.
        exists = await conn.fetchval(
            "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
        )
        if exists:
            rows = await conn.fetch("SELECT version FROM schema_migrations")
            seen = {r["version"] for r in rows}
        else:
            seen = set()

        for path in sorted(directory.iterdir()):
            m = _FILENAME_RE.match(path.name)
            if not m:
                continue
            version = int(m.group(1))
            name = m.group(2)
            if version in seen:
                continue
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)",
                    version,
                    name,
                )
            logger.info("migration applied", version=version, name=name)
            applied += 1
    finally:
        await conn.close()

    if applied:
        logger.info("migrations done", applied=applied)
    else:
        logger.info("migrations up-to-date")
    return applied
