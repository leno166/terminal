"""
@文件: platform.py
@描述: OEM 平台专有方法 — unlock_ssh 等非 UDS 标准的扩展操作
"""
from module.Diag.service import Service
from module.Diag.helper import to_bytes


def unlock_ssh(service: Service, soc_num: int = 1) -> bool:
    """
    解锁 SSH（OEM 私有 DID 0xDC06）。
    非 ISO 14229 标准方法，仅适用于特定平台。

    :param service: 已启动的 Service 实例
    :param soc_num: SoC 编号（1 → target=0x40, 其他 → target=0x50）
    :return: 成功返回 True
    """
    target = '40' if soc_num == 1 else '50'

    resp = service.read_did(0xDC06)
    if resp.check_fail(0x62, 0xDC06):
        return False

    status = to_bytes(resp.body)[-2:]
    if to_bytes(status) != to_bytes(target):
        return False

    resp = service.write_did(0xDC06, to_bytes(target))
    if resp.check_fail(0x6E, 0xDC06):
        return False

    return True