"""
@文件: errors.py
@作者: 雷小鸥
@日期: 2026/6/2
@许可: MIT License
@描述: 诊断模块自定义异常
@版本: Version 0.1
"""


class DoIpProtocolError(Exception):
    """DoIP 协议层错误 — 帧格式不符 ISO 13400 标准时抛出。

    触发条件：版本反码错、Payload Type 非 0x8001、长度不匹配、地址不匹配、帧过短。
    """
    pass
