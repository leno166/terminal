"""
@文件: helper.py
@作者: 雷小鸥
@日期: 2026/5/27 10:48
@许可: MIT License
@描述: 工具函数 — 收帧、类型转换（平台无关）
@版本: Version 0.2
"""
import socket
from typing import Literal


def recv_exact(sock: socket.socket, size: int) -> bytes:
    """
    精确收取 size 字节。

    Raises:
        ConnectionError: 连接在收齐数据前关闭。
    """
    data = bytearray()

    while len(data) < size:
        chunk = sock.recv(size - len(data))

        if not chunk:
            raise ConnectionError('连接已关闭')

        data.extend(chunk)

    return bytes(data)


def recv_frame(sock: socket.socket) -> bytes:
    header = recv_exact(sock, 8)

    payload_length = int.from_bytes(header[4:8], 'big')

    payload = recv_exact(sock, payload_length)

    return header + payload


def to_bytes(value: bytes | bytearray | str | int | None,
             byte_order: Literal['little', 'big'] = 'big') -> bytes:
    """
    统一类型 → bytes。

    Raises:
        TypeError: 传入不支持的类型。
    """
    if value is None:
        return b''

    if isinstance(value, (bytes, bytearray)):
        return bytes(value)

    if isinstance(value, str):
        cleaned = value.replace(' ', '').replace('0x', '').replace('0X', '')
        if len(cleaned) % 2:
            cleaned = '0' + cleaned
        return bytes.fromhex(cleaned)

    if isinstance(value, int):
        length = (value.bit_length() + 7) // 8 or 1
        return value.to_bytes(length, byte_order)

    raise TypeError(f"Unsupported type: {type(value)}")
