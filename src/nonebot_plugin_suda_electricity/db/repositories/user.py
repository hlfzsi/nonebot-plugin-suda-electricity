__all__ = ["BalanceThresholdInput", "DormitoryUserRepository", "UNSET"]

import time
from typing import Final, TypeAlias

from sqlmodel import select, col
from sqlmodel.ext.asyncio.session import AsyncSession

from ...suda.models import DormitoryProfile, ElectricityQueryResult
from ..engine import SESSION_MAKER, WRITE_LOCK
from ..models import (
    DEFAULT_BALANCE_THRESHOLD,
    Dormitory,
    DormitoryTable,
    DormitoryUser,
    DormitoryUserTable,
    build_user_name_hash,
    build_dormitory_key,
)
from .base import BaseRepository

UNSET: Final = object()
BalanceThresholdInput: TypeAlias = float | object


class DormitoryUserRepository(BaseRepository[DormitoryUserTable]):
    def __init__(self) -> None:
        super().__init__(DormitoryUserTable)

    async def get(self, record_id: str) -> DormitoryUser | None:
        async with SESSION_MAKER() as session:
            user = await session.get(DormitoryUserTable, record_id)
            if user is None:
                return None
            return user.to_domain()

    async def get_by_user_name(self, user_name: str) -> DormitoryUser | None:
        async with SESSION_MAKER() as session:
            user = await self._get_user_by_name(session, user_name)
            if user is None:
                return None
            return user.to_domain()

    async def get_by_user_id(self, user_id: str) -> DormitoryUser | None:
        async with SESSION_MAKER() as session:
            user = await self._get_user_by_user_id(session, user_id)
            if user is None:
                return None
            return user.to_domain()

    async def list_by_dormitory(self, dormitory_key: str) -> list[DormitoryUser]:
        async with SESSION_MAKER() as session:
            return await self._list_by_dormitory(session, dormitory_key)

    async def bind(
        self,
        *,
        dormitory: DormitoryProfile,
        user_name: str,
        password: str,
        user_id: str,
        subscribe_type: str | None = None,
        subscribe_id: str | None = None,
        balance_threshold: BalanceThresholdInput = UNSET,
        initial_next_check_at: int | None = None,
    ) -> tuple[Dormitory, DormitoryUser]:
        dormitory_key = build_dormitory_key(dormitory)
        now = int(time.time())

        async with WRITE_LOCK:
            async with SESSION_MAKER() as session:
                db_dormitory = await self._upsert_dormitory(
                    session,
                    dormitory=dormitory,
                    dormitory_key=dormitory_key,
                    balance_threshold=balance_threshold,
                    initial_next_check_at=initial_next_check_at,
                )
                user = await self._get_user_by_name(session, user_name)
                previous_dormitory_key = ""

                if user is None:
                    user = DormitoryUserTable(
                        user_name=user_name,
                        user_name_hash=await build_user_name_hash(user_name),
                        password=password,
                        user_id=user_id,
                        subscribe_type=subscribe_type or "",
                        subscribe_id=subscribe_id or "",
                        dormitory_key=dormitory_key,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(user)
                else:
                    previous_dormitory_key = user.dormitory_key
                    user.update_password(password)
                    user.update_subscription(
                        user_id=user_id,
                        subscribe_type=subscribe_type,
                        subscribe_id=subscribe_id,
                    )
                    user.rebind_dormitory(dormitory_key)

                if previous_dormitory_key and previous_dormitory_key != dormitory_key:
                    await self._delete_orphan_dormitory(
                        session,
                        previous_dormitory_key,
                    )

                await session.flush()
                await session.refresh(db_dormitory)
                await session.refresh(user)
                await session.commit()
                return db_dormitory.to_domain(), user.to_domain()

    async def bind_from_query_result(
        self,
        *,
        user_name: str,
        password: str,
        result: ElectricityQueryResult,
        user_id: str,
        subscribe_type: str | None = None,
        subscribe_id: str | None = None,
        balance_threshold: BalanceThresholdInput = UNSET,
        initial_next_check_at: int | None = None,
    ) -> tuple[Dormitory, DormitoryUser]:
        return await self.bind(
            dormitory=result.dormitory,
            user_name=user_name,
            password=password,
            user_id=user_id,
            subscribe_type=subscribe_type,
            subscribe_id=subscribe_id,
            balance_threshold=balance_threshold,
            initial_next_check_at=initial_next_check_at,
        )

    async def get_binding_by_user_name(
        self,
        user_name: str,
    ) -> tuple[Dormitory, DormitoryUser] | None:
        async with SESSION_MAKER() as session:
            user = await self._get_user_by_name(session, user_name)
            if user is None:
                return None

            dormitory = await session.get(DormitoryTable, user.dormitory_key)
            if dormitory is None:
                return None

            return dormitory.to_domain(), user.to_domain()

    async def get_binding_by_user_id(
        self,
        user_id: str,
    ) -> tuple[Dormitory, DormitoryUser] | None:
        async with SESSION_MAKER() as session:
            user = await self._get_user_by_user_id(session, user_id)
            if user is None:
                return None

            dormitory = await session.get(DormitoryTable, user.dormitory_key)
            if dormitory is None:
                return None

            return dormitory.to_domain(), user.to_domain()

    async def update_subscription_target(
        self,
        *,
        user_name: str,
        user_id: str,
        subscribe_type: str,
        subscribe_id: str,
    ) -> DormitoryUser | None:
        async with WRITE_LOCK:
            async with SESSION_MAKER() as session:
                user = await self._get_user_by_name(session, user_name)
                if user is None:
                    return None
                user.update_subscription(
                    user_id=user_id,
                    subscribe_type=subscribe_type,
                    subscribe_id=subscribe_id,
                )
                await session.flush()
                await session.refresh(user)
                await session.commit()
                return user.to_domain()

    async def unbind(
        self,
        *,
        user_id: str,
        cleanup_orphan_dormitory: bool = True,
    ) -> bool:
        async with WRITE_LOCK:
            async with SESSION_MAKER() as session:
                user = await session.get(DormitoryUserTable, user_id)
                if user is None:
                    return False

                dormitory_key = user.dormitory_key
                await session.delete(user)
                await session.flush()

                if cleanup_orphan_dormitory and dormitory_key:
                    await self._delete_orphan_dormitory(session, dormitory_key)

                await session.commit()
                return True

    async def unbind_by_user_name(self, user_name: str) -> bool:
        async with WRITE_LOCK:
            async with SESSION_MAKER() as session:
                user = await self._get_user_by_name(session, user_name)
                if user is None:
                    return False

                dormitory_key = user.dormitory_key
                await session.delete(user)
                await session.flush()

                if dormitory_key:
                    await self._delete_orphan_dormitory(session, dormitory_key)

                await session.commit()
                return True

    async def unbind_by_user_id(self, user_id: str) -> bool:
        async with WRITE_LOCK:
            async with SESSION_MAKER() as session:
                stmt = (
                    select(DormitoryUserTable)
                    .where(DormitoryUserTable.user_id == user_id)
                    .order_by(col(DormitoryUserTable.updated_at).desc())
                )
                users = list((await session.exec(stmt)).all())
                if not users:
                    return False

                dormitory_keys = {
                    user.dormitory_key for user in users if user.dormitory_key
                }
                for user in users:
                    await session.delete(user)
                await session.flush()

                for dormitory_key in dormitory_keys:
                    await self._delete_orphan_dormitory(session, dormitory_key)

                await session.commit()
                return True

    async def _upsert_dormitory(
        self,
        session: AsyncSession,
        *,
        dormitory: DormitoryProfile,
        dormitory_key: str,
        balance_threshold: BalanceThresholdInput,
        initial_next_check_at: int | None,
    ) -> DormitoryTable:
        db_dormitory = await session.get(DormitoryTable, dormitory_key)
        if db_dormitory is None:
            threshold = DEFAULT_BALANCE_THRESHOLD
            if balance_threshold is not UNSET:
                threshold = _normalize_balance_threshold(balance_threshold)

            next_check_at = (
                int(initial_next_check_at) if initial_next_check_at is not None else 0
            )
            db_dormitory = DormitoryTable.from_profile(
                dormitory,
                balance_threshold=threshold,
                next_check_at=next_check_at,
            )
            session.add(db_dormitory)
            await session.flush()
            return db_dormitory

        db_dormitory.apply_profile(dormitory)
        if balance_threshold is not UNSET:
            db_dormitory.update_balance_threshold(
                _normalize_balance_threshold(balance_threshold)
            )
        return db_dormitory

    @staticmethod
    async def _get_user_by_name(
        session: AsyncSession,
        user_name: str,
    ) -> DormitoryUserTable | None:
        user_name_hash = await build_user_name_hash(user_name)
        stmt = select(DormitoryUserTable).where(
            DormitoryUserTable.user_name_hash == user_name_hash
        )
        return (await session.exec(stmt)).first()

    @staticmethod
    async def _get_user_by_user_id(
        session: AsyncSession,
        user_id: str,
    ) -> DormitoryUserTable | None:
        stmt = (
            select(DormitoryUserTable)
            .where(DormitoryUserTable.user_id == user_id)
            .order_by(col(DormitoryUserTable.updated_at).desc())
        )
        return (await session.exec(stmt)).first()

    @staticmethod
    async def _list_by_dormitory(
        session: AsyncSession,
        dormitory_key: str,
    ) -> list[DormitoryUser]:
        stmt = (
            select(DormitoryUserTable)
            .where(DormitoryUserTable.dormitory_key == dormitory_key)
            .order_by(col(DormitoryUserTable.user_name))
        )
        users = list((await session.exec(stmt)).all())
        return [user.to_domain() for user in users]

    @staticmethod
    async def _delete_orphan_dormitory(
        session: AsyncSession,
        dormitory_key: str,
    ) -> None:
        stmt = (
            select(DormitoryUserTable.user_id)
            .where(DormitoryUserTable.dormitory_key == dormitory_key)
            .limit(1)
        )
        if (await session.exec(stmt)).first() is not None:
            return

        dormitory = await session.get(DormitoryTable, dormitory_key)
        if dormitory is None:
            return
        await session.delete(dormitory)


def _normalize_balance_threshold(balance_threshold: BalanceThresholdInput) -> float:
    if balance_threshold is UNSET:
        return DEFAULT_BALANCE_THRESHOLD
    normalized = float(balance_threshold)  # pyright: ignore[reportArgumentType]
    if normalized < 0:
        raise ValueError("balance_threshold must be greater than or equal to 0")
    return normalized
