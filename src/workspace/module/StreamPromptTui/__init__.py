"""
@文件: __init__.py
@作者: 雷小鸥
@日期: 2026/5/30 16:12
@许可: MIT License
@描述: StreamPromptTui — 基于 Textual 的流式终端提示/响应交互界面
@版本: Version 0.1
"""
import queue
import threading
import logging
from abc import ABC as _ABC, abstractmethod as _abstractmethod

from .app import TuiApp as _TuiApp
from .messages import LineMsg, Role
from . import event_bus

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
#  IBridge — 行桥接器（包的门户 + 抽象接口）
# ═══════════════════════════════════════════════════════════

class _IBridge(_ABC):
    """行桥接器。外部只跟字符串打交道，内部负责 LineMsg 的拼包/解包。

    send(line_str)   — 入队一个字符串（线程安全），后台线程拼成 LineMsg 发出。
    on_data(line_str) — 收到行文本时回调（抽象，子类必须实现）。
    run(...)          — 启动 TUI 应用（阻塞直到退出）。

    队列 + 单消费线程，保证 on_data 里调 send 不会嵌套 emit —
    send 只是 push 队列，实际 emit 在另一个线程里排队执行。
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        event_bus.on(LineMsg, self._on_event_msg)
        self._worker.start()
        self.app = None

    # ── 公开接口 ──────────────────────────────────────

    def send(self, line: str) -> None:
        """入队一个字符串，后台线程取出后拼成 LineMsg 并 emit。线程安全。"""
        self._queue.put(line)

    @_abstractmethod
    def on_data(self, line: str) -> None:
        """收到行文本时的回调入口。子类必须实现。"""
        ...

    def run(self, completion_dict: dict[str, str] = None, input_prompt: str = None, renderer_kwargs: dict = None) -> None:
        """启动 TUI 应用（阻塞直到退出）。

        Args:
            completion_dict: 补全字典，键为补全文本，值为描述。
            input_prompt: 输入提示符，默认 '>>'。
            renderer_kwargs: LineRenderer 初始化参数，预留扩展（enable_highlight 等）。
        """
        self.app = _TuiApp(completion_dict, input_prompt, renderer_kwargs)
        self.app.run()

    # ── 内部实现 ──────────────────────────────────────

    def _on_event_msg(self, msg: LineMsg) -> None:
        """event_bus 订阅回调：解包 LineMsg.line → 交给 on_data。"""
        if msg.role == Role.BRIDGE:
            return
        self.on_data(msg.line)

    def _run(self) -> None:
        """后台线程：阻塞等队列，取出 str 拼成 LineMsg 后 emit。"""
        while True:
            line = self._queue.get()  # 阻塞等待
            msg = LineMsg(role=Role.BRIDGE, line=line)
            event_bus.emit(msg)


App = _IBridge

__all__ = ["App"]
