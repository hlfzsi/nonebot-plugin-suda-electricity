import asyncio
import importlib
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel

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


def _load_scheduler_modules():
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

    fake_utils = types.ModuleType("nonebot_plugin_suda_electricity.utils")
    fake_utils.APP_CONFIG = types.SimpleNamespace(suda_database_url="")
    fake_utils.BASE_DATA_DIR = Path.cwd() / ".pytest-localstore"
    fake_utils.DATABASE_DATA_DIR = Path.cwd() / ".pytest-localstore"
    fake_utils.logger = logging.getLogger("test-utils")

    _purge_modules()

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "nonebot", fake_nonebot)
        mp.setitem(sys.modules, "nonebot_plugin_localstore", fake_localstore)
        mp.setitem(sys.modules, "nonebot_plugin_suda_electricity", fake_package)
        mp.setitem(sys.modules, "nonebot_plugin_suda_electricity.utils", fake_utils)
        return {
            "base_repo": importlib.import_module(
                "nonebot_plugin_suda_electricity.db.repositories.base"
            ),
            "dormitory_repo": importlib.import_module(
                "nonebot_plugin_suda_electricity.db.repositories.dormitory"
            ),
            "user_repo": importlib.import_module(
                "nonebot_plugin_suda_electricity.db.repositories.user"
            ),
            "observer": importlib.import_module(
                "nonebot_plugin_suda_electricity.scheduler.observer"
            ),
            "schedule": importlib.import_module(
                "nonebot_plugin_suda_electricity.scheduler.schedule"
            ),
            "service": importlib.import_module(
                "nonebot_plugin_suda_electricity.scheduler.service"
            ),
            "scheduler": importlib.import_module(
                "nonebot_plugin_suda_electricity.scheduler"
            ),
        }


@pytest.fixture
def scheduler_modules():
    SQLModel.metadata.clear()
    modules = _load_scheduler_modules()
    yield modules
    _purge_modules()
    SQLModel.metadata.clear()


@pytest.mark.asyncio
async def test_upsert_dormitory_uses_initial_next_check_at(
    scheduler_modules,
    monkeypatch,
) -> None:
    user_repo_module = scheduler_modules["user_repo"]
    captured = {}

    class FakeSession:
        def __init__(self) -> None:
            self.added = None

        async def get(self, model, dormitory_key):
            del model, dormitory_key
            return None

        def add(self, value):
            self.added = value

        async def flush(self):
            return None

    fake_session = FakeSession()
    fake_record = SimpleNamespace()

    def _fake_from_profile(
        cls,
        dormitory,
        *,
        balance_threshold,
        next_check_at,
    ):
        captured["dormitory"] = dormitory
        captured["balance_threshold"] = balance_threshold
        captured["next_check_at"] = next_check_at
        return fake_record

    monkeypatch.setattr(
        user_repo_module.DormitoryTable,
        "from_profile",
        classmethod(_fake_from_profile),
    )

    repo = user_repo_module.DormitoryUserRepository()
    profile = SimpleNamespace(
        user_type="student",
        dorm_room_id="DR-1001",
        campus_code="01",
        building_code="02",
        room_code="0301",
        campus_name="Main Campus",
        building_name="Building 2",
        room_name="Room 301",
    )

    result = await repo._upsert_dormitory(
        fake_session,
        dormitory=profile,
        dormitory_key="01:02:0301",
        balance_threshold=user_repo_module.UNSET,
        initial_next_check_at=2_000,
    )

    assert result is fake_record
    assert fake_session.added is fake_record
    assert captured["next_check_at"] == 2_000


@pytest.mark.asyncio
async def test_scheduler_service_reschedules_due_dormitory_and_notifies_observer(
    scheduler_modules,
) -> None:
    scheduler_module = scheduler_modules["scheduler"]
    events = []

    detail = SimpleNamespace(
        dormitory=SimpleNamespace(dormitory_key="01:02:0301"),
        users=[],
    )

    class FakeRepo:
        def __init__(self) -> None:
            self.updates = []

        async def list_due_details(self, *, now: int, limit: int):
            assert now == 1_000
            assert limit == 10
            return [detail]

        async def update_check_schedule(
            self,
            *,
            dormitory_key: str,
            last_check_at: int,
            next_check_at: int,
        ):
            self.updates.append(
                {
                    "dormitory_key": dormitory_key,
                    "last_check_at": last_check_at,
                    "next_check_at": next_check_at,
                }
            )
            return SimpleNamespace(
                dormitory_key=dormitory_key,
                last_check_at=last_check_at,
                next_check_at=next_check_at,
            )

    observer_registry = scheduler_module.DormitoryScheduleObserverRegistry()

    @observer_registry.register
    async def _observer(event):
        events.append(event)

    service = scheduler_module.DormitorySchedulerService(
        dormitory_repository=FakeRepo(),
        observer_registry=observer_registry,
        config=SimpleNamespace(
            suda_scheduler_interval_hours=8,
            suda_scheduler_tick_seconds=60,
            suda_scheduler_due_limit=10,
        ),
        now_provider=lambda: 1_000,
    )

    report = await service.run_once()

    assert report.checked_dormitories == 1
    assert report.dispatched_events == 1
    assert report.observer_calls == 1
    assert report.observer_failures == 0
    assert len(events) == 1
    assert events[0].dispatched_at == 1_000
    assert events[0].next_check_at == 1_000 + 8 * 60 * 60


@pytest.mark.asyncio
async def test_scheduler_service_start_stop_and_is_running(scheduler_modules) -> None:
    scheduler_module = scheduler_modules["service"]

    class FakeRepo:
        async def list_due_details(self, *, now: int, limit: int):
            del now, limit
            return []

        async def update_check_schedule(self, **kwargs):
            del kwargs
            return None

    service = scheduler_module.DormitorySchedulerService(
        dormitory_repository=FakeRepo(),
        observer_registry=scheduler_modules["scheduler"].DormitoryScheduleObserverRegistry(),
        config=SimpleNamespace(
            suda_scheduler_interval_hours=8,
            suda_scheduler_tick_seconds=0.01,
            suda_scheduler_due_limit=10,
        ),
        now_provider=lambda: 1_000,
    )

    assert service.is_running is False
    await service.start()
    await asyncio.sleep(0)
    assert service.is_running is True
    await service.stop()
    assert service.is_running is False


@pytest.mark.asyncio
async def test_scheduler_service_run_forever_handles_errors_and_timeouts(
    scheduler_modules,
    monkeypatch,
) -> None:
    service_module = scheduler_modules["service"]

    class FakeRepo:
        async def list_due_details(self, *, now: int, limit: int):
            del now, limit
            return []

        async def update_check_schedule(self, **kwargs):
            del kwargs
            return None

    service = service_module.DormitorySchedulerService(
        dormitory_repository=FakeRepo(),
        observer_registry=scheduler_modules["scheduler"].DormitoryScheduleObserverRegistry(),
        config=SimpleNamespace(
            suda_scheduler_interval_hours=8,
            suda_scheduler_tick_seconds=0.01,
            suda_scheduler_due_limit=10,
        ),
        now_provider=lambda: 1_000,
    )

    calls = {"run_once": 0, "logger": 0, "wait_for": 0}

    async def _run_once():
        calls["run_once"] += 1
        if calls["run_once"] == 1:
            raise RuntimeError("boom")
        service._stop_event.set()

    async def _wait_for(awaitable, timeout):
        del timeout
        awaitable.close()
        calls["wait_for"] += 1
        service._stop_event.set()
        raise TimeoutError

    monkeypatch.setattr(service, "run_once", _run_once)
    monkeypatch.setattr(service_module.asyncio, "wait_for", _wait_for)
    monkeypatch.setattr(
        service_module.logger,
        "exception",
        lambda *_args, **_kwargs: calls.__setitem__("logger", calls["logger"] + 1),
    )

    await service._run_forever()

    assert calls["run_once"] >= 1
    assert calls["logger"] == 1
    assert calls["wait_for"] == 1


@pytest.mark.asyncio
async def test_scheduler_service_skips_when_lock_is_held(scheduler_modules) -> None:
    scheduler_module = scheduler_modules["scheduler"]

    class LockedRepo:
        async def list_due_details(self, *, now: int, limit: int):
            del now, limit
            return []

        async def update_check_schedule(self, **kwargs):
            del kwargs
            return None

    service = scheduler_module.DormitorySchedulerService(
        dormitory_repository=LockedRepo(),
        observer_registry=scheduler_module.DormitoryScheduleObserverRegistry(),
        config=SimpleNamespace(
            suda_scheduler_interval_hours=8,
            suda_scheduler_tick_seconds=60,
            suda_scheduler_due_limit=10,
        ),
        now_provider=lambda: 1_000,
    )

    await service._run_lock.acquire()
    try:
        report = await service.run_once()
    finally:
        service._run_lock.release()

    assert report.skipped is True


@pytest.mark.asyncio
async def test_scheduler_service_handles_update_failures_and_observer_errors(
    scheduler_modules,
) -> None:
    scheduler_module = scheduler_modules["scheduler"]
    detail = SimpleNamespace(
        dormitory=SimpleNamespace(dormitory_key="01:02:0301"),
        users=[],
    )

    class FakeRepo:
        async def list_due_details(self, *, now: int, limit: int):
            del now, limit
            return [detail]

        async def update_check_schedule(self, **kwargs):
            del kwargs
            return None

    registry = scheduler_module.DormitoryScheduleObserverRegistry()

    @registry.register
    async def _observer(event):
        del event
        raise RuntimeError("boom")

    service = scheduler_module.DormitorySchedulerService(
        dormitory_repository=FakeRepo(),
        observer_registry=registry,
        config=SimpleNamespace(
            suda_scheduler_interval_hours=8,
            suda_scheduler_tick_seconds=60,
            suda_scheduler_due_limit=10,
        ),
        now_provider=lambda: 1_000,
    )

    report = await service.run_once()

    assert report.checked_dormitories == 1
    assert report.dispatched_events == 0
    assert report.observer_calls == 0
    assert report.observer_failures == 0


def test_compute_next_check_at_rejects_non_positive_interval(scheduler_modules) -> None:
    scheduler_module = scheduler_modules["scheduler"]

    with pytest.raises(ValueError, match="greater than 0"):
        scheduler_module.compute_next_check_at(from_timestamp=1_000, interval_hours=0)


def test_compute_initial_check_at_uses_config_when_missing(scheduler_modules, monkeypatch) -> None:
    schedule_module = scheduler_modules["schedule"]
    monkeypatch.setattr(
        schedule_module,
        "APP_CONFIG",
        SimpleNamespace(suda_scheduler_interval_hours=5),
    )

    assert schedule_module.compute_initial_check_at(now=1_000) == 1_000 + 5 * 60 * 60


def test_compute_initial_check_at_defaults_to_eight_hours(scheduler_modules) -> None:
    scheduler_module = scheduler_modules["scheduler"]

    next_check_at = scheduler_module.compute_initial_check_at(
        now=1_000,
        interval_hours=8,
    )

    assert next_check_at == 1_000 + 8 * 60 * 60
