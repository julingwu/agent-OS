"""保险库用的对称加密（Fernet）。

密钥文件默认放在数据库同目录的 vault.key（0600，首次自动生成）。
任何接口都不把明文回传给模型；只有人通过 reveal 接口才能取一次。
"""

from __future__ import annotations

import os
import pathlib

from cryptography.fernet import Fernet

from . import db

_cache: dict[str, Fernet] = {}


def _key_path() -> pathlib.Path:
    explicit = os.environ.get("VAULT_KEY_PATH")
    if explicit:
        return pathlib.Path(explicit)
    return pathlib.Path(db.db_path()).parent / "vault.key"


def _fernet() -> Fernet:
    path = _key_path()
    key = str(path)
    if key in _cache:
        return _cache[key]
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Fernet.generate_key())
        try:
            path.chmod(0o600)
        except OSError:
            pass
    _cache[key] = Fernet(path.read_bytes().strip())
    return _cache[key]


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
