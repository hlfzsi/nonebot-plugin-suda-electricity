__all__ = ["init_db", "shutdown_db"]

from .engine import ENGINE
from .models.base import METADATA


async def init_db() -> None:
    async with ENGINE.begin() as conn:
        await conn.run_sync(METADATA.create_all)


async def shutdown_db() -> None:
    await ENGINE.dispose()
