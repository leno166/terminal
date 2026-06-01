
"""
@文件: __main__.py
@作者: 雷小鸥
@日期: 2026/5/28 11:01
@许可: MIT License
@描述: 
@版本: Version 0.1
"""
import time
from collections import deque
import threading
from . import Session, Transport


class ExpTransport(Transport):
    def __init__(self):
        self._cond = threading.Condition()
        self._msgs = deque()
        self._closed = False

        self._thread = threading.Thread(
            target=self._run,
            daemon=True
        )
        self._thread.start()

    def _close(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    @property
    def is_closed(self) -> bool:
        return self._closed

    def _run(self):
        count = 0

        while not self._closed:
            count += 1
            time.sleep(0)

            with self._cond:
                self._msgs.append(f"{count}".encode())
                self._cond.notify_all()

    def send(self, data: bytes):
        with self._cond:
            self._msgs.append(data)
            self._cond.notify_all()

    def recv(self) -> bytes:
        with self._cond:

            while not self._msgs and not self._closed:
                self._cond.wait()

            if self._closed:
                raise EOFError

            msg = self._msgs.popleft()

            return msg


session = Session(ExpTransport())
tail = session.tail()

# 消费者1：发命令 + 读响应
session.send(b"AT\r\n")
for line in tail:
    if b"AT" in line:
        break

# 消费者2：tail -f 模式
for i, line in enumerate(tail):  # 同一个 session，新 for = 新游标
    print(f"{i}: {line}")
    if i >= 10:
        break

# 消费者3：纯写
session.send(b"cmd1")
session.send(b"cmd2")
session.send(b"cmd3")