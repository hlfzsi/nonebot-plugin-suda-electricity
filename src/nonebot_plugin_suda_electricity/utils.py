__all__ = [
    "logger",
    "APP_CONFIG",
    "BASE_DATA_DIR",
    "BASE_DATABASE_DIR",
    "DATABASE_DATA_DIR",
    "SessionInfo",
    "extract_session_info",
]

from pathlib import Path
from typing import Final, TypedDict

from nonebot import logger
from nonebot_plugin_localstore import get_plugin_data_dir
from nonebot_plugin_uninfo import Session

from .config import APP_CONFIG


BASE_DATA_DIR: Final[Path] = get_plugin_data_dir()
BASE_DATABASE_DIR: Final[Path] = BASE_DATA_DIR / "database"
DATABASE_DATA_DIR: Final[Path] = BASE_DATABASE_DIR / "db"


for path in (BASE_DATA_DIR, BASE_DATABASE_DIR, DATABASE_DATA_DIR):
    path.mkdir(parents=True, exist_ok=True)


class SessionInfo(TypedDict):
    user_id: str
    group_id: str | None


def extract_session_info(session: Session) -> SessionInfo:
    """从 Session 中提取 ID"""
    is_private = False
    if "private" in session.scene_path:  # llob下satori行为，私聊会给 group_id
        is_private = True
    if session.scene.is_private or is_private:
        return SessionInfo(user_id=session.user.id, group_id=None)
    else:
        return SessionInfo(user_id=session.user.id, group_id=session.scene_path)
