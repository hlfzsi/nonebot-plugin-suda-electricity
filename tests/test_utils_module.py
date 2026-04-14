import importlib
import importlib.machinery
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_PACKAGE_ROOT = Path.cwd() / "src" / "nonebot_plugin_suda_electricity"


def _purge_modules() -> None:
    for name in list(sys.modules):
        if name == "nonebot_plugin_suda_electricity" or name.startswith(
            "nonebot_plugin_suda_electricity."
        ):
            sys.modules.pop(name, None)


def _load_utils_module(base_dir: Path):
    fake_nonebot = types.ModuleType("nonebot")
    fake_nonebot.logger = logging.getLogger("test-nonebot")
    fake_nonebot.get_plugin_config = lambda model: model(
        suda_secret_key="test-secret-key-32-chars-min!"
    )

    fake_localstore = types.ModuleType("nonebot_plugin_localstore")
    fake_localstore.get_plugin_data_dir = lambda: base_dir

    fake_uninfo = types.ModuleType("nonebot_plugin_uninfo")
    fake_uninfo.Session = object

    fake_package = types.ModuleType("nonebot_plugin_suda_electricity")
    fake_package.__path__ = [str(_PACKAGE_ROOT)]
    fake_package.__spec__ = importlib.machinery.ModuleSpec(
        "nonebot_plugin_suda_electricity",
        loader=None,
        is_package=True,
    )
    fake_package.__spec__.submodule_search_locations = [str(_PACKAGE_ROOT)]

    _purge_modules()

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "nonebot", fake_nonebot)
        mp.setitem(sys.modules, "nonebot_plugin_localstore", fake_localstore)
        mp.setitem(sys.modules, "nonebot_plugin_uninfo", fake_uninfo)
        mp.setitem(sys.modules, "nonebot_plugin_suda_electricity", fake_package)
        return importlib.import_module("nonebot_plugin_suda_electricity.utils")


@pytest.fixture
def utils_module(tmp_path):
    module = _load_utils_module(tmp_path / "plugin-data")
    yield module
    _purge_modules()


def test_extract_session_info_private_scene_flag(utils_module) -> None:
    session = SimpleNamespace(
        user=SimpleNamespace(id="u1"),
        scene=SimpleNamespace(is_private=True),
        scene_path="group:1001",
    )

    info = utils_module.extract_session_info(session)

    assert info == {"user_id": "u1", "group_id": None}


def test_extract_session_info_private_scene_path(utils_module) -> None:
    session = SimpleNamespace(
        user=SimpleNamespace(id="u2"),
        scene=SimpleNamespace(is_private=False),
        scene_path="private:2002",
    )

    info = utils_module.extract_session_info(session)

    assert info == {"user_id": "u2", "group_id": None}


def test_extract_session_info_group_scene(utils_module) -> None:
    session = SimpleNamespace(
        user=SimpleNamespace(id="u3"),
        scene=SimpleNamespace(is_private=False),
        scene_path="group:3003",
    )

    info = utils_module.extract_session_info(session)

    assert info == {"user_id": "u3", "group_id": "group:3003"}


def test_utils_creates_expected_directories(utils_module) -> None:
    assert utils_module.BASE_DATA_DIR.exists()
    assert utils_module.BASE_DATABASE_DIR.exists()
    assert utils_module.DATABASE_DATA_DIR.exists()
