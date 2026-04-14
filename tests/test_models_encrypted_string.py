import importlib
import importlib.machinery
import logging
import sys
import types
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

_PACKAGE_ROOT = Path.cwd() / "src" / "nonebot_plugin_suda_electricity"


def _purge_modules() -> None:
    for name in list(sys.modules):
        if name == "nonebot_plugin_suda_electricity" or name.startswith(
            "nonebot_plugin_suda_electricity."
        ):
            sys.modules.pop(name, None)


def _load_modules():
    fake_nonebot = types.ModuleType("nonebot")
    fake_nonebot.logger = logging.getLogger("test-nonebot")
    fake_nonebot.get_plugin_config = lambda model: model(
        suda_secret_key="test-secret-key-32-chars-min!"
    )

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

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "nonebot", fake_nonebot)
        mp.setitem(sys.modules, "nonebot_plugin_localstore", fake_localstore)
        mp.setitem(sys.modules, "nonebot_plugin_suda_electricity", fake_package)
        mp.setitem(sys.modules, "nonebot_plugin_suda_electricity.utils", fake_utils)
        return {
            "crypto": importlib.import_module("nonebot_plugin_suda_electricity.crypto"),
            "types": importlib.import_module(
                "nonebot_plugin_suda_electricity.db.models.types"
            ),
            "user_model": importlib.import_module(
                "nonebot_plugin_suda_electricity.db.models.user"
            ),
            "dormitory_model": importlib.import_module(
                "nonebot_plugin_suda_electricity.db.models.dormitory"
            ),
        }


@pytest.fixture
def modules():
    SQLModel.metadata.clear()
    loaded = _load_modules()
    yield loaded
    _purge_modules()
    SQLModel.metadata.clear()


@pytest.mark.asyncio
async def test_encrypted_string_process_bind_and_result_value_roundtrip(
    modules,
    tmp_path,
) -> None:
    crypto = modules["crypto"]
    encrypted_string = modules["types"].EncryptedString()

    crypto._derive_key = lambda password, salt: Fernet.generate_key()
    await crypto.init_crypto(tmp_path)

    ciphertext = encrypted_string.process_bind_param("alice", None)

    assert ciphertext != "alice"
    assert encrypted_string.process_result_value(ciphertext, None) == "alice"
    assert encrypted_string.process_bind_param(None, None) is None
    assert encrypted_string.process_result_value(None, None) is None


@pytest.mark.asyncio
async def test_encrypted_string_roundtrip_in_orm_stores_ciphertext(
    modules,
    tmp_path,
) -> None:
    crypto = modules["crypto"]
    DormitoryUserTable = modules["user_model"].DormitoryUserTable
    build_user_name_hash = modules["user_model"].build_user_name_hash

    crypto._derive_key = lambda password, salt: Fernet.generate_key()
    await crypto.init_crypto(tmp_path)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(DormitoryUserTable.metadata.create_all)

    async with session_maker() as session:
        session.add(
            DormitoryUserTable(
                user_id="owner-1",
                user_name="alice",
                user_name_hash=await build_user_name_hash("alice"),
                password="pass-1",
                subscribe_type="private",
                subscribe_id="owner-1",
                dormitory_key="01:02:0301",
            )
        )
        await session.commit()

    async with session_maker() as session:
        table_name = DormitoryUserTable.__table__.name
        raw = (
            await session.exec(
                text(
                    f"SELECT user_name, password FROM {table_name} WHERE user_id = :uid"
                ).bindparams(uid="owner-1")
            )
        ).first()
        assert raw is not None
        assert raw[0] != "alice"
        assert raw[1] != "pass-1"

        user = await session.get(DormitoryUserTable, "owner-1")
        assert user is not None
        assert user.user_name == "alice"
        assert user.password == "pass-1"

    await engine.dispose()
