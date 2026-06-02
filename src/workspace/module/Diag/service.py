"""
@文件: service.py
@作者: 雷小鸥
@日期: 2026/6/2 14:44
@许可: MIT License
@描述: Service 层 — 配置 dataclass + UDS 标准诊断操作
@版本: Version 0.2
"""
import time
from dataclasses import dataclass
from typing import Callable, Literal, Tuple, Optional

from .uds import Session
from .response import UdsResponse
from .helper import to_bytes


# ================== 配置 dataclass ==================

@dataclass
class DoIPConfig:
    """DoIP 传输层配置"""
    port: int = 13400
    tester: int = 0x0E80
    accept_timeout: float = 1.5      # 初始 accept 等待 ECU 连接
    recv_timeout: float = 3.0        # 客户端 socket recv 等待 UDS 响应
    reconnect_timeout: float = 5.0   # 断连后重建连接的 accept 等待
    listen_count: int = 10
    version: int = 0x02
    msg_type: int = 0x8001
    byte_order: Literal['little', 'big'] = 'big'


@dataclass
class KeepAliveConfig:
    """TesterPresent 保活配置"""
    interval: float = 1.5
    payload: bytes = b'\x3E\x00'


@dataclass
class RetryConfig:
    """send_until 重试策略（ISO 14229 NRC 0x78 标准）"""
    count: int = 3
    delay: float = 0.5


# ================== Service ==================

class Service(Session):
    """UDS 标准诊断服务。继承 Session，提供 ISO 14229 协议方法。"""

    def __init__(self,
                 ip: str,
                 ecus: dict[str, tuple[str, int]],
                 doip: Optional[DoIPConfig] = None,
                 keepalive: Optional[KeepAliveConfig] = None,
                 retry: Optional[RetryConfig] = None):
        doip = doip or DoIPConfig()
        keepalive = keepalive or KeepAliveConfig()

        super().__init__(
            ip=ip,
            ecus=ecus,
            doip=doip,
            keepalive=keepalive,
        )

        self._retry = retry or RetryConfig()

        # --- 状态 ---
        self.session = 0x01
        self.level: int = 0x01

        # --- 可注入的回调 ---
        self._key_calculator: Optional[Callable[[int, bytes], bytes]] = None

        # 用于记录最近启动的例程 ID（供 get_routine_result 使用）
        self._current_routine_id: Optional[int] = None

    # ================== 注入点 ==================

    def set_key_calculator(self, fn: Callable[[int, bytes], bytes]) -> None:
        """注入 Key 计算回调。fn(level, seed) -> key_bytes。必须调用。"""
        self._key_calculator = fn

    # ================== 内部辅助 ==================

    def send_until(self, data: str, count: Optional[int] = None,
                   retry_delay: Optional[float] = None) -> UdsResponse:
        count = count if count is not None else self._retry.count
        retry_delay = retry_delay if retry_delay is not None else self._retry.delay

        for attempt in range(count):
            resp = self.send(data)

            if resp.is_negative and resp.nrc == 0x78:
                time.sleep(retry_delay)
                continue

            return resp

        raise RuntimeError("请求重复失败，已达到最大重试次数")

    # ================== UDS 标准服务 ==================

    def change_session(self, ss_id: int) -> Tuple[bool, UdsResponse]:
        """
        切换诊断会话（UDS 服务 0x10）
        :param ss_id: 会话类型，如 1=默认会话，2=编程会话，3=扩展会话等
        """
        resp = self.send_until(f"10 {ss_id:02X}")

        if resp.check_fail(0x50, ss_id):
            return False, resp

        self.session = ss_id
        return True, resp

    def change_level(self, level: int) -> Tuple[bool, UdsResponse]:
        """
        安全访问（UDS 服务 0x27）
        :param level: L 奇数，范围 0x01~0xFD（ISO 14229 行业惯例）
        """
        if not (0x01 <= level <= 0xFD and level % 2 == 1):
            raise ValueError(
                f"安全等级必须为 L 奇数 (0x01~0xFD)，收到: 0x{level:02X}"
            )

        if self._key_calculator is None:
            raise RuntimeError(
                "key_calculator 未注入。"
                "请先调用 service.set_key_calculator(fn)，"
                "fn(level: int, seed: bytes) -> bytes 负责 PIN 查找和 Key 计算。"
            )

        resp = self.send_until(f'27 {level:02X}')
        if resp.check_fail(0x67, level):
            return False, resp

        seed = to_bytes(resp.body)
        key = self._key_calculator(level, seed)
        resp = self.send_until(f'27 {level + 1:02X} {key.hex()}')
        if resp.check_fail(0x67, level + 1):
            return False, resp

        self.level = level
        return True, resp

    def change_any(
            self, ss_id: Optional[int] = None, level: Optional[int] = None,
    ) -> bool:
        if ss_id is not None:
            step_success, _ = self.change_session(ss_id)
            if not step_success:
                return False
        if level is not None:
            step_success, _ = self.change_level(level=level)
            if not step_success:
                return False
        return True

    def reset(self, reset_type: int = 0x01) -> bool:
        """
        ECU 复位（UDS 服务 0x11）
        :param reset_type: 复位类型，0x01=硬复位，0x02=钥匙关/开复位，0x03=软复位等
        """
        resp = self.send_until(f"11 {reset_type:02X}")
        if resp.check_fail(sid=0x51, head=reset_type):
            return False
        return True

    def read_data_by_identifier(self, did: int,
                                ss_id: Optional[int] = None,
                                level: Optional[int] = None) -> UdsResponse:
        self.change_any(ss_id, level)
        return self.send_until(f'22 {did:04X}')

    def read_did(self, did: int,
                 ss_id: Optional[int] = None,
                 level: Optional[int] = None) -> UdsResponse:
        return self.read_data_by_identifier(did, ss_id, level)

    def write_data_by_identifier(self, did: int, data: bytes,
                                 ss_id: Optional[int] = None,
                                 level: Optional[int] = None) -> UdsResponse:
        self.change_any(ss_id, level)
        return self.send_until(f'2E {did:04X} {data.hex()}')

    def write_did(self, did: int, data: bytes,
                  ss_id: Optional[int] = None,
                  level: Optional[int] = None) -> UdsResponse:
        return self.write_data_by_identifier(did, data, ss_id, level)

    def start_routine(self, routine_id: int,
                      data: Optional[bytes] = None,
                      ss_id: Optional[int] = None,
                      level: Optional[int] = None) -> UdsResponse:
        """
        启动例程（UDS 服务 0x31，子功能 0x01）
        :param routine_id: 例程标识符（2 字节）
        :param data:    可选的例程输入参数
        """
        self.change_any(ss_id, level)

        parts = [f"31 01 {routine_id:04X}"]
        if data:
            parts.append(data.hex())
        req_str = ' '.join(parts)
        resp = self.send_until(req_str)

        if not resp.is_negative and resp.head and len(resp.head) >= 1 and resp.head[0] == 0x01:
            self._current_routine_id = routine_id
        return resp

    def stop_routine(self, routine_id: int) -> bool:
        """
        停止例程（UDS 服务 0x31，子功能 0x02）
        """
        req_str = f"31 02 {routine_id:04X}"
        resp = self.send_until(req_str)

        if resp.check_fail(sid=0x71, head=0x02):
            return False

        if resp.head and len(resp.head) >= 2:
            returned_rid = int.from_bytes(resp.head[1:3], self._byte_order)
            if returned_rid != routine_id:
                return False

        return True

    def get_routine_result(self, routine_id: Optional[int] = None) -> UdsResponse:
        """
        获取例程结果（UDS 服务 0x31，子功能 0x03）
        """
        rid = routine_id if routine_id is not None else self._current_routine_id
        if rid is None:
            raise ValueError("未提供例程 ID 且没有最近启动的例程记录")

        req_str = f"31 03 {rid:04X}"
        return self.send_until(req_str)