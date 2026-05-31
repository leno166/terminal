"""
@文件: views.py
@作者: 雷小鸥
@日期: 2026/5/30 17:41
@许可: MIT License
@描述: StreamPromptTui 视图组件
@版本: Version 0.1
"""
import logging

from rich.text import Text
from rich.console import RenderableType
from textual.widgets import RichLog, Input
from textual.widget import Widget
from .messages import SubmitMsg, HistoryMsg, CompletionListMsg, LineMsg, CompletionSelectMsg, CompletionMsg, Role
from .helper import LineRenderer
from .event_bus import on, emit

logger = logging.getLogger("StreamPromptTui")


# ═══════════════════════════════════════════════════════════
#  EditorView
# ═══════════════════════════════════════════════════════════

class EditorView(Widget):
    """编辑区视图，包含一个输入框，支持历史、补全和提交。"""

    def __init__(self, input_prompt: str) -> None:
        super().__init__()
        self._input_prompt = input_prompt
        self._history: list[str] = []  # 后续限制最大长度
        self._idx: int = -1  # -1 表示新鲜输入；>=0 表示显示第几条历史
        self._suppress_change_event = False  # 避免程序修改触发重置
        self.input = Input()

    def _set_input_value(self, value: str) -> None:
        """安全设置输入框内容，不触发历史模式重置。"""
        self._suppress_change_event = True
        try:
            self.input.value = value
            # 光标移到末尾，提升体验
            self.input.action_end()
        finally:
            self._suppress_change_event = False

    # ── 事件处理 ──────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        """用户手动编辑输入框时，退出历史模式（_idx = -1）。"""
        if self._suppress_change_event:
            return
        if self._idx != -1:
            self._idx = -1

    # ── 入站消息处理 ──────────────────────────────────────

    def on_submit_msg(self, msg: SubmitMsg) -> None:
        """处理 SubmitMsg。

        1. 取出当前编辑区文本
        2. 清空编辑区
        3. 复制一份添加到 self._history，更新 self._idx
        4. 构造 LineMsg 发出（→ ShowView 渲染，→ 外部回调消费）
        """
        cur_str = self.input.value
        logger.debug("EditorView.on_submit_msg: 收到提交, 输入内容=%s", cur_str)
        self._set_input_value("")
        if not cur_str.strip():
            logger.debug("EditorView.on_submit_msg: 空输入, 跳过")
            return
        self._history.append(cur_str)
        self._idx = -1
        # params['prompt'] 与 LineRenderer 约定同一 key，修改需同步
        emit(LineMsg(role=Role.INPUT, line=cur_str, params={'prompt': self._input_prompt + ' '}))

    def on_history_msg(self, msg: HistoryMsg) -> None:
        """处理 HistoryMsg。

        direction = -1 → 上一条，direction = 1 → 下一条。
        """
        if not self._history:
            return

        new_idx = self._idx + msg.direction

        if new_idx == -2:
            new_idx = len(self._history) - 1
        elif new_idx == -1:
            new_idx = 0
        elif new_idx > len(self._history) - 1:
            new_idx = len(self._history) - 1

        if new_idx == self._idx:
            return

        self._idx = new_idx

        self._set_input_value(self._history[self._idx])

    def on_completion_list_msg(self, msg: CompletionListMsg) -> None:
        """处理 CompletionListMsg。

        1. 取出当前编辑区文本
        2. 与补全列表比对，取第一个前缀匹配项
        3. 唯一匹配时替换编辑区文本；无匹配或多匹配则无反应
        """
        cur_str = self.input.value
        matches = [item for item in msg.completions if item.startswith(cur_str)]
        if not matches or len(matches) > 1:
            return

        self._set_input_value(matches[0])

    # ── 渲染 ────────────────────────────────────────────

    def compose(self):
        yield self.input

    def on_mount(self) -> None:
        """组件挂载：订阅消息并聚焦输入框。"""
        on(SubmitMsg, self.on_submit_msg)
        on(HistoryMsg, self.on_history_msg)
        on(CompletionListMsg, self.on_completion_list_msg)
        logger.debug("EditorView.on_mount: 订阅完成, 聚焦输入框")
        self.input.focus()


# ═══════════════════════════════════════════════════════════
#  ShowView
# ═══════════════════════════════════════════════════════════

class ShowView(Widget):
    """输出展示区视图。

    接收 LineMsg，交给 LineRenderer 渲染后输出到屏幕，自动滚动。
    LineRenderer 内嵌在本视图，不走消息通道。
    """

    def __init__(self, line_render: LineRenderer) -> None:
        super().__init__()
        self._line_renderer = line_render or LineRenderer()
        # highlight / markup 仅对字符串写入生效；LineRenderer 返回 RenderableType，
        # 渲染控制权全在 LineRenderer，此处无需设置。
        self._log = RichLog(auto_scroll=True)
        self._log.can_focus = False

    def on_line_msg(self, msg: LineMsg) -> None:
        """接收 LineMsg（来自 EditorView 提交或外部推送），渲染并输出到屏幕。

        1. LineRenderer.render(msg) → 带高亮的富文本
        2. 追加到输出区域
        3. 自动滚动到底部
        """
        logger.debug("ShowView.on_line_msg: 收到行消息 role=%s line=%s", msg.role, msg.line)
        rendered: RenderableType = self._line_renderer.render(msg)
        self._log.write(rendered)

    # ── 渲染 ────────────────────────────────────────────

    def on_mount(self) -> None:
        """组件挂载：订阅 LineMsg。"""
        on(LineMsg, self.on_line_msg)
        logger.debug("ShowView.on_mount: 订阅完成")

    def compose(self):
        yield self._log


# ═══════════════════════════════════════════════════════════
#  CompletionView
# ═══════════════════════════════════════════════════════════

class CompletionView(Widget):
    """水平滚动的补全栏。收到 CompletionMsg 发送全部 keys，
    收到 CompletionSelectMsg 滚动查看，越界循环。"""

    _instance_counter: int = 0

    def __init__(self) -> None:
        super().__init__()
        CompletionView._instance_counter += 1
        self._instance_id = CompletionView._instance_counter
        self._completion_dict: dict[str, str] = {}
        self._offset: int = 0  # 当前显示窗口起始位置
        self._items: list[str] = []
        logger.info("[CV#%d] __init__  id=0x%x", self._instance_id, id(self))

    def on_mount(self) -> None:
        """组件挂载：订阅消息。"""
        on(CompletionMsg, self.on_completion_msg)
        on(CompletionSelectMsg, self.on_completion_select_msg)
        logger.debug("[CV#%d] on_mount: 订阅完成", self._instance_id)

    # ── 对外可配置 ────────────────────────────────────────

    def set_completion_dict(self, d: dict[str, str]) -> None:
        """注入补全字典。键为补全文本，值为关联数据。"""
        self._completion_dict = d
        self._offset = 0
        sorted_keys = sorted(self._completion_dict.keys())
        self._items = [f"{k}: {self._completion_dict[k]}" for k in sorted_keys]
        logger.info("[CV#%d] set_completion_dict  id=0x%x  count=%d  keys=%s", self._instance_id, id(self), len(d), sorted_keys)
        self.refresh()

    # ── 入站消息处理 ──────────────────────────────────────

    def on_completion_msg(self, msg: CompletionMsg) -> None:
        """收到 CompletionMsg：发送 CompletionListMsg，携带全部字典 keys。

        CompletionView 不做过滤，EditorView 收到后自行做前缀匹配。
        """
        keys = list(self._completion_dict.keys())
        logger.info("[CV#%d] on_completion_msg  id=0x%x  dict_size=%d  keys=%s", self._instance_id, id(self), len(self._completion_dict), keys)
        emit(CompletionListMsg(completions=keys))

    def on_completion_select_msg(self, msg: CompletionSelectMsg) -> None:
        """收到 CompletionSelectMsg：滚动补全栏显示偏移，越界循环。

        仅维护 CompletionView 自身显示状态，与其他视图无关。
        direction =  1 → 向列表尾部滚动
        direction = -1 → 向列表头部滚动
        """
        count = len(self._completion_dict)
        logger.debug("[CV#%d] on_completion_select_msg  id=0x%x  direction=%d  count=%d  old_offset=%d", self._instance_id, id(self), msg.direction, count, self._offset)
        if count == 0:
            return
        self._offset = (self._offset + msg.direction) % count

        self.refresh()

    # ── 渲染 ────────────────────────────────────────────

    def render(self) -> Text:
        if not self._items:
            return Text("(no completions)")

        # 获得当前组件可用宽度（字符数），如果尚未布局则使用控制台宽度
        width = self.size.width
        if width <= 1:
            width = self.app.console.width - 2  # 留一点边距
        if width <= 5:
            return Text("...")

        # 根据 offset 生成循环列表
        rotated = self._items[self._offset:] + self._items[:self._offset]

        # 分隔符
        separator = " | "
        sep_len = len(separator)

        # 尝试从第一个条目开始拼接，直到宽度不够为止
        result_parts = []
        current_len = 0
        truncated_tail = False

        for idx, item in enumerate(rotated):
            item_len = len(item)
            add_len = (sep_len if idx > 0 else 0) + item_len
            if current_len + add_len <= width:
                if idx > 0:
                    result_parts.append(separator)
                result_parts.append(item)
                current_len += add_len
            else:
                truncated_tail = True
                break

        # 如果被截断了，在末尾添加 " ..."
        if truncated_tail:
            if current_len + 4 <= width:
                result_parts.append(" ...")
            else:
                if result_parts:
                    if len(result_parts) >= 2 and result_parts[-2] == separator:
                        result_parts.pop()
                    result_parts.pop()
                    temp_text = Text().join(result_parts)
                    current_len = temp_text.cell_len
                    if current_len + 4 <= width:
                        result_parts.append(" ...")
                    else:
                        result_parts = ["..."]
                else:
                    result_parts = ["..."]

        return Text("".join(result_parts))
