"""
@文件: helper.py
@作者: 雷小鸥
@日期: 2026/5/27 10:48
@许可: MIT License
@描述: 工具函数 — 类型转换（平台无关）
@版本: Version 0.3
"""
from typing import Literal


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