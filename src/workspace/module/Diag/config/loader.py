"""
@文件: loader.py
@描述: Diag 配置加载器 — 从 YAML 文件加载 PIN Code 和连接配置
@版本: Version 0.1
"""
import os
from typing import Optional

import yaml


def _config_dir() -> str:
    """配置文件搜索顺序：环境变量 DIAG_CONFIG_DIR → 当前文件所在目录"""
    env_dir = os.environ.get('DIAG_CONFIG_DIR')
    if env_dir and os.path.isdir(env_dir):
        return env_dir
    return os.path.dirname(os.path.abspath(__file__))


def _read_yaml(filename: str) -> dict:
    path = os.path.join(_config_dir(), filename)
    if not os.path.exists(path):
        example = filename.replace('.yaml', '.example.yaml')
        example_path = os.path.join(_config_dir(), example)
        raise FileNotFoundError(
            f"配置文件不存在: {path}\n"
            f"请复制模板文件: cp {example_path} {path}\n"
            f"然后编辑 {path} 填入真实值。"
        )
    with open(path, 'r', encoding='utf-8') as fh:
        return yaml.safe_load(fh)


# ================== PIN Code ==================

_pin_cache: Optional[dict] = None


def load_pin_codes() -> dict:
    """
    加载 PIN Code 配置，返回扁平查找字典。

    返回格式:
        {
            (level, platform):            "PIN_CODE",
            (level, platform, version):   "PIN_CODE",   # 带版本号的精确匹配
        }
    """
    global _pin_cache
    if _pin_cache is not None:
        return _pin_cache

    raw = _read_yaml('secrets.yaml')
    table: dict = {}

    for group in raw.get('pin_codes', []):
        level = group['level']
        for entry in group.get('entries', []):
            pin = entry['pin']
            serial_version = entry.get('serial_version')
            for plat in entry.get('platforms', []):
                if serial_version is not None:
                    table[(level, plat, serial_version)] = pin
                else:
                    table[(level, plat)] = pin

    _pin_cache = table
    return _pin_cache


def get_pin_code(level: int, platform: str, serial_version: float = 2.0) -> str:
    """
    查询 PIN Code。

    匹配优先级：
        1. 精确匹配 (level, platform, serial_version)
        2. 回退匹配 (level, platform)
        3. 均不匹配则抛出 ValueError
    """
    table = load_pin_codes()

    key = (level, platform, serial_version)
    if key in table:
        return table[key]

    key = (level, platform)
    if key in table:
        return table[key]

    raise ValueError(
        f'无 pin code 配置：level={level}, platform={platform}, serial_version={serial_version}'
    )


# ================== 连接配置 ==================

_conn_cache: Optional[dict] = None


def load_connections() -> dict:
    """加载连接配置，返回字典"""
    global _conn_cache
    if _conn_cache is not None:
        return _conn_cache

    _conn_cache = _read_yaml('connections.yaml')
    return _conn_cache


def get_defaults() -> dict:
    """获取 defaults 段"""
    return load_connections().get('defaults', {})


def get_ecus() -> dict[str, tuple[str, int]]:
    """
    获取 ECU 列表，转换为 Session 期望的格式:
        {name: (ip, logical_addr)}
    """
    ecus_raw = load_connections().get('ecus', {})
    result: dict[str, tuple[str, int]] = {}
    for name, info in ecus_raw.items():
        result[name] = (info['ip'], info['logical_addr'])
    return result