__all__ = ["encrypt", "decrypt", "init_crypto", "get_salt"]

import asyncio
import base64
import secrets
from pathlib import Path

import aiofiles
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from .config import APP_CONFIG

_FERNET: Fernet | None = None
ITERATIONS = 600_000
_salts: dict[Path, bytes] = {}
_file_lock = asyncio.Lock()  # 粗粒度问题不大


async def get_salt(dir: Path, filename: str) -> bytes:
    bin_path = dir / filename
    if bin_path in _salts:
        return _salts[bin_path]
    async with _file_lock:
        if bin_path.exists():
            async with aiofiles.open(bin_path, "rb") as f:
                _salts[bin_path] = await f.read()
        else:
            salt = secrets.token_bytes(32)
            bin_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(bin_path, "wb") as f:
                await f.write(salt)
            _salts[bin_path] = salt

    return _salts[bin_path]


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=ITERATIONS,
    )
    kdf_bytes = kdf.derive(password.encode())
    return base64.urlsafe_b64encode(kdf_bytes)


async def init_crypto(path: Path) -> None:
    salt = await get_salt(path, "salt.bin")
    global _FERNET
    _FERNET = Fernet(_derive_key(APP_CONFIG.suda_secret_key, salt))


def encrypt(plaintext: str) -> str:
    if _FERNET is None:
        raise RuntimeError("Crypto not initialized")
    return _FERNET.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    if _FERNET is None:
        raise RuntimeError("Crypto not initialized")
    return _FERNET.decrypt(ciphertext.encode()).decode()
