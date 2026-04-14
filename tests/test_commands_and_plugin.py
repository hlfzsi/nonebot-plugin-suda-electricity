import importlib
import importlib.machinery
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_PACKAGE_ROOT = Path.cwd() / "src" / "nonebot_plugin_suda_electricity"


class _FakeMatcher:
    def __init__(self) -> None:
        self.messages = []
        self.handler = None

    def handle(self):
        def _decorator(func):
            self.handler = func
            return func

        return _decorator

    async def send(self, message: str) -> None:
        self.messages.append(message)

    async def finish(self, message: str) -> None:
        self.messages.append(message)


class _FakeArgs:
    def __getitem__(self, item):
        del item
        return self

    @classmethod
    def __class_getitem__(cls, item):
        del item
        return cls()


class _FakeMatch:
    @classmethod
    def __class_getitem__(cls, item):
        del item
        return cls


class _FakeTarget:
    def __init__(self, id: str, private: bool):
        self.id = id
        self.private = private


class _FakeUniMessage:
    last_instance = None

    def __init__(self):
        self.parts = []
        self.sent_target = None
        _FakeUniMessage.last_instance = self

    def at(self, user_id: str):
        self.parts.append(("at", user_id))
        return self

    def text(self, value: str):
        self.parts.append(("text", value))
        return self

    async def send(self, target=None):
        self.sent_target = target


class _CaptchaRequiredError(Exception):
    pass


class _FakeObserverRegistry:
    def __init__(self):
        self.observers = []

    def register(self, observer):
        self.observers.append(observer)
        return observer


def _purge_modules() -> None:
    for name in list(sys.modules):
        if name == "nonebot_plugin_suda_electricity" or name.startswith(
            "nonebot_plugin_suda_electricity."
        ):
            sys.modules.pop(name, None)


def _build_fake_alconna_module():
    fake = types.ModuleType("nonebot_plugin_alconna")
    fake.Args = _FakeArgs
    fake.Match = _FakeMatch
    fake.Alconna = lambda *args, **kwargs: (args, kwargs)
    fake.AlconnaMatch = lambda name: name
    fake.UniMessage = _FakeUniMessage
    fake.Target = _FakeTarget
    fake.on_alconna = lambda *args, **kwargs: _FakeMatcher()
    return fake


def _load_command_module(module_name: str, custom_modules: dict | None = None):
    fake_package = types.ModuleType("nonebot_plugin_suda_electricity")
    fake_package.__path__ = [str(_PACKAGE_ROOT)]
    fake_package.__spec__ = importlib.machinery.ModuleSpec(
        "nonebot_plugin_suda_electricity", loader=None, is_package=True
    )
    fake_package.__spec__.submodule_search_locations = [str(_PACKAGE_ROOT)]

    fake_commands_package = types.ModuleType("nonebot_plugin_suda_electricity.commands")
    fake_commands_package.__path__ = [str(_PACKAGE_ROOT / "commands")]
    fake_commands_package.__spec__ = importlib.machinery.ModuleSpec(
        "nonebot_plugin_suda_electricity.commands", loader=None, is_package=True
    )
    fake_commands_package.__spec__.submodule_search_locations = [
        str(_PACKAGE_ROOT / "commands")
    ]

    fake_uninfo = types.ModuleType("nonebot_plugin_uninfo")
    fake_uninfo.Uninfo = object
    fake_uninfo.Session = object

    logger = logging.getLogger("test-commands")

    db_module = types.ModuleType("nonebot_plugin_suda_electricity.db")
    db_module.dormitory_user_repo = SimpleNamespace()
    db_module.dormitory_repo = SimpleNamespace()
    db_module.DormitoryUser = object

    scheduler_module = types.ModuleType("nonebot_plugin_suda_electricity.scheduler")
    scheduler_module.compute_initial_check_at = lambda: 123
    scheduler_module.DormitoryCheckDueEvent = object
    scheduler_module.scheduler_observer_registry = _FakeObserverRegistry()

    suda_module = types.ModuleType("nonebot_plugin_suda_electricity.suda")
    suda_module.query_electricity = None
    suda_module.CaptchaRequiredError = _CaptchaRequiredError
    suda_module.ElectricityQueryResult = object

    utils_module = types.ModuleType("nonebot_plugin_suda_electricity.utils")
    utils_module.extract_session_info = lambda session: {
        "user_id": session.user.id,
        "group_id": None,
    }
    utils_module.logger = logger
    utils_module.BASE_DATA_DIR = Path.cwd()

    modules = {
        "nonebot_plugin_alconna": _build_fake_alconna_module(),
        "nonebot_plugin_uninfo": fake_uninfo,
        "nonebot_plugin_suda_electricity": fake_package,
        "nonebot_plugin_suda_electricity.commands": fake_commands_package,
        "nonebot_plugin_suda_electricity.db": db_module,
        "nonebot_plugin_suda_electricity.scheduler": scheduler_module,
        "nonebot_plugin_suda_electricity.suda": suda_module,
        "nonebot_plugin_suda_electricity.utils": utils_module,
    }
    if custom_modules:
        modules.update(custom_modules)

    _purge_modules()
    with pytest.MonkeyPatch.context() as mp:
        for key, value in modules.items():
            mp.setitem(sys.modules, key, value)

        if module_name.endswith(".commands.__init__"):
            return importlib.import_module(module_name)

        short_name = module_name.rsplit(".", 1)[-1]
        file_path = _PACKAGE_ROOT / "commands" / f"{short_name}.py"
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module


@pytest.fixture(autouse=True)
def _cleanup_modules_after_test():
    yield
    _purge_modules()


@pytest.mark.asyncio
async def test_bind_handler_success_and_fail_branches() -> None:
    module = _load_command_module("nonebot_plugin_suda_electricity.commands.bind")
    matcher = module.bind_matcher

    session = SimpleNamespace(user=SimpleNamespace(id="u1"))
    user_name = SimpleNamespace(result="alice")
    password = SimpleNamespace(result="pass")

    async def _ok_query(u, p):
        assert u == "alice"
        assert p == "pass"
        return SimpleNamespace(dormitory=SimpleNamespace(), dormitory_key="k")

    bound = {}

    async def _bind(**kwargs):
        bound.update(kwargs)

    module.query_electricity = _ok_query
    module.dormitory_user_repo = SimpleNamespace(bind=_bind)
    module.extract_session_info = lambda s: {"user_id": s.user.id, "group_id": "g1"}
    module.format_electricity_query_result = lambda result: "OK"

    await module.handle_bind(session, user_name=user_name, password=password)

    assert bound["user_id"] == "u1"
    assert bound["subscribe_type"] == "group"
    assert bound["subscribe_id"] == "g1"
    assert matcher.messages[-1].startswith("绑定成功")

    matcher.messages.clear()
    await module.handle_bind(
        session,
        user_name=SimpleNamespace(result=None),
        password=SimpleNamespace(result="x"),
    )
    assert "请提供用户名和密码" in matcher.messages[-1]

    matcher.messages.clear()

    async def _captcha(*args, **kwargs):
        del args, kwargs
        raise module.CaptchaRequiredError()

    module.query_electricity = _captcha
    await module.handle_bind(session, user_name=user_name, password=password)
    assert "可能需要验证码" in matcher.messages[-1]

    matcher.messages.clear()

    async def _boom(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("boom")

    module.query_electricity = _boom
    await module.handle_bind(session, user_name=user_name, password=password)
    assert "登录失败，发生错误" in matcher.messages[-1]


@pytest.mark.asyncio
async def test_check_handler_branches() -> None:
    module = _load_command_module("nonebot_plugin_suda_electricity.commands.check")
    matcher = module.check_matcher
    session = SimpleNamespace(user=SimpleNamespace(id="u1"))

    async def _no_user(_uid):
        return None

    module.dormitory_user_repo = SimpleNamespace(get_by_user_id=_no_user)
    await module.handle_check(session)
    assert "还没有绑定宿舍" in matcher.messages[-1]

    async def _get_user(_uid):
        return SimpleNamespace(user_name="alice", password="pass", dormitory_key="dk", user_id="u1")

    module.dormitory_user_repo = SimpleNamespace(get_by_user_id=_get_user)

    async def _captcha(*args, **kwargs):
        del args, kwargs
        raise module.CaptchaRequiredError()

    module.query_electricity = _captcha
    matcher.messages.clear()
    await module.handle_check(session)
    assert "可能需要验证码" in matcher.messages[-1]

    async def _boom(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("err")

    module.query_electricity = _boom
    matcher.messages.clear()
    await module.handle_check(session)
    assert "登录失败，发生错误" in matcher.messages[-1]

    async def _ok(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(dormitory=SimpleNamespace(balance="9.5"))

    module.query_electricity = _ok
    async def _no_dormitory(_k):
        return None

    module.dormitory_repo = SimpleNamespace(get=_no_dormitory)
    matcher.messages.clear()
    await module.handle_check(session)
    assert "无法获取宿舍信息" in matcher.messages[-1]

    called = {"alert": 0}

    async def _alert(result, user):
        del result, user
        called["alert"] += 1

    module.send_low_balance_alert = _alert
    async def _has_dormitory(_k):
        return SimpleNamespace(balance_threshold=10.0)

    module.dormitory_repo = SimpleNamespace(get=_has_dormitory)
    matcher.messages.clear()
    await module.handle_check(session)
    assert called["alert"] == 1

    async def _ok_high(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(dormitory=SimpleNamespace(balance="20"))

    module.query_electricity = _ok_high
    module.format_electricity_query_result = lambda _r: "NORMAL"
    matcher.messages.clear()
    await module.handle_check(session)
    assert matcher.messages[-1] == "NORMAL"


@pytest.mark.asyncio
async def test_subscribe_threshold_unbind_handlers() -> None:
    sub = _load_command_module("nonebot_plugin_suda_electricity.commands.subscribe")
    thr = _load_command_module("nonebot_plugin_suda_electricity.commands.threshold")
    unb = _load_command_module("nonebot_plugin_suda_electricity.commands.unbind")

    session = SimpleNamespace(user=SimpleNamespace(id="u1"))

    async def _sub_no_user(_uid):
        return None

    sub.dormitory_user_repo = SimpleNamespace(get_by_user_id=_sub_no_user)
    await sub.handle_subscribe(session)
    assert "还没有绑定学号" in sub.subscribe_matcher.messages[-1]

    async def _get_user(_uid):
        return SimpleNamespace(user_name="alice")

    async def _update_fail(**kwargs):
        del kwargs
        return None

    sub.dormitory_user_repo = SimpleNamespace(
        get_by_user_id=_get_user,
        update_subscription_target=_update_fail,
    )
    sub.extract_session_info = lambda _s: {"user_id": "u1", "group_id": None}
    sub.subscribe_matcher.messages.clear()
    await sub.handle_subscribe(session)
    assert "更新订阅目标失败" in sub.subscribe_matcher.messages[-1]

    async def _update_ok(**kwargs):
        del kwargs
        return object()

    sub.dormitory_user_repo = SimpleNamespace(
        get_by_user_id=_get_user,
        update_subscription_target=_update_ok,
    )
    sub.subscribe_matcher.messages.clear()
    await sub.handle_subscribe(session)
    assert "订阅成功" in sub.subscribe_matcher.messages[-1]

    thr.threshold_matcher.messages.clear()
    await thr.handle_threshold(session, balance_threshold=SimpleNamespace(result=None))
    assert "请提供电费阈值" in thr.threshold_matcher.messages[-1]

    thr.threshold_matcher.messages.clear()
    await thr.handle_threshold(session, balance_threshold=SimpleNamespace(result="bad"))
    assert "必须是一个数字" in thr.threshold_matcher.messages[-1]

    async def _get_binding_none(_uid):
        return None

    thr.dormitory_user_repo = SimpleNamespace(get_binding_by_user_id=_get_binding_none)
    thr.threshold_matcher.messages.clear()
    await thr.handle_threshold(session, balance_threshold=SimpleNamespace(result="10"))
    assert "还没有绑定学号" in thr.threshold_matcher.messages[-1]

    async def _get_binding(_uid):
        return (SimpleNamespace(dormitory_key="dk"), object())

    async def _raise_value_error(**kwargs):
        del kwargs
        raise ValueError("bad")

    thr.dormitory_user_repo = SimpleNamespace(get_binding_by_user_id=_get_binding)
    thr.dormitory_repo = SimpleNamespace(update_threshold=_raise_value_error)
    thr.threshold_matcher.messages.clear()
    await thr.handle_threshold(session, balance_threshold=SimpleNamespace(result="10"))
    assert "不能小于 0" in thr.threshold_matcher.messages[-1]

    async def _update_none(**kwargs):
        del kwargs
        return None

    thr.dormitory_repo = SimpleNamespace(update_threshold=_update_none)
    thr.threshold_matcher.messages.clear()
    await thr.handle_threshold(session, balance_threshold=SimpleNamespace(result="10"))
    assert "更新电费阈值失败" in thr.threshold_matcher.messages[-1]

    async def _update_ok(**kwargs):
        del kwargs
        return SimpleNamespace(balance_threshold=8.8)

    thr.dormitory_repo = SimpleNamespace(update_threshold=_update_ok)
    thr.threshold_matcher.messages.clear()
    await thr.handle_threshold(session, balance_threshold=SimpleNamespace(result="8.8"))
    assert "8.8" in thr.threshold_matcher.messages[-1]

    async def _unbind_no_user(_uid):
        return None

    unb.dormitory_user_repo = SimpleNamespace(get_by_user_id=_unbind_no_user)
    await unb.handle_unbind(session)
    assert "还没有绑定学号" in unb.unbind_matcher.messages[-1]

    called = {"unbind": False}

    async def _unbind(**kwargs):
        called["unbind"] = True
        assert kwargs["user_id"] == "u1"

    async def _get_user_ok(_uid):
        return SimpleNamespace(user_id="u1")

    unb.dormitory_user_repo = SimpleNamespace(get_by_user_id=_get_user_ok, unbind=_unbind)
    unb.unbind_matcher.messages.clear()
    await unb.handle_unbind(session)
    assert called["unbind"] is True
    assert "解绑成功" in unb.unbind_matcher.messages[-1]


@pytest.mark.asyncio
async def test_cron_observer_and_command_utils() -> None:
    _load_command_module("nonebot_plugin_suda_electricity.commands.__init__")
    utils_mod = _load_command_module("nonebot_plugin_suda_electricity.commands.utils")
    cron = _load_command_module("nonebot_plugin_suda_electricity.commands.cron")

    result = SimpleNamespace(
        dormitory=SimpleNamespace(
            dormitory_name="A-101",
            left_electricity="11",
            balance="9.9",
        )
    )
    text = utils_mod.format_electricity_query_result(result)
    assert "A-101" in text

    user = SimpleNamespace(user_id="u1", subscribe_id="g1", subscribe_type="group")
    await utils_mod.send_low_balance_alert(result, user)
    sent = _FakeUniMessage.last_instance
    assert sent is not None
    assert isinstance(sent.sent_target, _FakeTarget)
    assert sent.sent_target.id == "g1"
    assert sent.sent_target.private is False

    event = SimpleNamespace(
        dormitory=SimpleNamespace(dormitory=SimpleNamespace(dormitory_key="dk"))
    )

    logs = {"error": 0, "warning": 0, "debug": 0}
    cron.logger = SimpleNamespace(
        error=lambda *_: logs.__setitem__("error", logs["error"] + 1),
        warning=lambda *_: logs.__setitem__("warning", logs["warning"] + 1),
        debug=lambda *_: logs.__setitem__("debug", logs["debug"] + 1),
    )

    async def _none(_key):
        return None

    cron.dormitory_repo = SimpleNamespace(get_detail=_none)
    await cron.scheduler_observer(event)
    assert logs["error"] == 1

    async def _empty(_key):
        return SimpleNamespace(users=[], dormitory=SimpleNamespace(balance_threshold=1, dormitory_key="dk"))

    cron.dormitory_repo = SimpleNamespace(get_detail=_empty)
    await cron.scheduler_observer(event)
    assert logs["warning"] == 1

    detail = SimpleNamespace(
        users=[SimpleNamespace(user_name="alice", password="pass", user_id="u1", subscribe_id="g1", subscribe_type="group")],
        dormitory=SimpleNamespace(balance_threshold=10, dormitory_key="dk"),
    )

    async def _detail(_key):
        return detail

    cron.dormitory_repo = SimpleNamespace(get_detail=_detail)
    cron.random.choice = lambda users: users[0]

    async def _captcha(*args, **kwargs):
        del args, kwargs
        raise cron.CaptchaRequiredError()

    cron.query_electricity = _captcha
    await cron.scheduler_observer(event)
    assert logs["error"] >= 2

    async def _boom(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("boom")

    cron.query_electricity = _boom
    await cron.scheduler_observer(event)
    assert logs["error"] >= 3

    called = {"alert": 0}

    async def _alert(_r, _u):
        called["alert"] += 1

    cron.send_low_balance_alert = _alert

    async def _low(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(dormitory=SimpleNamespace(balance="8"))

    cron.query_electricity = _low
    await cron.scheduler_observer(event)
    assert called["alert"] == 1

    async def _high(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(dormitory=SimpleNamespace(balance="80"))

    cron.query_electricity = _high
    await cron.scheduler_observer(event)
    assert logs["debug"] >= 1


def _load_plugin_init_module():
    fake_nonebot = types.ModuleType("nonebot")

    calls = []

    class _Driver:
        def on_startup(self, fn):
            calls.append(("startup_reg", fn))
            return fn

        def on_shutdown(self, fn):
            calls.append(("shutdown_reg", fn))
            return fn

    fake_nonebot.get_driver = lambda: _Driver()
    fake_nonebot.require = lambda _name: None

    fake_plugin = types.ModuleType("nonebot.plugin")

    class _PluginMetadata:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_plugin.PluginMetadata = _PluginMetadata
    fake_plugin.inherit_supported_adapters = lambda *args: args

    fake_package = types.ModuleType("nonebot_plugin_suda_electricity")
    fake_package.__path__ = [str(_PACKAGE_ROOT)]
    fake_package.__spec__ = importlib.machinery.ModuleSpec(
        "nonebot_plugin_suda_electricity", loader=None, is_package=True
    )
    fake_package.__spec__.submodule_search_locations = [str(_PACKAGE_ROOT)]

    state = {"startup": [], "shutdown": []}

    async def _init_crypto(_base):
        state["startup"].append("crypto")

    async def _init_db():
        state["startup"].append("db")

    async def _start_scheduler():
        state["startup"].append("scheduler")

    async def _stop_scheduler():
        state["shutdown"].append("scheduler")

    async def _close_pool():
        state["shutdown"].append("pool")

    async def _shutdown_db():
        state["shutdown"].append("db")

    modules = {
        "nonebot": fake_nonebot,
        "nonebot.plugin": fake_plugin,
        "nonebot_plugin_suda_electricity": fake_package,
        "nonebot_plugin_suda_electricity.commands": types.ModuleType(
            "nonebot_plugin_suda_electricity.commands"
        ),
        "nonebot_plugin_suda_electricity.config": types.ModuleType(
            "nonebot_plugin_suda_electricity.config"
        ),
        "nonebot_plugin_suda_electricity.utils": types.ModuleType(
            "nonebot_plugin_suda_electricity.utils"
        ),
        "nonebot_plugin_suda_electricity.crypto": types.ModuleType(
            "nonebot_plugin_suda_electricity.crypto"
        ),
        "nonebot_plugin_suda_electricity.db": types.ModuleType(
            "nonebot_plugin_suda_electricity.db"
        ),
        "nonebot_plugin_suda_electricity.scheduler": types.ModuleType(
            "nonebot_plugin_suda_electricity.scheduler"
        ),
        "nonebot_plugin_suda_electricity.suda": types.ModuleType(
            "nonebot_plugin_suda_electricity.suda"
        ),
    }

    modules["nonebot_plugin_suda_electricity.config"].Config = object
    modules["nonebot_plugin_suda_electricity.utils"].BASE_DATA_DIR = Path.cwd()
    modules["nonebot_plugin_suda_electricity.crypto"].init_crypto = _init_crypto
    modules["nonebot_plugin_suda_electricity.db"].init_db = _init_db
    modules["nonebot_plugin_suda_electricity.db"].shutdown_db = _shutdown_db
    modules["nonebot_plugin_suda_electricity.scheduler"].start_scheduler = _start_scheduler
    modules["nonebot_plugin_suda_electricity.scheduler"].stop_scheduler = _stop_scheduler
    modules["nonebot_plugin_suda_electricity.suda"].close_shared_client_pools = _close_pool

    _purge_modules()
    with pytest.MonkeyPatch.context() as mp:
        for key, value in modules.items():
            mp.setitem(sys.modules, key, value)
        module = importlib.import_module("nonebot_plugin_suda_electricity.__init__")
        return module, state, calls


@pytest.mark.asyncio
async def test_plugin_init_startup_and_shutdown_flow() -> None:
    module, state, calls = _load_plugin_init_module()

    assert module.__plugin_meta__.kwargs["name"] == "苏大电费查询"
    assert any(item[0] == "startup_reg" for item in calls)
    assert any(item[0] == "shutdown_reg" for item in calls)

    await module.startup()
    await module.shutdown()

    assert state["startup"] == ["crypto", "db", "scheduler"]
    assert state["shutdown"] == ["scheduler", "pool", "db"]
