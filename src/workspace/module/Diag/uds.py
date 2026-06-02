"""
@文件: uds.py
@描述: UDS 层 — KeepAlive + Session
      无抽象类，无外部依赖（除 doip）
"""
import threading
from typing import Any, Callable, Literal, Self
from logging import getLogger
from types import MappingProxyType

from .DoIp import DoIPEndpoint
from .response import UdsResponse

logger = getLogger(__name__)


# ================== KeepAlive ==================

class KeepAlive:
    """后台保活线程，循环发送 payload"""

    def __init__(
            self, fn: Callable[[bytes], bytes], interval: float, payload: bytes
    ):
        self._fn = fn
        self._interval = interval
        self._payload = payload
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            logger.warning('心跳线程已在运行，忽略重复启动')
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name='DoIpKeepAlive', daemon=True)
        self._thread.start()
        logger.debug('心跳线程已启动')

    def stop(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            return
        self._stop_event.set()
        if threading.current_thread() is self._thread:
            return
        self._thread.join(timeout=self._interval + 0.1)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._fn(self._payload)
            except Exception:
                logger.warning('心跳发送失败，停止心跳', exc_info=True)
                self._stop_event.set()
            self._stop_event.wait(self._interval)


# ================== Session ==================

class Session:
    """用户唯一入口：持有 Endpoint + KeepAlive"""

    def __init__(self, ip: str, ecus: dict[str, tuple[str, int]],
                 port: int = 13400, tester: int = 0x0E80,
                 timeout: float = 1, listen_count: int = 10,
                 doip_version: int = 0x02, doip_msg_type: int = 0x8001,
                 byte_order: Literal['little', 'big'] = 'big',
                 keepalive_interval: float = 0.5,
                 keepalive_payload: bytes = b'\x3E\x00'):
        self._ip = ip
        self._port = port
        self._tester = tester
        self._timeout = timeout
        self._listen_count = listen_count
        self._doip_version = doip_version
        self._doip_msg_type = doip_msg_type
        self._byte_order = byte_order
        self._ecus = ecus.copy()

        self._keepalive_interval = keepalive_interval
        self._keepalive_payload = keepalive_payload

        self._endpoint: DoIPEndpoint | None = None
        self._keepalive: KeepAlive | None = None
        self._cur_ecu: str = ''
        self._opened = False
        self._state_lock = threading.RLock()

    # --- 运算符 ---

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    def __rshift__(self, uds: Any) -> Any:
        return self.send(uds)

    # --- 内部 ---

    @staticmethod
    def _pre_send(data: str) -> bytes:
        """前置：校验输入为字符串、偶数长度、合法 hex → bytes"""
        if not isinstance(data, str):
            raise TypeError(f"UDS 数据必须为字符串，收到: {type(data).__name__}")
        frame = data.replace(' ', '').upper()
        if len(frame) % 2:
            raise ValueError('UDS 长度必须为偶数')
        try:
            return bytes.fromhex(frame)
        except ValueError:
            raise ValueError(f'非法 UDS 数据: {data}')

    @staticmethod
    def _post_receive(data: bytes) -> UdsResponse:
        """后置：返回字节粗略拆分为空格分隔的 hex 字符串"""
        return UdsResponse.from_bytes(data)

    def _start_keepalive(self) -> None:
        if not self._endpoint:
            raise RuntimeError("Endpoint 未初始化")

        self._keepalive = KeepAlive(
            fn=self._endpoint.send,
            interval=self._keepalive_interval,
            payload=self._keepalive_payload,
        )
        self._keepalive.start()

    def _stop_keepalive(self) -> None:
        if self._keepalive:
            self._keepalive.stop()
            self._keepalive = None

    def _filter_ecus(self) -> dict[str, tuple[str, int]]:
        if not self._endpoint:
            raise RuntimeError("Endpoint 未初始化")

        connections = self._endpoint.connections()
        filtered = {}
        for name, (ip, ecu) in self._ecus.items():
            if ip in connections:
                filtered[name] = (ip, ecu)
        if not filtered:
            raise RuntimeError("未发现可连接的 ECU")
        return filtered

    # --- 属性 ---

    @property
    def ecus(self):
        with self._state_lock:
            return MappingProxyType(self._ecus)

    # --- 公开方法 ---

    def start(self) -> bool:
        with self._state_lock:
            if self._opened:
                return True

        endpoint = DoIPEndpoint(
            ip=self._ip, port=self._port, tester=self._tester,
            timeout=self._timeout, listen_count=self._listen_count,
            version=self._doip_version, msg_type=self._doip_msg_type,
            byte_order=self._byte_order,
        )
        endpoint.start()
        self._endpoint = endpoint

        self._ecus = self._filter_ecus()

        with self._state_lock:
            self._opened = True

        ecu_name = next(iter(self._ecus.keys()))
        self.on(ecu_name)

        logger.info('会话已开启')
        return True

    def stop(self) -> bool:
        with self._state_lock:
            if not self._opened:
                return True
            self._cur_ecu = ''
            self._opened = False

        self._stop_keepalive()
        if self._endpoint:
            self._endpoint.stop()
            self._endpoint = None

        logger.info('会话已关闭')
        return True

    def on(self, name: str) -> Self:
        with self._state_lock:
            if not self._opened:
                raise RuntimeError("会话未启动")
            if name not in self._ecus:
                raise ValueError(f"未知 ECU: {name}")
            ip, ecu = self._ecus[name]
            self._cur_ecu = name

        if not self._endpoint:
            raise RuntimeError("Endpoint 未初始化")

        self._stop_keepalive()
        self._endpoint.select(ip, ecu)
        self._start_keepalive()

        logger.info('已切换到 ECU: %s, IP: %s, 地址: 0x%04X', name, ip, ecu)
        return self

    def send(self, data: str) -> UdsResponse:
        with self._state_lock:
            if not self._opened:
                raise RuntimeError("会话未启动")
            if not self._endpoint:
                raise RuntimeError("Endpoint 未初始化")
            endpoint = self._endpoint

        logger.info('TX: %s', data)
        payload = self._pre_send(data)
        response = endpoint.send(payload)
        logger.info('RX: %s', response.hex(' '))
        return self._post_receive(response)
