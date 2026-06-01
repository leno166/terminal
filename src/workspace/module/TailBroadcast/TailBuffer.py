"""
@文件: TailBuffer.py
@作者: 雷小鸥
@日期: 2026/5/28 10:56
@许可: MIT License
@描述:
@版本: Version 0.1
"""
import threading


class Cursor:
    __slots__ = ("pos",)

    def __init__(self, start_pos: int = -1) -> None:
        self.pos = start_pos  # 游标当前指向的已消费序号，-1 表示尚未读取任何数据


class TailBuffer:
    """定长环形缓冲 + 多消费者独立游标。单写多读，使用单一条件变量，高效无遍历唤醒。"""

    def __init__(self, max_size: int = 256) -> None:
        self._max_size = max_size
        self._ring: list[tuple[int, bytes] | None] = [None] * max_size
        self._write_seq: int = -1  # 最后写入的序号
        self._cursors: list[Cursor] = []  # 所有活跃的游标（用于取消订阅）
        self._closed: bool = False
        self._cond = threading.Condition()  # 唯一条件变量，关联锁用于保护所有状态

    def write(self, data: bytes) -> None:
        """生产者写入数据，唤醒所有等待的消费者。"""
        with self._cond:
            if self._closed:
                return
            self._write_seq += 1
            idx = self._write_seq % self._max_size
            self._ring[idx] = (self._write_seq, data)
            self._cond.notify_all()  # 只唤醒一个条件变量，不遍历消费者

    def close(self) -> None:
        """关闭缓冲区，唤醒所有等待的消费者，使其抛出 StopIteration。"""
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def subscribe(self) -> Cursor:
        """消费者订阅：返回一个从当前最新序号开始的游标。"""
        with self._cond:
            cursor = Cursor(start_pos=self._write_seq)  # 新游标指向最新已写入位置（尚未消费）
            self._cursors.append(cursor)
            return cursor

    def unsubscribe(self, cursor: Cursor) -> None:
        """消费者取消订阅。幂等操作。"""
        with self._cond:
            try:
                self._cursors.remove(cursor)
            except ValueError:
                pass

    def read_next(self, cursor: Cursor, timeout: float | None = None) -> bytes:
        """
        阻塞读取下一条数据。
        返回 bytes 数据。
        如果缓冲区已关闭或游标数据已被覆盖，抛出 StopIteration。
        若 timeout 超时（未实现完整超时，可扩展），暂不实现。
        """
        if cursor not in self._cursors:
            raise StopIteration

        with self._cond:
            while True:
                if self._closed:
                    raise StopIteration

                # 检查是否有新数据可读
                if cursor.pos < self._write_seq:
                    cursor.pos += 1
                    idx = cursor.pos % self._max_size
                    entry = self._ring[idx]
                    if entry is not None and entry[0] == cursor.pos:
                        return entry[1]
                    else:
                        # 数据已被覆盖，消费者过慢
                        raise StopIteration

                # 没有新数据，等待
                if not self._cond.wait(timeout):
                    raise StopIteration
