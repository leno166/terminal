"""
@文件: DuplexTransport.py
@作者: 雷小鸥
@日期: 2026/5/28 10:54
@许可: MIT License
@描述:
@版本: Version 0.1
"""
from abc import ABC, abstractmethod


class DuplexTransport(ABC):
    """双工传输抽象——用户为 SSH/串口/HTTP 等实现此接口。"""

    def __del__(self) -> None:
        self._close()

    @abstractmethod
    def _close(self) -> None:
        """关闭传输，唤醒所有阻塞的 recv()。"""
        ...

    @property
    @abstractmethod
    def is_closed(self) -> bool:
        """传输是否已关闭。"""
        ...

    @abstractmethod
    def on_input(self, data: bytes) -> None:
        ...

    @abstractmethod
    def recv(self) -> bytes:
        """从对端读取数据。阻塞调用，由后台线程独占调用。"""
        ...

    def close(self) -> None:
        self._close()
