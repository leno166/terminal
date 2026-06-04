"""
@文件: uds.py
@描述: UDS 层 — KeepAlive + Session
      传输层委托给 autodoip 包
"""
import threading
from typing import Any, Callable, Self, Optional
from logging import getLogger
from types import MappingProxyType

from autodoip import Endpoint, Config

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
        self._thread: Optional[threading.Thread] = None

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
    """用户入口：持有 autodoip.Endpoint + KeepAlive。

    Raises:
        TypeError: send 传入非字符串。
        ValueError: send 传入非法 hex、on 指定未知 ECU、_pre_send 长度非偶数。
        RuntimeError: 在未 start 时调用 on/send、Endpoint 未初始化、
                     未发现可连接的 ECU。
    """

    def __init__(self,
                 ip: str,
                 ecus: dict[str, tuple],
                 port: int = 13400,
                 tester: int = 0x0E80,
                 transmit: Optional[Config] = None,
                 keepalive: Optional['KeepAliveConfig'] = None):
        # 延迟导入避免循环依赖
        from .service import KeepAliveConfig

        transmit = transmit or Config()
        keepalive = keepalive or KeepAliveConfig()

        self._ip = ip
        self._tester = tester
        self._port = port

        # 解析 ECU 表 — 新格式: {name: (logical_addr, ip)} 或 {name: (logical_addr, ip, port)}
        # port=0 表示使用 session 默认端口
        self._ecus: dict[str, tuple[int, str]] = {}        # {name: (logical_addr, ip)}
        self._ecu_names: dict[int, str] = {}                # {logical_addr: name}
        autodoip_ecus: dict[int, tuple[str, int]] = {}      # {logical_addr: (ip, port)}

        for name, ecu_tuple in ecus.items():
            addr, ecu_ip, *rest = ecu_tuple
            ecu_port = rest[0] if rest else 0
            if ecu_port == 0:
                ecu_port = port

            self._ecus[name] = (addr, ecu_ip)
            self._ecu_names[addr] = name
            autodoip_ecus[addr] = (ecu_ip, ecu_port)

        self._endpoint = Endpoint(
            ip=ip,
            ecus=autodoip_ecus,
            port=port,
            tester=tester,
            config=transmit,
        )

        self._byte_order = transmit.byte_order
        self._keepalive_interval = keepalive.interval
        self._keepalive_payload = keepalive.payload

        self._keepalive: Optional[KeepAlive] = None
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

    def _start_keepalive(self) -> None:
        if not self._endpoint:
            raise RuntimeError("Endpoint 未初始化")

        def _keepalive_send(payload: bytes) -> bytes:
            # 通过 send() 发送，与业务请求走同一路径
            resp = self.send(payload.hex(' '))
            return resp.raw

        self._keepalive = KeepAlive(
            fn=_keepalive_send,
            interval=self._keepalive_interval,
            payload=self._keepalive_payload,
        )
        self._keepalive.start()

    def _stop_keepalive(self) -> None:
        if self._keepalive:
            self._keepalive.stop()
            self._keepalive = None

    def _filter_ecus(self) -> dict[str, tuple[int, str]]:
        if not self._endpoint:
            raise RuntimeError("Endpoint 未初始化")

        # autodoip connections: {logical_addr: (ip, port, connected)}
        conns = self._endpoint.connections()
        filtered = {}
        for name, (addr, ip) in self._ecus.items():
            if addr in conns and conns[addr][2]:  # connected == True
                filtered[name] = (addr, ip)
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

        self._endpoint.start()

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

        logger.info('会话已关闭')
        return True

    def on(self, name: str) -> Self:
        with self._state_lock:
            if not self._opened:
                raise RuntimeError("会话未启动")
            if name not in self._ecus:
                raise ValueError(f"未知 ECU: {name}")
            addr, ip = self._ecus[name]
            self._cur_ecu = name

        if not self._endpoint:
            raise RuntimeError("Endpoint 未初始化")

        self._stop_keepalive()
        # autodoip 按逻辑地址选择
        self._endpoint.select(addr)
        self._start_keepalive()

        logger.info('已切换到 ECU: %s, IP: %s, 地址: 0x%04X', name, ip, addr)
        return self

    def send(self, data: str) -> UdsResponse:
        """发送 UDS 请求，在单次 conversation 内持续等待（含 0x78 流控）。
        只等待，不重发。
        """
        with self._state_lock:
            if not self._opened or not self._endpoint:
                raise RuntimeError("会话未启动或 Endpoint 无效")
            endpoint = self._endpoint

        payload = self._pre_send(data)
        logger.info('TX: %s', data)
        last_resp = None

        # 一次请求，持续等待（包括 NRC 0x78 流控帧）
        for raw in endpoint.conversation(payload):
            resp = UdsResponse.from_bytes(raw)
            resp.father = last_resp
            logger.info('RX: %s', raw.hex(' '))
            if not (resp.is_negative and resp.nrc == 0x78):
                return resp                  # 真正的最终响应
            last_resp = resp                 # 记下 0x78，继续等下一个
            logger.debug('收到 NRC 0x78（请求待处理），继续等待…')

        # 生成器耗尽（超时 / 连接中断），返回最后一个响应
        return last_resp or UdsResponse.from_bytes(b'')