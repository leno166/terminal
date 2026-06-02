"""
@文件: __init__.py
@描述: OEM 专有包 — Key 算法、PIN Code 管理、平台扩展方法
      本包为私有代码，不跟随 Diag 公共库发布。
"""
from .keys import make_key_calculator
from .platform import unlock_ssh

__all__ = ['make_key_calculator', 'unlock_ssh']