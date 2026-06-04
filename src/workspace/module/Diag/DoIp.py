"""
@文件: doip.py
@描述: DoIp 层 — Endpoint + SocketManager + Protocol + Sock
      无抽象类，无外部依赖（除 socket 和 helper）
"""
import threading
import socket
from typing import Literal, Optional
from logging import getLogger

from .helper import recv_frame
from .errors import ProtocolError

logger = getLogger(__name__)


# ================== Sock ==================

class Sock:
    """单个 socket 封装，send/recv/close"""

    def __init__(self, sock: socket.socket):
        self._sock = sock

    def send(self, msg: bytes) -> None:
        self._sock.sendall(msg)

    def recv(self) -> bytes:
        return recv_frame(self._sock)

    def close(self) -> None:
        self._sock.close()


# ================== SocketManager ==================

class SocketManager:
    """server socket 生命周期 + 连接表路由 + 重连，不加锁。

    由 DoIpEndpoint 保证单线程访问。

    Raises:
        RuntimeError: 未选中 ECU 时调用 send/recv、管理器未启动时调用 reconnect/accept。
        ConnectionError: select 的 ip 不在连接表中、重连接收到非预期 ip。
    """

    def __init__(self, sock_type: Optional[type[Sock]] = None):
        self._port = 0
        self._accept_timeout = 0.0
        self._recv_timeout = 0.0
        self._ip_table: dict[str, Sock] = {}
        self._current_ip: Optional[str] = None
        self._sock: Optional[socket.socket] = None
        self._sock_type = sock_type or Sock

    # --- 生命周期 ---

    def start(self, ip: str, port: int, listen_count: int,
              accept_timeout: float, recv_timeout: float) -> None:
        self._port = port
        self._accept_timeout = accept_timeout
        self._recv_timeout = recv_timeout
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(self._accept_timeout)
        sock.bind((ip, port))
        sock.listen(listen_count)
        self._sock = sock
        logger.info("DoIp 服务启动，监听 %s:%s，backlog %d", ip, port, listen_count)
        self._accept_once()

    def stop(self) -> None:
        for ip, s in self._ip_table.items():
            try:
                s.close()
                logger.debug("关闭 ECU %s 的 socket", ip)
            except Exception as e:
                logger.error("关闭 ECU %s 的 socket 时出错: %s", ip, e, exc_info=True)
        if self._sock:
            try:
                self._sock.close()
                logger.debug("关闭服务端 socket")
            except Exception as e:
                logger.error("关闭服务端 socket 时出错: %s", e, exc_info=True)
        self._ip_table.clear()
        self._current_ip = None
        logger.info("所有 socket 已关闭")

    # --- 连接表 ---

    def connections(self) -> list[str]:
        return list(self._ip_table.keys())

    def select(self, ip: str) -> None:
        if ip not in self._ip_table:
            raise ConnectionError(f"ECU {ip} 未连接，当前连接表:{list(self._ip_table.keys())}")
        self._current_ip = ip

    def current(self) -> Optional[str]:
        return self._current_ip

    # --- 数据路由 ---

    def send(self, data: bytes) -> None:
        if not self._current_ip:
            raise RuntimeError("未选中 ECU")
        self._ip_table[self._current_ip].send(data)

    def recv(self) -> bytes:
        if not self._current_ip:
            raise RuntimeError("未选中 ECU")
        return self._ip_table[self._current_ip].recv()

    # --- 重连 ---

    def reconnect(self, timeout: float) -> None:
        if not self._current_ip:
            raise RuntimeError("未选中 ECU，无法重连")
        if not self._sock:
            raise RuntimeError("管理器未启动")

        ip = self._current_ip
        old = self._ip_table.pop(ip, None)
        if old:
            old.close()

        self._sock.settimeout(timeout)
        try:
            sock, addr = self._sock.accept()
        finally:
            self._sock.settimeout(self._accept_timeout)

        if addr[0] != ip:
            sock.close()
            raise ConnectionError(f"重连失败：收到 {addr[0]} 而非 {ip}")

        sock.settimeout(self._recv_timeout)
        self._ip_table[ip] = self._sock_type(sock)
        logger.info("ECU %s 重连成功", ip)

    # --- 内部 ---

    def _accept_once(self) -> None:
        """单次 accept 循环，获取初始连接"""
        if not self._sock:
            raise RuntimeError("管理器未启动")

        logger.info('accept 启动')
        while True:
            try:
                sock, addr = self._sock.accept()
                logger.debug('accept once | addr: %s', addr)
            except TimeoutError:
                logger.debug('超时退出')
                break
            sock.settimeout(self._recv_timeout)
            ip, port = addr
            old = self._ip_table.get(ip)
            if old:
                old.close()
                logger.warning("IP %s 关闭已有连接", ip)
            self._ip_table[ip] = self._sock_type(sock)
            if port == self._port:
                logger.info("ECU 已连接 %s:%s", ip, port)
            else:
                logger.warning("ECU 已连接但端口未知 %s:%s", ip, port)


# ================== Protocol ==================

class Protocol:
    """DoIp 帧编解码，无状态。tester/ecu 每次调用传入。

    Raises:
        DoIpProtocolError: 帧格式校验失败 — 版本反码、Payload Type、长度、地址不匹配。
    """

    ERROR = ProtocolError

    def __init__(self, version: int, msg_type: int, byte_order: Literal['little', 'big']):
        self._version = version
        self._msg_type = msg_type
        self._byte_order: Literal['little', 'big'] = byte_order

    def encode(self, uds: bytes, tester: int, ecu: int) -> bytes:
        payload = (
            tester.to_bytes(2, self._byte_order) +
            ecu.to_bytes(2, self._byte_order) +
            uds
        )
        header = (
            self._version.to_bytes(1, self._byte_order) +
            (~self._version & 0xFF).to_bytes(1, self._byte_order) +
            self._msg_type.to_bytes(2, self._byte_order) +
            len(payload).to_bytes(4, self._byte_order)
        )
        return header + payload

    def decode(self, frame: bytes, tester: int, ecu: int) -> bytes:
        if len(frame) < 12:
            raise self.ERROR(f"响应帧太短: {len(frame)} 字节 (至少需要 12)，帧：{frame.hex(' ')}")

        version = frame[0]
        inverse_version = frame[1]
        if inverse_version != (~version & 0xFF):
            raise self.ERROR(
                f"版本反码错误: version=0x{version:02X}, inverse=0x{inverse_version:02X}，帧：{frame.hex(' ')}"
            )

        payload_type = int.from_bytes(frame[2:4], self._byte_order)
        if payload_type != 0x8001:
            raise self.ERROR(f"不支持的 Payload Type: 0x{payload_type:04X}，帧：{frame.hex(' ')}")

        payload_length = int.from_bytes(frame[4:8], self._byte_order)
        if payload_length != len(frame) - 8:
            raise self.ERROR(f"载荷长度不匹配: 头部 {payload_length}, 实际 {len(frame) - 8}，帧：{frame.hex(' ')}")

        src_addr = int.from_bytes(frame[8:10], self._byte_order)
        if src_addr != ecu:
            raise self.ERROR(f"源地址不匹配: 0x{src_addr:04X}，帧：{frame.hex(' ')}")

        dst_addr = int.from_bytes(frame[10:12], self._byte_order)
        if dst_addr != tester:
            raise self.ERROR(f"目标地址不匹配: 0x{dst_addr:04X}，帧：{frame.hex(' ')}")

        return frame[12:]


# ================== DoIpEndpoint ==================

class Endpoint:
    """DoIp 端点：整合 SocketManager + Protocol + 锁 + 重连决策，不对外暴露。

    Raises:
        DoIpProtocolError: ecu 未设置时调用 send。
        TimeoutError: 通信失败且重连后仍失败。
    """

    def __init__(self,
                 ip: str, port: int, tester: int,
                 accept_timeout: float, recv_timeout: float,
                 reconnect_timeout: float, listen_count: int,
                 version: int, msg_type: int, byte_order: Literal['little', 'big'],
                 sock_type: Optional[type[Sock]] = None):
        self._ip = ip
        self._port = port
        self._tester = tester
        self._accept_timeout = accept_timeout
        self._recv_timeout = recv_timeout
        self._reconnect_timeout = reconnect_timeout
        self._listen_count = listen_count
        self._ecu: Optional[int] = None
        self._lock = threading.Lock()
        self._manager = SocketManager(sock_type)
        self._protocol = Protocol(version, msg_type, byte_order)

    def start(self) -> None:
        self._manager.start(
            self._ip, self._port, self._listen_count,
            self._accept_timeout, self._recv_timeout,
        )

    def stop(self) -> None:
        self._manager.stop()

    def connections(self) -> list[str]:
        return self._manager.connections()

    def select(self, ip: str, ecu: int) -> None:
        self._manager.select(ip)
        self._ecu = ecu

    def send(self, uds: bytes) -> bytes:
        if self._ecu is None:
            raise Protocol.ERROR('DoIp 没有设置 ecu 逻辑地址')

        frame = self._protocol.encode(uds, self._tester, self._ecu)
        logger.debug('TX DoIp: %s', frame.hex(' '))

        with self._lock:
            try:
                self._manager.send(frame)
                response = self._manager.recv()
            except (ConnectionError, TimeoutError, OSError) as e:
                logger.warning('DoIp 通信失败，触发重连: %s', e)
                self._manager.reconnect(timeout=self._reconnect_timeout)
                self._manager.send(frame)

                try:
                    response = self._manager.recv()
                except (ConnectionError, TimeoutError, OSError):
                    logger.error('重连失败', exc_info=True)
                    raise TimeoutError

        logger.debug('RX DoIp: %s', response.hex(' '))
        return self._protocol.decode(response, self._tester, self._ecu)
