"""
@文件: __init__.py
@作者: 雷小鸥
@日期: 2026/5/28 10:54
@许可: MIT License
@描述:
@版本: Version 0.1
"""
from .Engine import TailEngine as Session
from .DuplexTransport import DuplexTransport as Transport

__all__ = ['Session', 'Transport']
