import importlib
import logging
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from cryptography.fernet import Fernet

_BASE_MODULE = None
_DORMITORY_REPO_MODULE = None
_USER_REPO_MODULE = None
_CRYPTO_MODULE = None
_PACKAGE_ROOT = Path.cwd() / "src" / "nonebot_plugin_suda_electricity"


class _DummyAsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        del exc_type, exc_val, exc_tb
        return None


class _DormitoryProfile(types.SimpleNamespace):
    pass


def _build_profile(
    *,
    dorm_room_id: str = "DR-1001",
    campus_code: str = "01",
    building_code: str = "02",
    room_code: str = "0301",
) -> _DormitoryProfile:
    return _DormitoryProfile(
        user_type="student",
        dorm_room_id=dorm_room_id,
        campus_code=campus_code,
        building_code=building_code,
        room_code=room_code,
        campus_name="Main Campus",
        building_name="Building 2",
        room_name="Room 301",
    )


def _build_other_profile() -> _DormitoryProfile:
    return _build_profile(
        dorm_room_id="DR-2002",
        campus_code="01",
        building_code="08",
        room_code="0602",
    )


def _purge_db_modules() -> None:
    for name in list(sys.modules):
        if name == "nonebot_plugin_suda_electricity" or name.startswith(
            "nonebot_plugin_suda_electricity."
        ):
            sys.modules.pop(name, None)


def _load_repo_modules():
    global _BASE_MODULE, _DORMITORY_REPO_MODULE, _USER_REPO_MODULE, _CRYPTO_MODULE
    if (
        _BASE_MODULE is not None
        and _DORMITORY_REPO_MODULE is not None
        and _USER_REPO_MODULE is not None
        and _CRYPTO_MODULE is not None
    ):
        return _BASE_MODULE, _DORMITORY_REPO_MODULE, _USER_REPO_MODULE, _CRYPTO_MODULE

    fake_nonebot = types.ModuleType("nonebot")
    fake_nonebot.logger = logging.getLogger("test-nonebot")
    fake_nonebot.get_plugin_config = lambda model: model(suda_secret_key="test-secret-key-32-chars-min!")
    fake_nonebot.get_adapters = lambda: {}
    fake_nonebot.load_plugin = lambda name: None
    fake_nonebot.get_bots = lambda: {}

    fake_localstore = types.ModuleType("nonebot_plugin_localstore")
    fake_localstore.get_plugin_data_dir = lambda: Path.cwd() / ".pytest-localstore"

    fake_package = types.ModuleType("nonebot_plugin_suda_electricity")
    fake_package.__path__ = [str(_PACKAGE_ROOT)]

    fake_utils = types.ModuleType("nonebot_plugin_suda_electricity.utils")
    fake_utils.APP_CONFIG = types.SimpleNamespace(database_url="")
    fake_utils.BASE_DATA_DIR = Path.cwd() / ".pytest-localstore"
    fake_utils.DATABASE_DATA_DIR = Path.cwd() / ".pytest-localstore"
    fake_utils.logger = logging.getLogger("test-utils")

    _purge_db_modules()

    with patch.dict(
        sys.modules,
        {
            "nonebot": fake_nonebot,
            "nonebot_plugin_localstore": fake_localstore,
            "nonebot_plugin_suda_electricity": fake_package,
            "nonebot_plugin_suda_electricity.utils": fake_utils,
        },
    ):
        _BASE_MODULE = importlib.import_module(
            "nonebot_plugin_suda_electricity.db.repositories.base"
        )
        _DORMITORY_REPO_MODULE = importlib.import_module(
            "nonebot_plugin_suda_electricity.db.repositories.dormitory"
        )
        _USER_REPO_MODULE = importlib.import_module(
            "nonebot_plugin_suda_electricity.db.repositories.user"
        )
        _CRYPTO_MODULE = importlib.import_module(
            "nonebot_plugin_suda_electricity.crypto"
        )

    return _BASE_MODULE, _DORMITORY_REPO_MODULE, _USER_REPO_MODULE, _CRYPTO_MODULE


@pytest.fixture
async def repository_bundle():
    base_module, dormitory_repo_module, user_repo_module, crypto_module = _load_repo_modules()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(
            dormitory_repo_module.DormitoryTable.__table__.create,
            checkfirst=True,
        )
        await conn.run_sync(
            user_repo_module.DormitoryUserTable.__table__.create,
            checkfirst=True,
        )

    patchers = [
        patch.object(base_module, "SESSION_MAKER", session_maker),
        patch.object(base_module, "WRITE_LOCK", _DummyAsyncLock()),
        patch.object(dormitory_repo_module, "SESSION_MAKER", session_maker),
        patch.object(dormitory_repo_module, "WRITE_LOCK", _DummyAsyncLock()),
        patch.object(user_repo_module, "SESSION_MAKER", session_maker),
        patch.object(user_repo_module, "WRITE_LOCK", _DummyAsyncLock()),
    ]

    for patcher in patchers:
        patcher.start()

    try:
        crypto_module._FERNET = Fernet(Fernet.generate_key())
        
        yield (
            dormitory_repo_module.DormitoryRepository(),
            user_repo_module.DormitoryUserRepository(),
        )
    finally:
        for patcher in reversed(patchers):
            patcher.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_bind_deduplicates_dormitory_and_builds_detail(
    repository_bundle,
) -> None:
    dormitory_repo, user_repo = repository_bundle
    profile = _build_profile()

    dormitory1, user1 = await user_repo.bind(
        dormitory=profile,
        user_name="alice",
        password="pass-1",
        user_id="10001",
        subscribe_type="group",
        subscribe_id="20001",
        balance_threshold=15.0,
    )
    dormitory2, user2 = await user_repo.bind(
        dormitory=profile,
        user_name="bob",
        password="pass-2",
        user_id="10002",
        subscribe_type="group",
        subscribe_id="20001",
    )

    dormitories = await dormitory_repo.list()
    saved = await dormitory_repo.get_detail(dormitory1.dormitory_key)

    assert dormitory1.dormitory_key == dormitory2.dormitory_key
    assert user1.dormitory_key == dormitory1.dormitory_key
    assert user2.dormitory_key == dormitory2.dormitory_key
    assert len(dormitories) == 1
    assert saved is not None
    assert saved.dormitory.balance_threshold == 15.0
    assert sorted(user.user_name for user in saved.users) == ["alice", "bob"]


@pytest.mark.asyncio
async def test_rebind_moves_user_and_preserves_subscription_when_not_overridden(
    repository_bundle,
) -> None:
    dormitory_repo, user_repo = repository_bundle

    old_dormitory, _ = await user_repo.bind(
        dormitory=_build_profile(),
        user_name="alice",
        password="old-pass",
        user_id="10001",
        subscribe_type="private",
        subscribe_id="30001",
    )

    new_dormitory, rebound_user = await user_repo.bind(
        dormitory=_build_other_profile(),
        user_name="alice",
        password="new-pass",
        user_id="10001",
    )

    binding = await user_repo.get_binding_by_user_name("alice")

    assert await dormitory_repo.get(old_dormitory.dormitory_key) is None
    assert binding is not None
    assert binding[0].dormitory_key == new_dormitory.dormitory_key
    assert rebound_user.password == "new-pass"
    assert rebound_user.user_id == "10001"
    assert rebound_user.subscribe_type == "private"
    assert rebound_user.subscribe_id == "30001"


@pytest.mark.asyncio
async def test_update_threshold_is_dormitory_scoped(
    repository_bundle,
) -> None:
    dormitory_repo, user_repo = repository_bundle
    dormitory, _ = await user_repo.bind(
        dormitory=_build_profile(),
        user_name="alice",
        password="pass-1",
        user_id="owner-1",
        balance_threshold=20.0,
    )
    await user_repo.bind(
        dormitory=_build_profile(),
        user_name="bob",
        password="pass-2",
        user_id="owner-2",
    )

    updated = await dormitory_repo.update_threshold(
        dormitory_key=dormitory.dormitory_key,
        balance_threshold=12.5,
    )
    binding = await user_repo.get_binding_by_user_name("bob")

    assert updated is not None
    assert updated.balance_threshold == 12.5
    assert binding is not None
    assert binding[0].balance_threshold == 12.5


@pytest.mark.asyncio
async def test_unbind_only_cleans_up_orphan_dormitory_after_last_user_removed(
    repository_bundle,
) -> None:
    dormitory_repo, user_repo = repository_bundle
    profile = _build_profile()

    dormitory, alice = await user_repo.bind(
        dormitory=profile,
        user_name="alice",
        password="pass-1",
        user_id="owner-1",
    )
    _, bob = await user_repo.bind(
        dormitory=profile,
        user_name="bob",
        password="pass-2",
        user_id="owner-2",
    )

    assert await user_repo.unbind(user_id=alice.user_id)

    saved = await dormitory_repo.get_detail(dormitory.dormitory_key)
    assert saved is not None
    assert [user.user_name for user in saved.users] == ["bob"]

    assert await user_repo.unbind(user_id=bob.user_id)
    assert await dormitory_repo.get(dormitory.dormitory_key) is None


@pytest.mark.asyncio
async def test_list_due_details_groups_users_under_dormitory(
    repository_bundle,
) -> None:
    dormitory_repo, user_repo = repository_bundle

    first_dormitory, _ = await user_repo.bind(
        dormitory=_build_profile(),
        user_name="alice",
        password="pass-1",
        user_id="owner-1",
        initial_next_check_at=30,
    )
    await user_repo.bind(
        dormitory=_build_profile(),
        user_name="bob",
        password="pass-2",
        user_id="owner-2",
    )
    await user_repo.bind(
        dormitory=_build_other_profile(),
        user_name="carl",
        password="pass-3",
        user_id="owner-3",
        initial_next_check_at=300,
    )

    due = await dormitory_repo.list_due_details(now=100, limit=10)

    assert len(due) == 1
    assert due[0].dormitory.dormitory_key == first_dormitory.dormitory_key
    assert sorted(user.user_name for user in due[0].users) == ["alice", "bob"]


@pytest.mark.asyncio
async def test_delete_dormitory_cascades_bound_users(
    repository_bundle,
) -> None:
    dormitory_repo, user_repo = repository_bundle
    dormitory, _ = await user_repo.bind(
        dormitory=_build_profile(),
        user_name="alice",
        password="pass-1",
        user_id="owner-1",
    )
    await user_repo.bind(
        dormitory=_build_profile(),
        user_name="bob",
        password="pass-2",
        user_id="owner-2",
    )

    assert await dormitory_repo.delete(dormitory.dormitory_key)
    assert await dormitory_repo.get(dormitory.dormitory_key) is None
    assert await user_repo.get_by_user_name("alice") is None
    assert await user_repo.get_by_user_name("bob") is None


@pytest.mark.asyncio
async def test_owner_user_binding_lookup_and_unbind_remove_all_records(
    repository_bundle,
) -> None:
    dormitory_repo, user_repo = repository_bundle

    await user_repo.bind(
        dormitory=_build_profile(),
        user_name="alice",
        password="pass-1",
        user_id="owner-1",
        subscribe_type="private",
        subscribe_id="owner-1",
    )
    binding = await user_repo.get_binding_by_user_id("owner-1")

    assert binding is not None
    assert binding[1].user_id == "owner-1"
    assert binding[1].user_name == "alice"

    assert await user_repo.unbind_by_user_id("owner-1")
    assert await user_repo.get_by_user_name("alice") is None
    assert await dormitory_repo.count() == 0


@pytest.mark.asyncio
async def test_sensitive_columns_are_stored_as_ciphertext(repository_bundle) -> None:
    _, user_repo = repository_bundle

    await user_repo.bind(
        dormitory=_build_profile(),
        user_name="alice",
        password="pass-1",
        user_id="owner-1",
        subscribe_type="private",
        subscribe_id="owner-1",
    )

    base_module, dormitory_repo_module, user_repo_module, _ = _load_repo_modules()
    user_table = user_repo_module.DormitoryUserTable.__table__.name
    dormitory_table = dormitory_repo_module.DormitoryTable.__table__.name

    async with base_module.SESSION_MAKER() as session:
        user_row = (
            await session.exec(
                text(
                    f"SELECT user_name, user_name_hash, password FROM {user_table} WHERE user_id = :uid"
                ).bindparams(uid="owner-1")
            )
        ).first()
        dormitory_row = (
            await session.exec(
                text(
                    f"SELECT campus_name, building_name, room_name FROM {dormitory_table} WHERE dormitory_key = :key"
                ).bindparams(key="01:02:0301")
            )
        ).first()

    assert user_row is not None
    assert user_row[0] != "alice"
    assert user_row[1] == await user_repo_module.build_user_name_hash("alice")
    assert user_row[2] != "pass-1"

    assert dormitory_row is not None
    assert dormitory_row[0] != "Main Campus"
    assert dormitory_row[1] != "Building 2"
    assert dormitory_row[2] != "Room 301"


@pytest.mark.asyncio
async def test_hash_lookup_works_with_unicode_user_name(repository_bundle) -> None:
    _, user_repo = repository_bundle
    profile = _build_profile(room_code="0909")

    await user_repo.bind(
        dormitory=profile,
        user_name="测试用户",
        password="P@ss+中/=",
        user_id="owner-cn",
        subscribe_type="group",
        subscribe_id="20001",
    )

    binding = await user_repo.get_binding_by_user_name("测试用户")

    assert binding is not None
    assert binding[1].user_name == "测试用户"
    assert binding[1].password == "P@ss+中/="
    assert binding[0].room_code == "0909"


@pytest.mark.asyncio
async def test_user_repository_negative_threshold_and_missing_records(repository_bundle) -> None:
    dormitory_repo, user_repo = repository_bundle

    with pytest.raises(ValueError, match="greater than or equal to 0"):
        await dormitory_repo.update_threshold(dormitory_key="missing", balance_threshold=-1)

    assert await user_repo.get("missing") is None
    assert await user_repo.get_by_user_name("missing") is None
    assert await user_repo.get_by_user_id("missing") is None
    assert await user_repo.get_binding_by_user_name("missing") is None
    assert await user_repo.get_binding_by_user_id("missing") is None
    assert await user_repo.update_subscription_target(
        user_name="missing",
        user_id="missing",
        subscribe_type="private",
        subscribe_id="missing",
    ) is None
    assert await user_repo.unbind(user_id="missing") is False
    assert await user_repo.unbind_by_user_name("missing") is False
    assert await user_repo.unbind_by_user_id("missing") is False


@pytest.mark.asyncio
async def test_delete_if_orphan_handles_both_paths(repository_bundle) -> None:
    dormitory_repo, user_repo = repository_bundle

    profile = _build_profile()
    dormitory, _ = await user_repo.bind(
        dormitory=profile,
        user_name="alice",
        password="pass-1",
        user_id="owner-1",
    )

    assert await dormitory_repo.delete_if_orphan(dormitory.dormitory_key) is False
    assert await user_repo.unbind(user_id="owner-1") is True
    assert await dormitory_repo.delete_if_orphan(dormitory.dormitory_key) is False
