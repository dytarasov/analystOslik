from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter
from neo4j import AsyncDriver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
@inject
async def readyz(
    engine: FromDishka[AsyncEngine],
    neo4j_driver: FromDishka[AsyncDriver],
) -> dict:
    checks: dict[str, str] = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = f"fail: {exc!s}"
    try:
        async with neo4j_driver.session() as session:
            await session.run("RETURN 1")
        checks["neo4j"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["neo4j"] = f"fail: {exc!s}"

    ready = all(v == "ok" for v in checks.values())
    return {"ready": ready, "checks": checks}
