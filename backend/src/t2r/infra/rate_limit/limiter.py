from __future__ import annotations

from starlette.requests import Request

from slowapi import Limiter
from slowapi.util import get_remote_address


def _client_ip(request: Request) -> str:
    """Реальный IP клиента. За reverse-proxy берём первый хоп из X-Forwarded-For
    (proxy обязан его проставлять), иначе — адрес соединения."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return get_remote_address(request)


# Единый инстанс лимитера: и приложение (SlowAPIMiddleware), и роуты через
# декоратор @limiter.limit(...) должны ссылаться на ОДИН и тот же объект,
# иначе декораторы на эндпоинтах не сработают.
limiter = Limiter(key_func=_client_ip)


def make_limiter() -> Limiter:
    return limiter
