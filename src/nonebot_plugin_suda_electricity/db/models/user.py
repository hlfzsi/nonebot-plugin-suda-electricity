__all__ = ["DormitoryUser", "DormitoryUserTable"]

import hashlib
import time

from pydantic import BaseModel
from sqlalchemy import Column
from sqlmodel import Field

from .base import Base
from .types import EncryptedString
from ...crypto import get_salt
from ...utils import BASE_DATA_DIR


async def build_user_name_hash(user_name: str) -> str:
    salt = await get_salt(BASE_DATA_DIR, "salty.bin")
    data = f"{salt}:{user_name}".encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class DormitoryUser(BaseModel):
    user_id: str
    user_name: str
    password: str
    subscribe_type: str
    subscribe_id: str
    dormitory_key: str


class DormitoryUserTable(Base, table=True):
    user_id: str = Field(
        ..., primary_key=True, description="Owner user id in the IM platform"
    )
    user_name: str = Field(
        sa_column=Column(EncryptedString),
        description="User login name",
    )
    user_name_hash: str = Field(
        default="",
        index=True,
        sa_column_kwargs={"unique": True},
        description="Deterministic hash for querying user login name",
    )
    password: str = Field(
        sa_column=Column(EncryptedString), description="User login password"
    )
    subscribe_type: str = Field(
        default="",
        index=True,
        description="Subscription type, e.g. group/private",
    )
    subscribe_id: str = Field(
        default="", index=True, description="Subscription target id"
    )
    dormitory_key: str = Field(
        default="",
        index=True,
        description="Weakly-linked dormitory key",
    )

    created_at: int = Field(default_factory=lambda: int(time.time()))
    updated_at: int = Field(default_factory=lambda: int(time.time()))

    @property
    def belongs_to_dormitory_key(self) -> str:
        return self.dormitory_key

    def rebind_dormitory(self, dormitory_key: str) -> None:
        self.dormitory_key = dormitory_key
        self.updated_at = int(time.time())

    def update_subscription(
        self,
        *,
        user_id: str | None = None,
        subscribe_type: str | None = None,
        subscribe_id: str | None = None,
    ) -> None:
        if user_id is not None:
            self.user_id = user_id
        if subscribe_type is not None:
            self.subscribe_type = subscribe_type
        if subscribe_id is not None:
            self.subscribe_id = subscribe_id
        self.updated_at = int(time.time())

    def update_password(self, password: str) -> None:
        self.password = password
        self.updated_at = int(time.time())

    def to_domain(self) -> DormitoryUser:
        return DormitoryUser(
            user_id=self.user_id,
            user_name=self.user_name,
            password=self.password,
            subscribe_type=self.subscribe_type,
            subscribe_id=self.subscribe_id,
            dormitory_key=self.dormitory_key,
        )
