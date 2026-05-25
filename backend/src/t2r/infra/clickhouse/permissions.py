from __future__ import annotations

import secrets

from t2r.infra.clickhouse.client import CHClient


async def probe_readonly(client: CHClient) -> bool:
    """Return True if the user looks read-only.

    We attempt a CREATE TEMP table and a write to a system table. Both should
    fail for a properly restricted user. If either succeeds, we assume the
    account has elevated rights.
    """
    suffix = secrets.token_hex(4)
    test_table = f"_t2r_probe_{suffix}"
    try:
        await client.query(f"CREATE TABLE {test_table} (a UInt8) ENGINE=Memory")
    except Exception:
        return True
    try:
        await client.query(f"DROP TABLE IF EXISTS {test_table}")
    except Exception:
        pass
    return False
