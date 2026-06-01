"""
@文件: Engine.py
@作者: 雷小鸥
@日期: 2026/5/28 11:00
@许可: MIT License
@描述:
@版本: Version 0.1
"""
import threading
from .Session import Session
from .TailBuffer import TailBuffer
from .DuplexTransport import DuplexTransport


class TailEngine:
    """长生命周期引擎：初始化即启动后台泵线程。读写中枢，Session 只读。"""

    def __init__(self, transport: DuplexTransport, max_size: int = 256) -> None:
        self._transport = transport
        self._buffer = TailBuffer(max_size=max_size)
        self._send_lock = threading.Lock()
        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._pump_thread.start()

    def __del__(self) -> None:
        self.close()

    def _pump(self) -> None:
        while not self._transport.is_closed:
            try:
                data = self._transport.recv()
                if not data:
                    break
                self._buffer.write(data)
            except Exception:
                # 记录日志并退出（或根据需要重试）
                break
        self._buffer.close()

    def send(self, data: bytes) -> None:
        with self._send_lock:
            self._transport.on_input(data)

    def tail(self) -> Session:
        """创建新的只读会话。"""
        return Session(self._buffer)

    def close(self) -> None:
        """显式关闭引擎，停止泵线程并释放资源。"""
        self._buffer.close()
        self._transport.close()