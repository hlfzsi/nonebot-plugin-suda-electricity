__all__ = [
    "DEFAULT_BALANCE_THRESHOLD",
    "Dormitory",
    "DormitoryDetail",
    "DormitoryTable",
    "build_dormitory_key",
]

import time
import random
from typing import overload

from pydantic import BaseModel
from sqlalchemy import Column
from sqlmodel import Field

from .base import Base
from .types import EncryptedString
from .user import DormitoryUser
from ...suda.models import DormitoryProfile

DEFAULT_BALANCE_THRESHOLD = 20.0


def _clean(value: str | None) -> str:
    return (value or "").strip()


@overload
def build_dormitory_key(
    dormitory: DormitoryProfile,
) -> str: ...


@overload
def build_dormitory_key(
    *,
    dorm_room_id: str,
    campus_code: str,
    building_code: str,
    room_code: str,
) -> str: ...


def build_dormitory_key(
    dormitory: DormitoryProfile | None = None,
    *,
    dorm_room_id: str | None = None,
    campus_code: str | None = None,
    building_code: str | None = None,
    room_code: str | None = None,
) -> str:
    if dormitory is not None:
        dorm_room_id = dormitory.dorm_room_id
        campus_code = dormitory.campus_code
        building_code = dormitory.building_code
        room_code = dormitory.room_code

    normalized_codes = [_clean(campus_code), _clean(building_code), _clean(room_code)]
    if all(normalized_codes):
        return ":".join(normalized_codes)

    normalized_room_id = _clean(dorm_room_id)
    if normalized_room_id:
        return f"room:{normalized_room_id}"

    raise ValueError("unable to build dormitory key from empty dormitory fields")


class DormitoryTable(Base, table=True):
    dormitory_key: str = Field(primary_key=True, description="Stable dormitory key")
    dorm_room_id: str = Field(
        default="", index=True, description="Gateway dorm room id"
    )
    user_type: str = Field(default="", description="Gateway user type")
    campus_code: str = Field(default="", description="Campus code")
    building_code: str = Field(default="", description="Building code")
    room_code: str = Field(default="", description="Room code")
    campus_name: str = Field(
        default="",
        sa_column=Column(EncryptedString),
        description="Campus name"
    )
    building_name: str = Field(
        default="",
        sa_column=Column(EncryptedString),
        description="Building name"
    )
    room_name: str = Field(
        default="",
        sa_column=Column(EncryptedString),
        description="Room name"
    )
    balance_threshold: float = Field(
        default=DEFAULT_BALANCE_THRESHOLD,
        ge=0,
        description="Dormitory-level low balance threshold",
    )
    last_check_at: int = Field(
        default=0, index=True, description="Last scheduler check timestamp"
    )
    next_check_at: int = Field(
        default=0, index=True, description="Next due timestamp for scheduler"
    )
    created_at: int = Field(default_factory=lambda: int(time.time()))
    updated_at: int = Field(default_factory=lambda: int(time.time()))

    @classmethod
    def from_profile(
        cls,
        dormitory: DormitoryProfile,
        *,
        balance_threshold: float = DEFAULT_BALANCE_THRESHOLD,
        next_check_at: int = 0,
    ) -> "DormitoryTable":
        now = int(time.time())
        return cls(
            dormitory_key=build_dormitory_key(dormitory),
            dorm_room_id=dormitory.dorm_room_id,
            user_type=dormitory.user_type,
            campus_code=dormitory.campus_code,
            building_code=dormitory.building_code,
            room_code=dormitory.room_code,
            campus_name=dormitory.campus_name,
            building_name=dormitory.building_name,
            room_name=dormitory.room_name,
            balance_threshold=balance_threshold,
            last_check_at=0,
            next_check_at=next_check_at,
            created_at=now,
            updated_at=now,
        )

    def apply_profile(self, dormitory: DormitoryProfile) -> None:
        self.dorm_room_id = dormitory.dorm_room_id
        self.user_type = dormitory.user_type
        self.campus_code = dormitory.campus_code
        self.building_code = dormitory.building_code
        self.room_code = dormitory.room_code
        self.campus_name = dormitory.campus_name
        self.building_name = dormitory.building_name
        self.room_name = dormitory.room_name
        self.updated_at = int(time.time())

    def update_balance_threshold(self, balance_threshold: float) -> None:
        self.balance_threshold = balance_threshold
        self.updated_at = int(time.time())

    def update_check_schedule(self, *, last_check_at: int, next_check_at: int) -> None:
        self.last_check_at = last_check_at
        self.next_check_at = next_check_at
        self.updated_at = int(time.time())

    def to_domain(self) -> "Dormitory":
        return Dormitory(
            dormitory_key=self.dormitory_key,
            dorm_room_id=self.dorm_room_id,
            user_type=self.user_type,
            campus_code=self.campus_code,
            building_code=self.building_code,
            room_code=self.room_code,
            campus_name=self.campus_name,
            building_name=self.building_name,
            room_name=self.room_name,
            balance_threshold=self.balance_threshold,
            last_check_at=self.last_check_at,
            next_check_at=self.next_check_at,
        )

    def to_detail(self, users: list[DormitoryUser]) -> "DormitoryDetail":
        return DormitoryDetail(dormitory=self.to_domain(), users=users)


class Dormitory(BaseModel):
    dormitory_key: str
    dorm_room_id: str
    user_type: str
    campus_code: str
    building_code: str
    room_code: str
    campus_name: str
    building_name: str
    room_name: str
    balance_threshold: float = Field(default=DEFAULT_BALANCE_THRESHOLD, ge=0)
    last_check_at: int = 0
    next_check_at: int = 0


class DormitoryDetail(BaseModel):
    dormitory: Dormitory
    users: list[DormitoryUser] = Field(default_factory=list)

    def get_user_by_user_id(self, user_id: str) -> DormitoryUser | None:
        for user in self.users:
            if user.user_id == user_id:
                return user
        return None

    def get_user_by_name(self, user_name: str) -> DormitoryUser | None:
        for user in self.users:
            if user.user_name == user_name:
                return user
        return None

    def random_user(self) -> DormitoryUser | None:
        if not self.users:
            return None

        return random.choice(self.users)
