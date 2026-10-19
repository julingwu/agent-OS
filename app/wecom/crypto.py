"""WeCom (企业微信) callback crypto: signature check + AES-256-CBC envelope.

Spec recap (same as the official WXBizMsgCrypt sample):
  * EncodingAESKey is 43 chars; key = base64decode(key + "=") -> 32 bytes, iv = key[:16]
  * plaintext = random(16) + msg_len(4, big endian) + msg + corp_id
  * PKCS#7 padding to a 32-byte block
  * signature = sha1(sorted([token, timestamp, nonce, encrypt]).join(""))
"""
from __future__ import annotations

import base64
import hashlib
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

BLOCK = 32


class WeComCryptoError(ValueError):
    pass


def _key(encoding_aes_key: str) -> bytes:
    raw = base64.b64decode(encoding_aes_key + "=")
    if len(raw) != 32:
        raise WeComCryptoError("EncodingAESKey must decode to 32 bytes")
    return raw


def signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    parts = sorted([token, timestamp, nonce, encrypt])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def verify(token: str, timestamp: str, nonce: str, encrypt: str, msg_signature: str) -> bool:
    return signature(token, timestamp, nonce, encrypt) == (msg_signature or "").lower()


def _pad(data: bytes) -> bytes:
    n = BLOCK - (len(data) % BLOCK)
    return data + bytes([n]) * n


def _unpad(data: bytes) -> bytes:
    if not data:
        raise WeComCryptoError("empty plaintext")
    n = data[-1]
    if n < 1 or n > BLOCK or n > len(data):
        raise WeComCryptoError(f"bad padding byte {n}")
    return data[:-n]


def encrypt(plain: str, encoding_aes_key: str, corp_id: str) -> str:
    key = _key(encoding_aes_key)
    msg = plain.encode("utf-8")
    body = os.urandom(16) + struct.pack(">I", len(msg)) + msg + corp_id.encode("utf-8")
    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
    enc = cipher.encryptor()
    out = enc.update(_pad(body)) + enc.finalize()
    return base64.b64encode(out).decode("ascii")


def decrypt(encrypted: str, encoding_aes_key: str, corp_id: str) -> str:
    key = _key(encoding_aes_key)
    try:
        raw = base64.b64decode(encrypted)
    except Exception as exc:  # noqa: BLE001
        raise WeComCryptoError(f"bad base64: {exc}") from exc
    if not raw or len(raw) % 16:
        raise WeComCryptoError("ciphertext length is not a multiple of 16")
    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
    dec = cipher.decryptor()
    plain = _unpad(dec.update(raw) + dec.finalize())
    if len(plain) < 20:
        raise WeComCryptoError("plaintext too short")
    msg_len = struct.unpack(">I", plain[16:20])[0]
    msg = plain[20:20 + msg_len]
    tail = plain[20 + msg_len:]
    if corp_id and tail.decode("utf-8", "ignore") != corp_id:
        raise WeComCryptoError("corp_id mismatch — wrong secret or cross-corp callback")
    return msg.decode("utf-8")
