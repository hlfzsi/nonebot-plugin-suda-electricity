__all__ = ["EncryptedString"]

from sqlalchemy import String, TypeDecorator
from ...crypto import decrypt, encrypt


class EncryptedString(TypeDecorator):
    """自动对字符串值进行加密和解密。"""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """在存储到数据库之前加密值。"""
        if value is None:
            return None
        return encrypt(value)

    def process_result_value(self, value, dialect):
        """从数据库读取后解密值。"""
        if value is None:
            return None
        return decrypt(value)
