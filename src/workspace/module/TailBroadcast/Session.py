"""
@文件: Session.py
@作者: 雷小鸥
@日期: 2026/5/28 10:58
@许可: MIT License
@描述:
@版本: Version 0.1
"""
from .TailBuffer import TailBuffer, Cursor


class Session:
    """只读消费者视图，实现迭代器协议，自动管理游标订阅/取消订阅。"""

    def __init__(self, buffer: TailBuffer) -> None:
        self._buffer = buffer
        self._cursor: Cursor = buffer.subscribe()
        self._active = True

    def __iter__(self):
        self._close()
        self._cursor = self._buffer.subscribe()
        self._active = True
        return self

    def __next__(self) -> bytes:
        if not self._active:
            raise StopIteration
        try:
            return self._buffer.read_next(self._cursor)
        except StopIteration:
            self._close()
            raise

    def __del__(self):
        """析构时尝试清理，但不保证一定调用（建议显式 close）。"""
        self._close()

    def _close(self) -> None:
        if self._active:
            self._active = False
            self._buffer.unsubscribe(self._cursor)

    def close(self) -> None:
        """显式关闭 Session，立即取消订阅。"""
        self._close()
