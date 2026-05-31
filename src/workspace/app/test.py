"""
@文件: test.py
@作者: 雷小鸥
@日期: 2026/5/30 15:34
@许可: MIT License
@描述:

@版本: Version 0.1
"""
import logging
import threading
from pathlib import Path

from pynput import mouse

from src.workspace.module.StreamPromptTui import App

logger = logging.getLogger("MouseBridge")


class MouseBridge(App):
    """监听鼠标移动、点击、滚动，将事件转为文本并通过 send() 推送到 TUI。"""

    def __init__(self):
        super().__init__()
        self.running = True
        self.listener = None

        # 启动鼠标监听线程（pynput 的 Listener 会自己启动线程）
        self._start_listener()

    def _start_listener(self):
        """创建并启动鼠标监听器。"""
        self.listener = mouse.Listener(
            on_move=self._on_move,
            on_click=self._on_click,
            on_scroll=self._on_scroll,
        )
        self.listener.start()
        logger.info("鼠标监听器已启动")

    def _on_move(self, x, y):
        """鼠标移动时调用。"""
        if not self.running:
            return
        # 为避免数据过多，可以限制发送频率（例如每 50ms 一次），这里为了演示直接发送
        self.send(f"🖱️ 移动 → ({x}, {y})")

    def _on_click(self, x, y, button, pressed):
        """鼠标点击时调用。"""
        if not self.running:
            return
        action = "按下" if pressed else "释放"
        self.send(f"🖱️ 点击 [{action}] {button} @ ({x}, {y})")

    def _on_scroll(self, x, y, dx, dy):
        """鼠标滚轮滚动时调用。"""
        if not self.running:
            return
        self.send(f"🖱️ 滚动 Δ({dx}, {dy}) @ ({x}, {y})")

    def on_data(self, line: str) -> None:
        """
        用户在输入框提交消息时调用。
        这里简单回显，也可以添加与鼠标交互的命令（例如临时停止监听等）。
        """
        logger.info("用户输入: %s", line)
        self.send(f"[回复] 收到命令: {line}")
        # 可选：支持动态开关监听
        if line.strip().lower() == "stop":
            self.running = False
            self.send("🛑 鼠标监听已停止")
        elif line.strip().lower() == "start":
            if not self.running:
                self.running = True
                self._start_listener()
                self.send("▶️ 鼠标监听已重新启动")

    def stop(self):
        """停止监听器。"""
        self.running = False
        if self.listener and self.listener.running:
            self.listener.stop()
        logger.info("鼠标监听器已停止")


# ═══════════════════════════════════════════════════════════
#  启动入口
# ═══════════════════════════════════════════════════════════

def main():
    log_file = Path(__file__).parent / "mouse_bridge.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)-7s] [%(threadName)s] %(name)s - %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(str(log_file), mode="w", encoding="utf-8"),
        ],
    )
    logging.getLogger("textual").setLevel(logging.WARNING)

    logger.info("===== 鼠标桥接器启动（pynput 模式），日志文件: %s =====", log_file)

    bridge = MouseBridge()

    completion_dict = {
        "help": "显示帮助（无实际效果）",
        "stop": "停止鼠标监听",
        "start": "重新启动鼠标监听",
        "quit": "退出程序（按 Ctrl+C）",
    }

    try:
        bridge.run(completion_dict=completion_dict, input_prompt="🐭 鼠标监控 > ")
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在退出...")
    finally:
        bridge.stop()
        logger.info("程序已退出")


if __name__ == "__main__":
    main()