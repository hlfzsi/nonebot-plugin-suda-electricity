import importlib
import importlib.machinery
import logging
import sys
import types
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

_PACKAGE_ROOT = Path.cwd() / "src" / "nonebot_plugin_suda_electricity"


def _purge_modules() -> None:
    for name in list(sys.modules):
        if name == "nonebot_plugin_suda_electricity" or name.startswith(
            "nonebot_plugin_suda_electricity."
        ):
            sys.modules.pop(name, None)


def _load_crypto_module(secret_key: str = "test-secret-key-32-chars-min!"):
    fake_nonebot = types.ModuleType("nonebot")
    fake_nonebot.logger = logging.getLogger("test-nonebot")
    fake_nonebot.get_plugin_config = lambda model: model(suda_secret_key=secret_key)

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
        mp.setitem(sys.modules, "nonebot_plugin_suda_electricity", fake_package)
        return importlib.import_module("nonebot_plugin_suda_electricity.crypto")


@pytest.fixture
def crypto_module():
    module = _load_crypto_module()
    module._FERNET = None
    yield module
    module._FERNET = None
    _purge_modules()


@pytest.mark.asyncio
async def test_encrypt_decrypt_roundtrip_preserves_plaintext(crypto_module, tmp_path) -> None:
    crypto_module._derive_key = lambda password, salt: Fernet.generate_key()
    await crypto_module.init_crypto(tmp_path)

    ciphertext = crypto_module.encrypt("alice")

    assert ciphertext != "alice"
    assert crypto_module.decrypt(ciphertext) == "alice"


@pytest.mark.asyncio
async def test_encrypt_is_nondeterministic_per_call(crypto_module, tmp_path) -> None:
    crypto_module._derive_key = lambda password, salt: Fernet.generate_key()
    await crypto_module.init_crypto(tmp_path)

    first = crypto_module.encrypt("same-input")
    second = crypto_module.encrypt("same-input")

    assert first != second
    assert crypto_module.decrypt(first) == "same-input"
    assert crypto_module.decrypt(second) == "same-input"


def test_encrypt_without_init_raises_runtime_error(crypto_module) -> None:
    crypto_module._FERNET = None

    with pytest.raises(RuntimeError, match="Crypto not initialized"):
        crypto_module.encrypt("alice")


@pytest.mark.asyncio
async def test_init_crypto_creates_and_reuses_salt(crypto_module, tmp_path) -> None:
    crypto_module._derive_key = lambda password, salt: Fernet.generate_key()
    await crypto_module.init_crypto(tmp_path)
    salt_file = tmp_path / "salt.bin"
    first = salt_file.read_bytes()

    await crypto_module.init_crypto(tmp_path)
    second = salt_file.read_bytes()

    assert salt_file.exists()
    assert len(first) == 32
    assert first == second


@pytest.mark.asyncio
async def test_decrypt_invalid_token_raises(crypto_module, tmp_path) -> None:
    crypto_module._derive_key = lambda password, salt: Fernet.generate_key()
    await crypto_module.init_crypto(tmp_path)

    with pytest.raises(InvalidToken):
        crypto_module.decrypt("not-a-valid-fernet-token")
