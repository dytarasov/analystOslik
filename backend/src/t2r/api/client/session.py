from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Cookie, HTTPException, Response

from t2r.api.cookies import SESSION_MAX_AGE, set_cookie
from t2r.services.session_service import SessionService
from t2r.services.task_service import TaskService

router = APIRouter(prefix="/api/sessions", tags=["client-sessions"])

COOKIE = "t2r_session"


def _ensure_cookie(response: Response, current: str | None) -> str:
    if current:
        return current
    new = SessionService.new_cookie()
    set_cookie(response, COOKIE, new, max_age=SESSION_MAX_AGE)
    return new


@router.get("")
@inject
async def list_sessions(
    response: Response,
    svc: FromDishka[SessionService],
    t2r_session: str | None = Cookie(default=None),
) -> dict:
    cookie = _ensure_cookie(response, t2r_session)
    items = await svc.list_sessions(cookie)
    return {"items": items}


@router.post("")
@inject
async def create_session(
    payload: dict,
    response: Response,
    svc: FromDishka[SessionService],
    t2r_session: str | None = Cookie(default=None),
) -> dict:
    cookie = _ensure_cookie(response, t2r_session)
    sid = payload.get("source_id")
    title = payload.get("title")
    res = await svc.create_session(
        cookie,
        source_id=UUID(sid) if sid else None,
        title=title,
    )
    return res


@router.get("/{session_id}/messages")
@inject
async def list_messages(
    session_id: UUID,
    svc: FromDishka[SessionService],
) -> list[dict]:
    return await svc.list_messages(session_id)


@router.delete("/{session_id}", status_code=204)
@inject
async def delete_session(
    session_id: UUID,
    response: Response,
    svc: FromDishka[SessionService],
    t2r_session: str | None = Cookie(default=None),
) -> None:
    cookie = _ensure_cookie(response, t2r_session)
    ok = await svc.soft_delete_session(cookie, session_id)
    if not ok:
        # Either not found or doesn't belong to this cookie.
        raise HTTPException(status_code=404, detail="session not found")


@router.get("/{session_id}/active-task")
@inject
async def get_active_task(
    session_id: UUID,
    tasks: FromDishka[TaskService],
) -> dict | None:
    """Куда переподписать SSE если в этой сессии есть «живой» task."""
    res = await tasks.get_active_task_for_session(session_id)
    return res
