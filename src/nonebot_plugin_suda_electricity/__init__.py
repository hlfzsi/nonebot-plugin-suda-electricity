from importlib.metadata import version, PackageNotFoundError

from nonebot import get_driver, require
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

require("nonebot_plugin_localstore")
require("nonebot_plugin_alconna")
require("nonebot_plugin_uninfo")

from . import commands  # noqa: E402, F401
from .config import Config  # noqa: E402
from .utils import BASE_DATA_DIR  # noqa: E402
from .crypto import init_crypto  # noqa: E402
from .db import init_db, shutdown_db  # noqa: E402
from .scheduler import start_scheduler, stop_scheduler  # noqa: E402
from .suda import close_shared_client_pools  # noqa: E402


try:
    __version__ = version(__package__) if __package__ else None
except PackageNotFoundError:
    __version__ = None

__author__ = "hlfzsi"

__plugin_meta__ = PluginMetadata(
    name="苏大电费查询",
    description="苏大宿舍电费查询与低余额提醒插件",
    usage="阅读README.md",
    config=Config,
    type="application",
    homepage="https://github.com/hlfzsi/nonebot-plugin-suda-electricity",
    supported_adapters=inherit_supported_adapters(
        "nonebot_plugin_localstore", "nonebot_plugin_alconna", "nonebot_plugin_uninfo"
    ),
    extra={
        "author": __author__,
        "version": __version__,
    },
)
# TODO 数据库加密
driver = get_driver()


@driver.on_startup
async def startup() -> None:
    await init_crypto(BASE_DATA_DIR)
    await init_db()
    await start_scheduler()


@driver.on_shutdown
async def shutdown() -> None:
    await stop_scheduler()
    await close_shared_client_pools()
    await shutdown_db()
