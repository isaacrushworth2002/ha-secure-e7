"""E7+ BLE wire format: framing, AES-128-ECB and JSON-RPC requests.

Every message is a 2-byte big-endian length prefix plus payload, sent as
numbered chunks of up to 19 bytes (sequence byte first, 0xFF on the last).
Authenticated JSON-RPC is zero-padded to 16 bytes and AES-ECB encrypted;
commissioning sends it in cleartext.

Kept free of Home Assistant imports so it can be unit tested on its own.
"""
from __future__ import annotations

import asyncio
import json
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PACKET = 19  # payload bytes per BLE chunk (after the sequence byte)


def aes_encrypt(key: bytes, data: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(data) + enc.finalize()


def aes_decrypt(key: bytes, data: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return dec.update(data) + dec.finalize()


def _chunk(buf: bytes) -> list[bytes]:
    tot = len(buf) // PACKET + 1
    out: list[bytes] = []
    for i in range(1, tot):
        out.append(bytes([i]) + buf[(i - 1) * PACKET : (i - 1) * PACKET + PACKET])
    out.append(bytes([0xFF]) + buf[(tot - 1) * PACKET :])
    return out


def _with_length(payload: bytes) -> bytes:
    n = len(payload)
    return bytes([(n >> 8) & 0xFF, n & 0xFF]) + payload


def frame_raw(payload: bytes) -> list[bytes]:
    """Chunks for a binary payload (the four-pass handshake)."""
    return _chunk(_with_length(payload))


def frame_json(js: str) -> list[bytes]:
    """Chunks for a cleartext JSON-RPC request (commissioning mode only)."""
    return _chunk(_with_length(js.encode()))


def frame_json_encrypted(key: bytes, js: str) -> list[bytes]:
    """Chunks for an authenticated JSON-RPC request."""
    body = _with_length(js.encode())
    if len(body) % 16:
        body += b"\x00" * (16 - len(body) % 16)
    return _chunk(aes_encrypt(key, body))


def unframe(raw: bytes) -> bytes:
    """Payload of a reassembled reply, without its length prefix or padding."""
    if len(raw) < 2:
        return b""
    n = (raw[0] << 8) | raw[1]
    return raw[2 : 2 + n]


def rpc(gmi: str, hi: int, si: int, args=None) -> str:
    """JSON-RPC request text for handler id hi on service id si."""
    header = {"GMI": gmi, "HI": hi, "SI": si}
    p = [header] if args is None else [header, args]
    return json.dumps(
        {"I": "0-1", "M": "Request", "P": p, "V": "1.0", "DTS": int(time.time())},
        separators=(",", ":"),
    )


class Reassembler:
    """Accumulates notify chunks keyed by their leading sequence byte."""

    def __init__(self) -> None:
        self.parts: dict[int, bytes] = {}
        self.event = asyncio.Event()

    def feed(self, data: bytes) -> None:
        if not data:
            return
        self.parts.setdefault(data[0], data[1:])
        if data[0] == 0xFF:
            self.event.set()

    def assembled(self) -> bytes:
        return b"".join(self.parts[k] for k in sorted(self.parts))

    def reset(self) -> None:
        self.parts = {}
        self.event.clear()
