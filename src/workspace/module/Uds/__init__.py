"""
@文件: __init__.py
@作者: 雷小鸥
@日期: 2026/5/26 13:41
@许可: MIT License
@描述: Uds 模块入口 — UDS 诊断（传输层基于 autodoip）
      声明式 API：UdsApp + @read / @write / @workflow
      命令式 API：Session / Service
@版本: Version 0.4
"""
from autodoip import ProtocolError

from .core import Session
from .service import Service, KeepAliveConfig
from .response import UdsResponse
from .app import UdsApp
from .decorators import read, write, workflow

__all__ = [
    # 声明式 API
    'UdsApp',
    'read',
    'write',
    'workflow',
    # 命令式 API
    'Session',
    'Service',
    'UdsResponse',
    'KeepAliveConfig',
    'ProtocolError',
]