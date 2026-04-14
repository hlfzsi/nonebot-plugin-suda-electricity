import importlib
import importlib.machinery
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace
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
    fake_utils.APP_CONFIG = types.SimpleNamespace(database_url="")
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
            "config": importlib.import_module(
                "nonebot_plugin_suda_electricity.config"
            ),
            "observer": importlib.import_module(
                "nonebot_plugin_suda_electricity.scheduler.observer"
            ),
            "service": importlib.import_module(
                "nonebot_plugin_suda_electricity.scheduler.service"
            ),
            "schedule": importlib.import_module(
                "nonebot_plugin_suda_electricity.scheduler.schedule"
            ),
            "crypto": importlib.import_module(
                "nonebot_plugin_suda_electricity.crypto"
            ),
            "suda_models": importlib.import_module(
                "nonebot_plugin_suda_electricity.suda.models"
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


@pytest.mark.asyncio
async def test_compute_next_check_uses_interval_hours(modules) -> None:
    next_check_at = modules["schedule"].compute_next_check_at(
        from_timestamp=1_000,
        interval_hours=2,
    )

    assert next_check_at == 1_000 + 2 * 60 * 60


@pytest.mark.asyncio
async def test_scheduler_dispatches_due_dormitory_with_decrypted_user(
    modules,
    repository_bundle,
) -> None:
    dormitory_repo, user_repo = repository_bundle
    observer_module = modules["observer"]
    service_module = modules["service"]
    config_module = modules["config"]

    profile = SimpleNamespace(
        user_type="student",
        dorm_room_id="DR-1001",
        campus_code="01",
        building_code="02",
        room_code="0301",
        campus_name="Main Campus",
        building_name="Building 2",
        room_name="Room 301",
        balance="18.20",
    )

    await user_repo.bind(
        dormitory=profile,
        user_name="测试用户",
        password="P@ss+中/=",
        user_id="owner-1",
        subscribe_type="group",
        subscribe_id="20001",
        initial_next_check_at=900,
    )

    events = []
    registry = observer_module.DormitoryScheduleObserverRegistry()

    @registry.register
    async def _observer(event):
        events.append(event)

    service = service_module.DormitorySchedulerService(
        dormitory_repository=dormitory_repo,
        observer_registry=registry,
        config=config_module.Config(
            suda_secret_key="test-secret-key-32-chars-min!",
            scheduler_interval_hours=2,
            scheduler_tick_seconds=60,
            scheduler_due_limit=10,
        ),
        now_provider=lambda: 1_000,
    )

    report = await service.run_once()

    assert report.checked_dormitories == 1
    assert report.dispatched_events == 1
    assert report.observer_calls == 1
    assert report.observer_failures == 0
    assert len(events) == 1
    assert events[0].dormitory.users[0].user_name == "测试用户"
    assert events[0].dormitory.users[0].password == "P@ss+中/="

    updated = await dormitory_repo.get(events[0].dormitory.dormitory.dormitory_key)
    assert updated is not None
    assert updated.last_check_at == 1_000
    assert updated.next_check_at == 1_000 + 2 * 60 * 60


@pytest.mark.asyncio
async def test_scheduler_skips_when_no_due_dormitory(modules, repository_bundle) -> None:
    dormitory_repo, user_repo = repository_bundle
    observer_module = modules["observer"]
    service_module = modules["service"]
    config_module = modules["config"]

    profile = SimpleNamespace(
        user_type="student",
        dorm_room_id="DR-2002",
        campus_code="01",
        building_code="08",
        room_code="0602",
        campus_name="Main Campus",
        building_name="Building 8",
        room_name="Room 602",
        balance="50.00",
    )

    await user_repo.bind(
        dormitory=profile,
        user_name="alice",
        password="pass-1",
        user_id="owner-1",
        subscribe_type="private",
        subscribe_id="owner-1",
        initial_next_check_at=5_000,
    )

    service = service_module.DormitorySchedulerService(
        dormitory_repository=dormitory_repo,
        observer_registry=observer_module.DormitoryScheduleObserverRegistry(),
        config=config_module.Config(
            suda_secret_key="test-secret-key-32-chars-min!",
            scheduler_interval_hours=2,
            scheduler_tick_seconds=60,
            scheduler_due_limit=10,
        ),
        now_provider=lambda: 1_000,
    )

    report = await service.run_once()

    assert report.checked_dormitories == 0
    assert report.dispatched_events == 0
