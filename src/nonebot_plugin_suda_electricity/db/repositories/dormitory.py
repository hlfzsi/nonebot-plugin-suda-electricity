__all__ = ["DormitoryRepository"]

from collections import defaultdict
from typing import Dict, List

from sqlmodel import select, col
from sqlmodel.ext.asyncio.session import AsyncSession

from ..engine import SESSION_MAKER, WRITE_LOCK
from ..models import (
    Dormitory,
    DormitoryDetail,
    DormitoryTable,
    DormitoryUser,
    DormitoryUserTable,
)
from .base import BaseRepository


class DormitoryRepository(BaseRepository[DormitoryTable]):
    def __init__(self) -> None:
        super().__init__(DormitoryTable)

    async def get(self, dormitory_key: str) -> Dormitory | None:
        async with SESSION_MAKER() as session:
            dormitory = await session.get(DormitoryTable, dormitory_key)
            if dormitory is None:
                return None
            return dormitory.to_domain()

    async def get_detail(self, dormitory_key: str) -> DormitoryDetail | None:
        async with SESSION_MAKER() as session:
            dormitory = await session.get(DormitoryTable, dormitory_key)
            if dormitory is None:
                return None
            users_by_key = await self._load_users_by_dormitory_keys(
                session, [dormitory_key]
            )
            return dormitory.to_detail(users_by_key.get(dormitory_key, []))

    async def list(self) -> List[Dormitory]:
        async with SESSION_MAKER() as session:
            stmt = select(DormitoryTable).order_by(col(DormitoryTable.dormitory_key))
            dormitories = list((await session.exec(stmt)).all())
            return [dormitory.to_domain() for dormitory in dormitories]

    async def list_details(self) -> List[DormitoryDetail]:
        async with SESSION_MAKER() as session:
            stmt = select(DormitoryTable).order_by(col(DormitoryTable.dormitory_key))
            dormitories = list((await session.exec(stmt)).all())
            return await self._build_details(session, dormitories)

    async def count(self) -> int:
        async with SESSION_MAKER() as session:
            stmt = select(DormitoryTable)
            return len(list((await session.exec(stmt)).all()))

    async def list_due_for_check(
        self,
        *,
        now: int,
        limit: int,
    ) -> List[Dormitory]:
        async with SESSION_MAKER() as session:
            stmt = (
                select(DormitoryTable)
                .where(col(DormitoryTable.next_check_at) <= now)
                .order_by(
                    col(DormitoryTable.next_check_at),
                    col(DormitoryTable.dormitory_key),
                )
                .limit(limit)
            )
            dormitories = list((await session.exec(stmt)).all())
            return [dormitory.to_domain() for dormitory in dormitories]

    async def list_due_details(
        self,
        *,
        now: int,
        limit: int,
    ) -> List[DormitoryDetail]:
        async with SESSION_MAKER() as session:
            stmt = (
                select(DormitoryTable)
                .where(col(DormitoryTable.next_check_at) <= now)
                .order_by(
                    col(DormitoryTable.next_check_at),
                    col(DormitoryTable.dormitory_key),
                )
                .limit(limit)
            )
            dormitories = list((await session.exec(stmt)).all())
            return await self._build_details(session, dormitories)

    async def update_threshold(
        self,
        *,
        dormitory_key: str,
        balance_threshold: float,
    ) -> Dormitory | None:
        normalized_threshold = _normalize_balance_threshold(balance_threshold)

        async with WRITE_LOCK:
            async with SESSION_MAKER() as session:
                dormitory = await session.get(DormitoryTable, dormitory_key)
                if dormitory is None:
                    return None
                dormitory.update_balance_threshold(normalized_threshold)
                await session.flush()
                await session.refresh(dormitory)
                await session.commit()
                return dormitory.to_domain()

    async def update_check_schedule(
        self,
        *,
        dormitory_key: str,
        last_check_at: int,
        next_check_at: int,
    ) -> Dormitory | None:
        async with WRITE_LOCK:
            async with SESSION_MAKER() as session:
                dormitory = await session.get(DormitoryTable, dormitory_key)
                if dormitory is None:
                    return None
                dormitory.update_check_schedule(
                    last_check_at=last_check_at,
                    next_check_at=next_check_at,
                )
                await session.flush()
                await session.refresh(dormitory)
                await session.commit()
                return dormitory.to_domain()

    async def delete(self, dormitory_key: str) -> bool:
        async with WRITE_LOCK:
            async with SESSION_MAKER() as session:
                dormitory = await session.get(DormitoryTable, dormitory_key)
                if dormitory is None:
                    return False

                stmt = select(DormitoryUserTable).where(
                    col(DormitoryUserTable.dormitory_key) == dormitory_key
                )
                users = list((await session.exec(stmt)).all())
                for user in users:
                    await session.delete(user)

                await session.delete(dormitory)
                await session.commit()
                return True

    async def delete_if_orphan(self, dormitory_key: str) -> bool:
        async with WRITE_LOCK:
            async with SESSION_MAKER() as session:
                return await self._delete_if_orphan(session, dormitory_key)

    @staticmethod
    async def _delete_if_orphan(session: AsyncSession, dormitory_key: str) -> bool:
        stmt = (
            select(DormitoryUserTable.user_id)
            .where(col(DormitoryUserTable.dormitory_key) == dormitory_key)
            .limit(1)
        )
        if (await session.exec(stmt)).first() is not None:
            return False

        dormitory = await session.get(DormitoryTable, dormitory_key)
        if dormitory is None:
            return False

        await session.delete(dormitory)
        await session.commit()
        return True

    async def _build_details(
        self,
        session: AsyncSession,
        dormitories: List[DormitoryTable],
    ) -> List[DormitoryDetail]:
        dormitory_keys = [dormitory.dormitory_key for dormitory in dormitories]
        users_by_key = await self._load_users_by_dormitory_keys(session, dormitory_keys)
        return [
            dormitory.to_detail(users_by_key.get(dormitory.dormitory_key, []))
            for dormitory in dormitories
        ]

    @staticmethod
    async def _load_users_by_dormitory_keys(
        session: AsyncSession,
        dormitory_keys: List[str],
    ) -> Dict[str, List[DormitoryUser]]:
        if not dormitory_keys:
            return {}

        stmt = (
            select(DormitoryUserTable)
            .where(col(DormitoryUserTable.dormitory_key).in_(dormitory_keys))
            .order_by(col(DormitoryUserTable.user_name))
        )
        users = list((await session.exec(stmt)).all())

        users_by_key = defaultdict(list)
        for user in users:
            users_by_key[user.dormitory_key].append(user.to_domain())
        return dict(users_by_key)


def _normalize_balance_threshold(balance_threshold: float) -> float:
    normalized = float(balance_threshold)
    if normalized < 0:
        raise ValueError("balance_threshold must be greater than or equal to 0")
    return normalized
