"""
@文件: messages.py
@作者: 雷小鸥
@日期: 2026/5/30 17:48
@许可: MIT License
@描述: StreamPromptTui 消息定义 — 所有消息继承 textual.message.Message
@版本: Version 0.1
"""
from typing import Any
from enum import Enum, auto

from textual.message import Message


# ── Role 枚举 ──────────────────────────────────────────
# LineMsg.role 取值唯一来源。match/case 点号访问即值比较。
class Role(Enum):
    INPUT = auto()   # 用户输入行（EditorView → ShowView / IBridge）
    BRIDGE = auto()  # IBridge 外部推送 → ShowView


class LineMsg(Message):
    """公共行消息，双向通道。

    line 字段承载文本内容。如需传递富文本（rich Text 等结构体），
    可将文本放入 params 字典，让 line 退化为辅助标识。

    发送方：
      - EditorView 提交用户输入时发出
      - 外部业务逻辑推送输出时发出

    接收方：
      - ShowView 接收后渲染输出到屏幕
      - IBridge 接收后解包 line 转发给 on_data()
    """

    def __init__(self, role: Role, line: str, params: dict[str, Any] | None = None) -> None:
        self.role = role
        self.line = line
        self.params = params if params is not None else {}
        super().__init__()


class HistoryMsg(Message):
    """KeyController → EditorView：翻阅历史命令。

    direction = -1 → 上一条
    direction =  1 → 下一条
    """

    def __init__(self, direction: int) -> None:
        self.direction = direction
        super().__init__()


class SubmitMsg(Message):
    """KeyController → EditorView：提交当前输入。无额外字段。"""
    pass


# ═══════════════════════════════════════════════════════════
#  补全相关消息
# ═══════════════════════════════════════════════════════════

class CompletionMsg(Message):
    """KeyController → CompletionView：通知发送补全列表。"""
    pass


class CompletionListMsg(Message):
    """
    CompletionView → EditorView：发送补全 keys 列表。

    completions: 补全候选项列表（补全字典的全部键）。
    """

    def __init__(self, completions: list[str]) -> None:
        self.completions = completions
        super().__init__()


class CompletionSelectMsg(Message):
    """KeyController → CompletionView：补全栏滚动。

    仅影响 CompletionView 自身显示偏移，与其他视图无关。越界循环。
    direction =  1 → 向列表尾部滚动
    direction = -1 → 向列表头部滚动
    """

    def __init__(self, direction: int) -> None:
        self.direction = direction
        super().__init__()
