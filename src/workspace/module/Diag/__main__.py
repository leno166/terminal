"""
@文件: __main__.py
@作者: 雷小鸥
@日期: 2026/5/27 18:30
@许可: MIT License
@描述:
@版本: Version 0.1
"""
import time

from src.workspace.module.Diag import *


# ================== 使用演示 ==================
if __name__ == '__main__':
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(funcName)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 优先从 connections.yaml 加载配置；也可显式传入覆盖
    with Service() as ss:
        ss.change_session(0x03)
        ss.change_level('L1')
