from dishka import AsyncContainer, make_async_container

from t2r.di.providers.app import AppProvider
from t2r.di.providers.request import RequestProvider


def build_container() -> AsyncContainer:
    return make_async_container(AppProvider(), RequestProvider())
