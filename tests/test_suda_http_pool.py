import asyncio
import importlib
import importlib.machinery
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_MODULES = None
_PACKAGE_ROOT = Path.cwd() / "src" / "nonebot_plugin_suda_electricity"


def _purge_modules() -> None:
    for name in list(sys.modules):
        if name == "nonebot_plugin_suda_electricity" or name.startswith(
            "nonebot_plugin_suda_electricity."
        ):
            sys.modules.pop(name, None)


def _load_suda_modules():
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

    _purge_modules()

    with patch.dict(
        sys.modules,
        {
            "nonebot": fake_nonebot,
            "nonebot_plugin_localstore": fake_localstore,
            "nonebot_plugin_suda_electricity": fake_package,
        },
    ):
        http_pool_module = importlib.import_module(
            "nonebot_plugin_suda_electricity.suda.http_pool"
        )
        service_module = importlib.import_module(
            "nonebot_plugin_suda_electricity.suda.service"
        )
        _MODULES = {
            "http_pool_module": http_pool_module,
            "HttpClientPool": http_pool_module.HttpClientPool,
            "SudaElectricityService": service_module.SudaElectricityService,
        }

    return _MODULES


@pytest.fixture(autouse=True)
def _cleanup_module_state():
    global _MODULES
    _MODULES = None
    yield
    _purge_modules()
    _MODULES = None


@pytest.fixture
def suda_modules():
    return _load_suda_modules()


class _FakeAsyncClient:
    def __init__(self, user_agent: str) -> None:
        self.headers = {"User-Agent": user_agent}
        self.cookies = SimpleNamespace(clear=lambda: None)
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    async def get(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("unexpected network GET in http pool tests")

    async def post(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("unexpected network POST in http pool tests")


def _set_user_agents(
    monkeypatch: pytest.MonkeyPatch,
    http_pool_module,
    values: list[str],
) -> None:
    user_agents = iter(values)
    monkeypatch.setattr(http_pool_module, "build_user_agent", lambda: next(user_agents))


def _mock_http_clients(monkeypatch: pytest.MonkeyPatch, http_pool_module) -> None:
    monkeypatch.setattr(
        http_pool_module,
        "build_http_client",
        lambda timeout: _FakeAsyncClient(user_agent=http_pool_module.build_user_agent()),
    )


@pytest.mark.asyncio
async def test_http_client_pool_reuses_client_and_refreshes_user_agent(
    monkeypatch: pytest.MonkeyPatch,
    suda_modules,
) -> None:
    http_pool_module = suda_modules["http_pool_module"]
    HttpClientPool = suda_modules["HttpClientPool"]

    _set_user_agents(monkeypatch, http_pool_module, ["UA-1", "UA-2", "UA-3"])
    _mock_http_clients(monkeypatch, http_pool_module)

    pool = HttpClientPool(timeout=5, max_size=1)
    try:
        lease1 = await pool.acquire()
        client1 = lease1.client
        ua1 = client1.headers["User-Agent"]
        await lease1.release()

        lease2 = await pool.acquire()
        client2 = lease2.client
        ua2 = client2.headers["User-Agent"]
        await lease2.release()
    finally:
        await pool.aclose()

    assert client1 is client2
    assert ua1 == "UA-1"
    assert ua2 == "UA-2"


@pytest.mark.asyncio
async def test_http_client_pool_rejects_new_acquires_after_close(
    monkeypatch: pytest.MonkeyPatch,
    suda_modules,
) -> None:
    http_pool_module = suda_modules["http_pool_module"]
    HttpClientPool = suda_modules["HttpClientPool"]

    _mock_http_clients(monkeypatch, http_pool_module)
    pool = HttpClientPool(timeout=5, max_size=1)
    await pool.aclose()

    with pytest.raises(RuntimeError, match="closed"):
        await pool.acquire()


@pytest.mark.asyncio
async def test_http_client_pool_close_wakes_waiting_acquire(
    monkeypatch: pytest.MonkeyPatch,
    suda_modules,
) -> None:
    http_pool_module = suda_modules["http_pool_module"]
    HttpClientPool = suda_modules["HttpClientPool"]

    _mock_http_clients(monkeypatch, http_pool_module)
    pool = HttpClientPool(timeout=5, max_size=1)
    try:
        lease = await pool.acquire()
        waiter = asyncio.create_task(pool.acquire())
        await asyncio.sleep(0)

        await pool.aclose()
        await lease.release()

        with pytest.raises(RuntimeError, match="closed"):
            await asyncio.wait_for(waiter, timeout=1)
    finally:
        await pool.aclose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_service_pipeline_uses_one_client_for_cas_and_gateway(
    monkeypatch: pytest.MonkeyPatch,
    suda_modules,
) -> None:
    http_pool_module = suda_modules["http_pool_module"]
    SudaElectricityService = suda_modules["SudaElectricityService"]

    _set_user_agents(monkeypatch, http_pool_module, ["PIPE-UA-1", "PIPE-UA-2", "PIPE-UA-3"])
    _mock_http_clients(monkeypatch, http_pool_module)

    service = SudaElectricityService(timeout=5, max_pool_size=1)
    try:
        cas_client, gateway = await service._restart_pipeline()
        first_client = service.active_client
        first_ua = service.active_user_agent

        assert first_client is not None
        assert cas_client._client is gateway._client is first_client
        assert first_ua == "PIPE-UA-1"

        await service._release_pipeline()

        cas_client, gateway = await service._restart_pipeline()
        second_client = service.active_client
        second_ua = service.active_user_agent

        assert second_client is not None
        assert cas_client._client is gateway._client is second_client
        assert first_client is second_client
        assert second_ua == "PIPE-UA-2"
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_service_waiting_pipeline_unblocks_when_pool_closes(
    monkeypatch: pytest.MonkeyPatch,
    suda_modules,
) -> None:
    http_pool_module = suda_modules["http_pool_module"]
    HttpClientPool = suda_modules["HttpClientPool"]
    SudaElectricityService = suda_modules["SudaElectricityService"]

    _mock_http_clients(monkeypatch, http_pool_module)
    pool = HttpClientPool(timeout=5, max_size=1)
    service1 = SudaElectricityService(timeout=5, client_pool=pool, max_pool_size=1)
    service2 = SudaElectricityService(timeout=5, client_pool=pool, max_pool_size=1)

    try:
        await service1._restart_pipeline()
        pending_restart = asyncio.create_task(service2._restart_pipeline())
        await asyncio.sleep(0)

        await pool.aclose()
        await service1._release_pipeline()

        with pytest.raises(RuntimeError, match="closed"):
            await asyncio.wait_for(pending_restart, timeout=1)
    finally:
        await service1.aclose()
        await service2.aclose()
        await pool.aclose()


@pytest.mark.property
@settings(
    deadline=None,
    max_examples=20,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    max_size=st.integers(min_value=1, max_value=4),
    waiter_count=st.integers(min_value=1, max_value=6),
)
def test_http_client_pool_close_wakes_all_waiters_property(
    max_size: int,
    waiter_count: int,
    suda_modules,
) -> None:
    http_pool_module = suda_modules["http_pool_module"]
    HttpClientPool = suda_modules["HttpClientPool"]

    async def scenario() -> None:
        with patch.object(
            http_pool_module,
            "build_http_client",
            lambda timeout: _FakeAsyncClient(user_agent=http_pool_module.build_user_agent()),
        ):
            pool = HttpClientPool(timeout=5, max_size=max_size)
            leases = [await pool.acquire() for _ in range(max_size)]
            waiters = [asyncio.create_task(pool.acquire()) for _ in range(waiter_count)]
            await asyncio.sleep(0)

            await pool.aclose()
            for lease in leases:
                await lease.release()

            results = await asyncio.wait_for(
                asyncio.gather(*waiters, return_exceptions=True),
                timeout=1,
            )

            assert all(
                isinstance(result, RuntimeError) and "closed" in str(result)
                for result in results
            )

    asyncio.run(scenario())
