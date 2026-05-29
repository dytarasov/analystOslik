from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(dsn: str) -> AsyncEngine:
    return create_async_engine(
        dsn,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        # Fail fast instead of blocking forever if the pool is exhausted — a
        # caller gets a clean error rather than a hung request under load.
        pool_timeout=30,
        future=True,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
