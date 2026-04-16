__all__ = ["ENGINE", "WRITE_LOCK", "SESSION_MAKER"]

import asyncio
from typing import Any

from orjson import dumps, loads
from sqlalchemy import event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from ..utils import APP_CONFIG, DATABASE_DATA_DIR


class _Lock:
    __slots__ = ("_enabled", "_async_lock")

    def __init__(self, enabled: bool = False):
        self._enabled = enabled
        self._async_lock = asyncio.Lock()

    async def __aenter__(self):
        if self._enabled:
            await self._async_lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._enabled:
            try:
                self._async_lock.release()
            except RuntimeError:
                pass


WRITE_LOCK = _Lock()
_db_path = DATABASE_DATA_DIR / "suda_electricity.db"
_DATABASE_URL = APP_CONFIG.suda_database_url or f"sqlite+aiosqlite:///{_db_path.as_posix()}"

ENGINE = create_async_engine(
    _DATABASE_URL,
    json_serializer=lambda obj: dumps(obj).decode("utf-8"),
    json_deserializer=loads,
)


@event.listens_for(ENGINE.sync_engine, "connect")
def set_sqlite_pragma(
    dbapi_connection: DBAPIConnection, connection_record: Any
) -> None:
    if ENGINE.dialect.name == "sqlite":
        WRITE_LOCK._enabled = True
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


SESSION_MAKER = async_sessionmaker(ENGINE, class_=AsyncSession, expire_on_commit=False)
