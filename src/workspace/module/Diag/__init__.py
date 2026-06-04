"""
@文件: __init__.py
@作者: 雷小鸥
@日期: 2026/5/26 13:41
@许可: MIT License
@描述: Diag 模块入口 — UDS 诊断（传输层基于 autodoip）
@版本: Version 0.3
"""
from autodoip import ProtocolError

from .uds import Session
from .service import Service, KeepAliveConfig
from .response import UdsResponse

__all__ = [
    'Session',
    'Service',
    'UdsResponse',
    'KeepAliveConfig',
    'ProtocolError',
]