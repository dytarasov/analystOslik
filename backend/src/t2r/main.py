from contextlib import asynccontextmanager

from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from t2r.api.admin.audit import router as admin_audit_router
from t2r.api.admin.auth import router as admin_auth_router
from t2r.api.admin.columns import router as admin_columns_router
from t2r.api.admin.demo_sse import router as admin_demo_sse_router
from t2r.api.admin.edit import router as admin_edit_router
from t2r.api.admin.graph import router as admin_graph_router
from t2r.api.admin.profiling import router as admin_profiling_router
from t2r.api.admin.selection import router as admin_selection_router
from t2r.api.admin.sources import router as admin_sources_router
from t2r.api.admin.tables import router as admin_tables_router
from t2r.api.client.session import router as client_sessions_router
from t2r.api.client.sources import router as client_sources_router
from t2r.api.client.tasks import router as client_tasks_router
from t2r.api.common.health import router as health_router
from t2r.di.container import build_container
from t2r.errors import DomainError, to_payload
from t2r.infra.db.migrations import apply_pending
from t2r.infra.rate_limit.limiter import make_limiter
from t2r.logging import configure_logging, get_logger, set_request_id
from t2r.settings import get_settings

logger = get_logger("main")


async def _recover_abandoned_profiling_runs(container) -> None:
    """Reset orphaned profiling_runs left behind by a previous backend.

    In-memory AgentRuns are lost on restart; without this, their DB rows would
    stay forever in 'running' and block new starts via the unique index.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from t2r.infra.db.repos.profiling_repo_pg import ProfilingRepoPg
    from t2r.infra.db.repos.source_repo_pg import SourceRepoPg
    from t2r.infra.security.cipher import FernetCipher

    sm = await container.get(async_sessionmaker[AsyncSession])
    cipher = await container.get(FernetCipher)
    async with sm() as s:
        source_ids = await ProfilingRepoPg(s).mark_all_active_abandoned(
            reason="abandoned_on_restart"
        )
        for sid in source_ids:
            await SourceRepoPg(s, cipher).sync_profiling_status_from_runs(sid)
        await s.commit()
        if source_ids:
            logger.info(
                "startup: profiling runs recovered",
                count=len(source_ids),
            )


async def _recover_abandoned_tasks(container) -> None:
    """Same idea as profiling recovery but for client-side text→SQL tasks.

    Without this, task_runs rows stay in 'running' forever after a restart and
    the user sees a frozen turn in the chat with no way to know what happened.
    We also drop an assistant message into the corresponding chat session so
    the next page load shows a clear «прервано рестартом сервиса» bubble.
    """
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    sm = await container.get(async_sessionmaker[AsyncSession])
    async with sm() as s:
        rows = (
            await s.execute(
                _text(
                    "UPDATE task_runs SET status = 'failed',"
                    " error = 'abandoned_on_restart', finished_at = now()"
                    " WHERE status = 'running'"
                    " RETURNING id, session_id"
                )
            )
        ).all()
        for tid, sid in rows:
            if sid is None:
                continue
            await s.execute(
                _text(
                    "INSERT INTO chat_messages (session_id, role, content, metadata)"
                    " VALUES (:sid, 'assistant', :c,"
                    " CAST(:m AS jsonb))"
                ),
                {
                    "sid": sid,
                    "c": "Запрос прерван перезапуском сервиса. Попробуйте задать его ещё раз.",
                    "m": '{"abandoned": true, "task_id": "' + str(tid) + '"}',
                },
            )
        await s.commit()
        if rows:
            logger.info("startup: client tasks recovered", count=len(rows))


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    # Dishka adds a middleware — it MUST be installed before the app starts
    # serving requests. We build the container synchronously here; lifespan
    # only handles startup migrations + graceful close.
    container = build_container()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            applied = await apply_pending(settings.pg_dsn)
            if applied:
                logger.info("startup: migrations applied", count=applied)
        except Exception as exc:  # noqa: BLE001
            logger.exception("startup: migrations failed", error=str(exc))
        try:
            await _recover_abandoned_profiling_runs(container)
        except Exception:
            logger.exception("startup: profiling recovery failed")
        try:
            await _recover_abandoned_tasks(container)
        except Exception:
            logger.exception("startup: task recovery failed")
        try:
            yield
        finally:
            await container.close()

    app = FastAPI(title="text-to-report", version="0.1.0", lifespan=lifespan)
    setup_dishka(container=container, app=app)

    limiter = make_limiter()
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = request.headers.get("x-request-id") or set_request_id()
        set_request_id(rid)
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status_code, content=to_payload(exc))

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(_request: Request, exc: RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content={"code": "RATE_LIMIT", "message": str(exc)},
        )

    app.include_router(health_router)
    app.include_router(admin_auth_router)
    app.include_router(admin_sources_router)
    app.include_router(admin_demo_sse_router)
    app.include_router(admin_profiling_router)
    app.include_router(admin_tables_router)
    app.include_router(admin_columns_router)
    app.include_router(admin_edit_router)
    app.include_router(admin_audit_router)
    app.include_router(admin_graph_router)
    app.include_router(admin_selection_router)
    app.include_router(client_sessions_router)
    app.include_router(client_tasks_router)
    app.include_router(client_sources_router)
    return app


app = create_app()
