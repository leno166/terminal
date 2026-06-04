"""
@文件: keys.py
@描述: OEM Key 算法 + PIN Code 管理
      包含 calculate_key（3字节 seed 自定义算法 + AES-CMAC）和 get_pin_code。
      通过 make_key_calculator() 工厂组装成 Uds 可注入的回调。
"""
from Crypto.Cipher import AES
from Crypto.Hash import CMAC
from binascii import unhexlify


def calculate_key(level: int, seed: bytes, pin_code: str) -> bytes:
    """
    Seed/Key 安全访问算法。

    - len(seed) == 3: 自定义位运算算法（某 OEM legacy）
    - 其他: AES-CMAC（标准算法）
    """
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


def get_pin_code(level: int, platform: str, serial_version: float) -> str:
    """
    PIN Code 查找表 — 平台 + 安全等级 → PIN Code。

    Raises:
        ValueError: 无对应平台的 PIN Code 配置。
    """
    table = {
        (0x1, 'P_30TU'):          'FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF',
        (0x1, 'P_G30TU'):         'FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF',
        (0x1, 'P_EEA40'):         'FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF',
        (0x1, 'P_20_25_25S'):     'FFFFFFFFFF',

        (0x19, 'P_30TU'):         'FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF',
        (0x19, 'P_G30TU'):        'FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF',
        (0x19, 'P_EEA40'):        'FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF',
        (0x19, 'P_20_25_25S'):    'FFFFFFFFFF',

        (0x5, 'P_30TU'):          'E853ECE43ABA6A39CB6CC221FC88B223',
        (0x5, 'P_G30TU'):         '51902E1AD902AF40119486A8DFA71708',
        (0x5, 'P_EEA40'):         '51902E1AD902AF40119486A8DFA71708',

        (0x5, 'P_20_25_25S', 2.0): 'FE63C818C2',
        (0x5, 'P_20_25_25S', 2.5): '7C9143F1BA',
    }

    key = (level, platform, serial_version)
    if key in table:
        return table[key]

    key = (level, platform)
    if key in table:
        return table[key]

    raise ValueError(
        f'无 pin code 配置：level={level}, platform={platform}, serial_version={serial_version}'
    )


def make_key_calculator(platform: str, serial_version: float = 2.0):
    """
    工厂函数：组装 key_calculator，供 Service.set_key_calculator() 注入。

    用法:
        ss.set_key_calculator(make_key_calculator('P_G30TU'))

    返回的 callable 签名: (level: int, seed: bytes) -> bytes
    """
    def key_calculator(level: int, seed: bytes) -> bytes:
        pin = get_pin_code(level, platform, serial_version)
        return calculate_key(level, seed, pin)

    return key_calculator