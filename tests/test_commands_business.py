import importlib
import importlib.machinery
import logging
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

_MODULES = None
_PACKAGE_ROOT = Path.cwd() / "src" / "nonebot_plugin_suda_electricity"


class _DummyAsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        del exc_type, exc_val, exc_tb
        return None


def _purge_modules() -> None:
    for name in list(sys.modules):
        if name == "nonebot_plugin_suda_electricity" or name.startswith(
            "nonebot_plugin_suda_electricity."
        ):
            sys.modules.pop(name, None)


@pytest.fixture(autouse=True)
def _cleanup_module_state():
    global _MODULES
    SQLModel.metadata.clear()
    _MODULES = None
    yield
    _purge_modules()
    SQLModel.metadata.clear()
    _MODULES = None


def _load_modules():
    global _MODULES
    if _MODULES is not None:
        return _MODULES

    fake_nonebot = types.ModuleType("nonebot")
    fake_nonebot.logger = logging.getLogger("test-nonebot")
    fake_nonebot.get_plugin_config = lambda model: model(
        suda_secret_key="test-secret-key-32-chars-min!"
    )
    fake_nonebot.get_adapters = lambda: {}
    fake_nonebot.get_bots = lambda: {}
    fake_nonebot.load_plugin = lambda name: None

    fake_localstore = types.ModuleType("nonebot_plugin_localstore")
    fake_localstore.get_plugin_data_dir = lambda: Path.cwd() / ".pytest-localstore"

    fake_package = types.ModuleType("nonebot_plugin_suda_electricity")
    fake_package.__path__ = [str(_PACKAGE_ROOT)]
    fake_package.__spec__ = importlib.machinery.ModuleSpec(
        "nonebot_plugin_suda_electricity",
        loader=None,
        is_package=True,
    )
    fake_package.__spec__.submodule_search_locations = [str(_PACKAGE_ROOT)]

    fake_utils = types.ModuleType("nonebot_plugin_suda_electricity.utils")
    fake_utils.APP_CONFIG = types.SimpleNamespace(suda_database_url="")
    fake_utils.BASE_DATA_DIR = Path.cwd() / ".pytest-localstore"
    fake_utils.DATABASE_DATA_DIR = Path.cwd() / ".pytest-localstore"
    fake_utils.logger = logging.getLogger("test-utils")

    _purge_modules()

    with patch.dict(
        sys.modules,
        {
            "nonebot": fake_nonebot,
            "nonebot_plugin_localstore": fake_localstore,
            "nonebot_plugin_suda_electricity": fake_package,
            "nonebot_plugin_suda_electricity.utils": fake_utils,
        },
    ):
        _MODULES = {
            "base_repo": importlib.import_module(
                "nonebot_plugin_suda_electricity.db.repositories.base"
            ),
            "dormitory_repo": importlib.import_module(
                "nonebot_plugin_suda_electricity.db.repositories.dormitory"
            ),
            "user_repo": importlib.import_module(
                "nonebot_plugin_suda_electricity.db.repositories.user"
            ),
            "suda_models": importlib.import_module(
                "nonebot_plugin_suda_electricity.suda.models"
            ),
            "crypto": importlib.import_module(
                "nonebot_plugin_suda_electricity.crypto"
            ),
        }

    return _MODULES


@pytest.fixture
def modules():
    return _load_modules()


@pytest.fixture
async def repository_bundle(modules):
    base_module = modules["base_repo"]
    dormitory_repo_module = modules["dormitory_repo"]
    user_repo_module = modules["user_repo"]

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
        modules["crypto"]._FERNET = Fernet(Fernet.generate_key())
        yield (
            dormitory_repo_module.DormitoryRepository(),
            user_repo_module.DormitoryUserRepository(),
        )
    finally:
        for patcher in reversed(patchers):
            patcher.stop()
        await engine.dispose()


def _build_query_result(suda_models, *, user_name: str, balance: str, room_code: str):
    auth_response = suda_models.GatewayResponse[suda_models.UserIdentity](
        ok=True,
        status=200,
        raw_text="ok",
        content=suda_models.UserIdentity(account="10001", name=user_name, userType="1"),
    )
    dormitory = suda_models.DormitoryProfile(
        userType="student",
        dkRoomId=f"DR-{room_code}",
        xqbm="01",
        gylbm="02",
        fjbm=room_code,
        xqmc="Main Campus",
        gylmc="Building 2",
        fjmc=f"Room {room_code}",
        leftElec="100",
        balance=balance,
        leftElecK="0",
        balanceK="0",
        leftBzElec="0",
        balanceBz="0",
        avrElec="1.2",
        isMerge=False,
    )
    average_response = suda_models.GatewayResponse[suda_models.AverageElectricity](
        ok=True,
        status=200,
        raw_text="ok",
        content=suda_models.AverageElectricity(avrElec="1.2"),
    )
    stats = suda_models.ElectricityStats(
        leftElec="100",
        leftAmount=balance,
        leftDays="3",
        highestDailyAmount="5",
        lowestDailyAmount="1",
        averageDailyAmount="3",
        highestMonthlyAmount="100",
        lowestMonthlyAmount="20",
        averageMonthlyAmount="60",
    )
    stats_response = suda_models.GatewayResponse[suda_models.ElectricityStats](
        ok=True,
        status=200,
        raw_text="ok",
        content=stats,
    )
    return suda_models.ElectricityQueryResult(
        code="code",
        final_url="https://example.com",
        identity=auth_response.content,
        dormitory=dormitory,
        stats=stats,
        auth_response=auth_response,
        login_response=suda_models.GatewayResponse[suda_models.DormitoryProfile](
            ok=True,
            status=200,
            raw_text="ok",
            content=dormitory,
        ),
        average_response=average_response,
        stats_response=stats_response,
    )


@pytest.mark.asyncio
async def test_bind_and_lookup_support_unicode_user_and_special_password(
    modules,
    repository_bundle,
) -> None:
    _, user_repo = repository_bundle
    result = _build_query_result(
        modules["suda_models"],
        user_name="测试用户123",
        balance="16.80",
        room_code="0602",
    )

    await user_repo.bind(
        dormitory=result.dormitory,
        user_name="测试用户123",
        password="P@ss+中/=",
        user_id="owner-1",
        subscribe_type="private",
        subscribe_id="owner-1",
    )

    user = await user_repo.get_by_user_name("测试用户123")
    binding = await user_repo.get_binding_by_user_id("owner-1")

    assert user is not None
    assert user.user_name == "测试用户123"
    assert user.password == "P@ss+中/="
    assert binding is not None
    assert binding[1].user_name == "测试用户123"


@pytest.mark.asyncio
async def test_rebinding_one_owner_does_not_affect_other_owner(
    modules,
    repository_bundle,
) -> None:
    dormitory_repo, user_repo = repository_bundle
    suda_models = modules["suda_models"]

    first = _build_query_result(
        suda_models,
        user_name="alice",
        balance="30.00",
        room_code="0301",
    )
    second = _build_query_result(
        suda_models,
        user_name="bob",
        balance="18.50",
        room_code="0808",
    )
    rebound = _build_query_result(
        suda_models,
        user_name="alice",
        balance="12.00",
        room_code="0602",
    )

    await user_repo.bind(
        dormitory=first.dormitory,
        user_name="alice",
        password="old-pass",
        user_id="owner-1",
        subscribe_type="private",
        subscribe_id="owner-1",
    )
    await user_repo.bind(
        dormitory=second.dormitory,
        user_name="bob",
        password="pass-2",
        user_id="owner-2",
        subscribe_type="group",
        subscribe_id="20001",
    )

    await user_repo.bind(
        dormitory=rebound.dormitory,
        user_name="alice",
        password="new-pass",
        user_id="owner-1",
        subscribe_type="private",
        subscribe_id="owner-1",
    )

    owner_1 = await user_repo.get_binding_by_user_id("owner-1")
    owner_2 = await user_repo.get_binding_by_user_id("owner-2")

    assert owner_1 is not None
    assert owner_1[1].password == "new-pass"
    assert owner_1[0].room_code == "0602"
    assert owner_2 is not None
    assert owner_2[1].user_name == "bob"
    assert await dormitory_repo.count() == 2
