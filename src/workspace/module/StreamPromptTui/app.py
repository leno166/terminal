"""
@文件: app.py
@作者: 雷小鸥
@日期: 2026/5/30 20:57
@许可: MIT License
@描述: 
@版本: Version 0.1
"""
from textual.app import App
from textual.containers import Container
from textual.widgets import Footer, Header
from logging import getLogger
from .views import EditorView, ShowView, CompletionView
from .helper import KeyController,LineRenderer
from .event_bus import emit

logger = getLogger(__name__)


class TuiApp(App):
    """主应用，通过 LineMsg 实现双向通信：编辑区提交 → LineMsg 发出；外部推送 → LineMsg 进入 ShowView 渲染"""

    CSS = """
            #show_container {
                height: 1fr;
            }
            #editor_container {
                height: 3;    
            }
            #completion_container {
                height: 1;
            }
        """

    def __init__(self, completion_dict: dict[str, str], input_prompt: str = None, renderer_kwargs: dict = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._input_prompt = input_prompt or '>>'
        self._completion_dict = completion_dict
        self._renderer_kwargs = renderer_kwargs or {}
        self._key_controller = KeyController()

    def on_key(self, event) -> None:
        """全局按键处理，将按键转换为消息并投递"""
        if event.is_printable:
            return
        logger.debug("on_key: key=%s", event.key)
        msg = self._key_controller.dispatch(event)
        if msg:
            emit(msg)
            event.prevent_default()

    def compose(self):
        yield Header()
        with Container(id="show_container"):
            yield ShowView(LineRenderer(**self._renderer_kwargs))
        with Container(id="editor_container"):
            yield EditorView(self._input_prompt)
        with Container(id="completion_container"):
            completer = CompletionView()
            completer.set_completion_dict(self._completion_dict)
            yield completer
        yield Footer()
