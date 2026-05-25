"""Run SQL migrations against PostgreSQL.

Usage:
    python -m scripts.migrate up
    python -m scripts.migrate status
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"
FILENAME_RE = re.compile(r"^(\d+)_(.+)\.sql$")


def _asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


def _read_dsn() -> str:
    dsn = os.environ.get("T2R_PG_DSN")
    if not dsn:
        sys.exit("T2R_PG_DSN env var is required")
    return _asyncpg_dsn(dsn)


def _list_migrations() -> list[tuple[int, str, Path]]:
    files = []
    for p in sorted(MIGRATIONS_DIR.iterdir()):
        if not p.is_file() or not p.name.endswith(".sql"):
            continue
        m = FILENAME_RE.match(p.name)
        if not m:
            continue
        version = int(m.group(1))
        name = m.group(2)
        files.append((version, name, p))
    files.sort(key=lambda t: t[0])
    return files


async def _applied_versions(conn: asyncpg.Connection) -> set[int]:
    exists = await conn.fetchval(
        "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
    )
    if not exists:
        return set()
    rows = await conn.fetch("SELECT version FROM schema_migrations")
    return {r["version"] for r in rows}


async def cmd_up() -> None:
    dsn = _read_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        applied = await _applied_versions(conn)
        for version, name, path in _list_migrations():
            if version in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            print(f"applying {version:04d}_{name}")
            async with conn.transaction():
                await conn.execute(sql)
                # for the very first migration, the table is created inside it
                await conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)",
                    version,
                    name,
                )
        print("done")
    finally:
        await conn.close()


async def cmd_status() -> None:
    dsn = _read_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        applied = await _applied_versions(conn)
        for version, name, _ in _list_migrations():
            marker = "applied" if version in applied else "pending"
            print(f"{version:04d}  {marker:8s}  {name}")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("up")
    sub.add_parser("status")
    args = parser.parse_args()
    if args.cmd == "up":
        asyncio.run(cmd_up())
    elif args.cmd == "status":
        asyncio.run(cmd_status())


if __name__ == "__main__":
    main()
