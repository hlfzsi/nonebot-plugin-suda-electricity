import asyncio
import importlib
import importlib.machinery
import logging
import sys
import types
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path.cwd() / "src" / "nonebot_plugin_suda_electricity"


def _purge_modules() -> None:
    for name in list(sys.modules):
        if name == "nonebot_plugin_suda_electricity" or name.startswith(
            "nonebot_plugin_suda_electricity."
        ):
            sys.modules.pop(name, None)


def _load_modules(db_dir: Path):
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
    fake_utils.BASE_DATA_DIR = db_dir.parent
    fake_utils.DATABASE_DATA_DIR = db_dir
    fake_utils.logger = logging.getLogger("test-utils")

    fake_db_package = types.ModuleType("nonebot_plugin_suda_electricity.db")
    fake_db_package.__path__ = [str(_PACKAGE_ROOT / "db")]
    fake_db_package.__spec__ = importlib.machinery.ModuleSpec(
        "nonebot_plugin_suda_electricity.db",
        loader=None,
        is_package=True,
    )
    fake_db_package.__spec__.submodule_search_locations = [str(_PACKAGE_ROOT / "db")]

    fake_db_models_package = types.ModuleType("nonebot_plugin_suda_electricity.db.models")
    fake_db_models_package.__path__ = [str(_PACKAGE_ROOT / "db" / "models")]
    fake_db_models_package.__spec__ = importlib.machinery.ModuleSpec(
        "nonebot_plugin_suda_electricity.db.models",
        loader=None,
        is_package=True,
    )
    fake_db_models_package.__spec__.submodule_search_locations = [
        str(_PACKAGE_ROOT / "db" / "models")
    ]

    _purge_modules()

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "nonebot_plugin_suda_electricity", fake_package)
        mp.setitem(sys.modules, "nonebot_plugin_suda_electricity.utils", fake_utils)
        mp.setitem(sys.modules, "nonebot_plugin_suda_electricity.db", fake_db_package)
        mp.setitem(
            sys.modules,
            "nonebot_plugin_suda_electricity.db.models",
            fake_db_models_package,
        )
        engine_module = importlib.import_module("nonebot_plugin_suda_electricity.db.engine")
        lifecycle_module = importlib.import_module(
            "nonebot_plugin_suda_electricity.db.lifecycle"
        )
        return engine_module, lifecycle_module


@pytest.fixture
def db_modules(tmp_path):
    engine_module, lifecycle_module = _load_modules(tmp_path / "db")
    yield engine_module, lifecycle_module
    _purge_modules()


@pytest.mark.asyncio
async def test_lock_acquire_and_release_when_enabled(db_modules) -> None:
    engine_module, _ = db_modules
    lock = engine_module._Lock(enabled=True)

    await lock.__aenter__()
    assert lock._async_lock.locked()

    await lock.__aexit__(None, None, None)
    assert not lock._async_lock.locked()


@pytest.mark.asyncio
async def test_lock_exit_without_acquire_is_safe(db_modules) -> None:
    engine_module, _ = db_modules
    lock = engine_module._Lock(enabled=True)

    await lock.__aexit__(None, None, None)

    assert not lock._async_lock.locked()


def test_set_sqlite_pragma_enables_write_lock_and_sets_pragmas(db_modules, monkeypatch) -> None:
    engine_module, _ = db_modules

    executed = []

    class FakeCursor:
        def execute(self, sql):
            executed.append(sql)

        def close(self):
            return None

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(
        engine_module,
        "ENGINE",
        types.SimpleNamespace(dialect=types.SimpleNamespace(name="sqlite")),
    )
    engine_module.WRITE_LOCK._enabled = False

    engine_module.set_sqlite_pragma(FakeConnection(), None)

    assert engine_module.WRITE_LOCK._enabled is True
    assert executed == ["PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL"]


def test_set_sqlite_pragma_skips_non_sqlite(db_modules, monkeypatch) -> None:
    engine_module, _ = db_modules

    class FakeConnection:
        def __init__(self):
            self.called = False

        def cursor(self):
            self.called = True
            raise AssertionError("cursor should not be called for non-sqlite")

    connection = FakeConnection()
    monkeypatch.setattr(
        engine_module,
        "ENGINE",
        types.SimpleNamespace(dialect=types.SimpleNamespace(name="postgresql")),
    )

    engine_module.set_sqlite_pragma(connection, None)
    assert connection.called is False


@pytest.mark.asyncio
async def test_lifecycle_init_and_shutdown_call_engine_hooks(db_modules, monkeypatch) -> None:
    _, lifecycle_module = db_modules

    called = {"create_all": False, "dispose": False}

    class FakeConn:
        async def run_sync(self, fn):
            called["create_all"] = fn is fake_create_all

    class FakeBegin:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return None

    async def fake_dispose():
        called["dispose"] = True

    def fake_create_all(_):
        return None

    fake_engine = types.SimpleNamespace(begin=lambda: FakeBegin(), dispose=fake_dispose)
    fake_metadata = types.SimpleNamespace(create_all=fake_create_all)

    monkeypatch.setattr(lifecycle_module, "ENGINE", fake_engine)
    monkeypatch.setattr(lifecycle_module, "METADATA", fake_metadata)

    await lifecycle_module.init_db()
    await lifecycle_module.shutdown_db()

    assert called["create_all"] is True
    assert called["dispose"] is True
