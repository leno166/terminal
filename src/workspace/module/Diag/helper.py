"""
@文件: helper.py
@作者: 雷小鸥
@日期: 2026/5/27 10:48
@许可: MIT License
@描述:
@版本: Version 0.1
"""
import socket
from Crypto.Cipher import AES
from Crypto.Hash import CMAC
from binascii import unhexlify


def recv_exact(sock: socket.socket, size: int) -> bytes:
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


def to_bytes(value: bytes | bytearray | str | int | None) -> bytes:
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
        return value.to_bytes(length, 'big')

    raise TypeError(f"Unsupported type: {type(value)}")


def calculate_key(level: int, seed: bytes, pin_code: str) -> bytes:
    if len(seed) == 3:
        key_01 = bytearray(3)
        CB = bytearray(8)
        pincode_bytes = bytearray.fromhex(pin_code)
        Data_C = 0xC541A9
        CB[0] = seed[0]
        CB[1] = seed[1]
        CB[2] = seed[2]
        CB[3] = pincode_bytes[0]
        CB[4] = pincode_bytes[1]
        CB[5] = pincode_bytes[2]
        CB[6] = pincode_bytes[3]
        CB[7] = pincode_bytes[4]

        for j in range(8):
            for i in range(8):
                Temp = (CB[j] >> i) & 0x000001
                Data_B = (((Data_C >> 1) & 0xFFFFFF) + (
                        (((Data_C & 0x000001) ^ Temp & 0x000001) << 23) & 0xFFFFFF)) & 0xFFFFFF
                Data_C = (Data_B & 0xEF6FD7) + ((((Data_B >> 23) ^ ((Data_B & 0x000008) >> 3)) & 0x000001) << 3) + (
                        (((Data_B >> 23) ^ ((Data_B & 0x000020) >> 5)) & 0x000001) << 5) + (
                                 (((Data_B >> 23) ^ ((Data_B & 0x001000) >> 12)) & 0x000001) << 12) + (
                                 (((Data_B >> 23) ^ ((Data_B & 0x008000) >> 15)) & 0x000001) << 15) + (
                                 (((Data_B >> 23) ^ ((Data_B & 0x100000) >> 20)) & 0x000001) << 20)

        key_01[0] = ((Data_C & 0x000FF0) >> 4) & 0xFF
        key_01[1] = (((Data_C & 0xF00000) >> 20) & 0xFF) + ((((Data_C & 0x00F000) >> 12) & 0xFF) << 4)
        key_01[2] = (((Data_C & 0x00000F) & 0xFF) << 4) + (((Data_C & 0x0F0000) >> 16) & 0xFF)

        return key_01
    else:
        return CMAC.new(unhexlify(pin_code), seed, ciphermod=AES).digest()


def get_pin_code(level: int, platform: str, serial_version: float = 2.0) -> str:
    """查询 PIN Code（委托给 config.loader，避免密钥硬编码）"""
    try:
        from .config.loader import get_pin_code as _loader_get
    except ImportError:
        from config.loader import get_pin_code as _loader_get

    return _loader_get(level, platform, serial_version)

