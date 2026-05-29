"""Единая политика выставления cookie. Secure берётся из настроек
(secure в prod, открыто в dev), SameSite=Lax — корректно при деплое фронта и API
под одним доменом за reverse-proxy."""
from __future__ import annotations

from fastapi import Response

from t2r.settings import get_settings

SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 дней — анонимная сессия чата


def set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure_effective,
        path="/",
    )
