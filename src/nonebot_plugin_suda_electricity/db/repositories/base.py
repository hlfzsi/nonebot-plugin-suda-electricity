__all__ = ["BaseRepository"]

from abc import ABC
from typing import Generic, TypeVar

from sqlmodel import SQLModel

from ..engine import SESSION_MAKER, WRITE_LOCK

T = TypeVar("T", bound=SQLModel)


class BaseRepository(ABC, Generic[T]):
    def __init__(self, model_class: type[T]):
        self.model_class = model_class

    async def get_by_id(self, id_value: object) -> T | None:
        async with SESSION_MAKER() as session:
            return await session.get(self.model_class, id_value)

    async def save(self, entity: T) -> T:
        async with WRITE_LOCK:
            async with SESSION_MAKER() as session:
                merged_entity = await session.merge(entity)
                await session.flush()
                await session.refresh(merged_entity)
                await session.commit()
        return merged_entity
