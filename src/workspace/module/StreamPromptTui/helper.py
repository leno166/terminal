"""
@文件: helper.py
@作者: 雷小鸥
@日期: 2026/5/31 18:05
@许可: MIT License
@描述:

    LineRenderer — 将 LineMsg 渲染为 rich Text，内嵌在 ShowView，不走消息通道。

    KeyController — 按键到消息的纯映射器，不继承 Widget。

@版本: Version 0.1
"""
from rich.text import Text

from .messages import LineMsg, Role
from logging import getLogger
from typing import Callable
from textual import events
from textual.message import Message

from .messages import (
    HistoryMsg,
    CompletionMsg,
    CompletionSelectMsg,
    SubmitMsg,
)

logger = getLogger(__name__)


class KeyController:
    """按键 → 消息 映射器。

    纯数据/逻辑类，不继承 Widget，不参与 DOM 树，不持有 App 引用。
    由 TerminalWidget（或 App）持有实例并调用 dispatch()。

    调用方负责：
      - 将 Textual Key 事件传给 dispatch()
      - 将返回的消息通过 post_message() 投递
      - 对已处理按键调用 event.prevent_default()
      - Ctrl+Q / Ctrl+C 等退出快捷键由 App 层 BINDINGS 处理
    """

    # ── 按键映射表 ────────────────────────────────────────
    # 只在此处定义按键映射，改键位只改这里。
    # key_name → factory() -> Message

    _KEY_MAP: dict[str, Callable[[], Message]] = {
        "up"      : lambda: HistoryMsg(direction=-1),
        "down"    : lambda: HistoryMsg(direction=1),
        "tab"     : lambda: CompletionMsg(),
        "pageup"  : lambda: CompletionSelectMsg(direction=-1),
        "pagedown": lambda: CompletionSelectMsg(direction=1),
        "enter"   : lambda: SubmitMsg(),
    }

    def dispatch(self, event: events.Key) -> Message | None:
        """查表将按键事件转为消息。未匹配返回 None。"""
        factory = self._KEY_MAP.get(event.key)
        if factory is None:
            return None
        msg = factory()
        logger.debug("dispatch: key=%s → msg=%s", event.key, type(msg).__name__)
        return msg


class LineRenderer:
    """行渲染器。将 LineMsg 转为 rich.renderable 输出到屏幕。

    后续在此扩展：代码高亮、日志高亮、shell 命令高亮、关键字高亮、markdown 渲染。
    目前原样返回。

    RichLog 的 highlight / markup 参数仅对字符串写入生效；本渲染器返回 RenderableType，
    因此所有渲染控制权在此集中管理，不依赖 RichLog 内置处理。
    """

    def __init__(self, **kwargs) -> None:
        """初始化渲染器。

        预留扩展参数（例如 enable_highlight、enable_markdown），
        后续 render() 可根据这些选项切换渲染策略。
        """
        self._options = kwargs

    def render(self, msg: LineMsg) -> Text:
        """渲染一条 LineMsg 为 rich Text。

        后续可在此按 role / params 选择不同的高亮策略。
        """
        # params['prompt'] 与 EditorView 约定同一 key，修改需同步
        match msg.role:
            case Role.INPUT:
                text = Text(msg.params.get('prompt', '') + msg.line)
            case _:
                text = Text(msg.line)

        return text
