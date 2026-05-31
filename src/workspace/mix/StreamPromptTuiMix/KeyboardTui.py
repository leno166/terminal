"""
@文件: KeyboardTui.py
@作者: 雷小鸥
@日期: 2026/5/31 19:18
@许可: MIT License
@描述: 
@版本: Version 0.1
"""
#!/usr/bin/env python3
"""
@文件: KeyboardTui.py
@作者: 雷小鸥
@日期: 2026/5/31 19:30
@许可: MIT License
@描述: 键盘事件监控 TUI，实时显示所有按键（包括组合键），支持开始/停止监听。
@版本: Version 0.1
"""

import logging
from pathlib import Path

from pynput import keyboard

from src.workspace.module.StreamPromptTui import App

logger = logging.getLogger(__name__)


class KeyboardApp(App):
    """监听全局键盘事件，将按键信息通过 send() 推送到 TUI。"""

    def __init__(self):
        super().__init__()
        self.running = True
        self.listener = None
        self._start_listener()

    def _start_listener(self):
        """创建并启动键盘监听器（后台线程）。"""
        self.listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release
        )
        self.listener.start()
        logger.info("键盘监听器已启动")

    def _on_press(self, key):
        """按键按下时调用。"""
        if not self.running:
            return
        try:
            # 普通字符键
            key_repr = key.char
        except AttributeError:
            # 特殊键（如 Ctrl, Shift, F1 等）
            key_repr = str(key).replace('Key.', '')  # 变成 'ctrl', 'alt', 'f1' 等
        self.send(f"⌨️ 按下: {key_repr}")

    def _on_release(self, key):
        """按键释放时调用。"""
        if not self.running:
            return
        try:
            key_repr = key.char
        except AttributeError:
            key_repr = str(key).replace('Key.', '')
        self.send(f"⌨️ 释放: {key_repr}")

        # 可选：按 Esc 键自动退出（如果想加这个功能，取消下面注释）
        # if key == keyboard.Key.esc:
        #     self.send("⚠️ 按下了 ESC，正在退出...")
        #     self.stop()
        #     self.exit()  # 需要从 textual 导入 exit，不建议在这里处理，保持简洁

    def on_data(self, line: str) -> None:
        """
        用户输入命令时调用。
        支持 stop / start / clear 等。
        """
        logger.info("用户输入: %s", line)
        cmd = line.strip().lower()
        if cmd == "stop":
            if self.running:
                self.running = False
                self.send("🛑 键盘监听已停止")
            else:
                self.send("⚠️ 键盘监听已经是停止状态")
        elif cmd == "start":
            if not self.running:
                self.running = True
                self._start_listener()
                self.send("▶️ 键盘监听已重新启动")
            else:
                self.send("⚠️ 键盘监听已在运行中")
        elif cmd == "clear":
            # 清除屏幕（实际无法直接清除 RichLog，可以发送一个清屏提示或留空）
            self.send("🧹 请输入 :clear 来手动清屏（暂不支持自动清屏）")
        else:
            self.send(f"[回复] 收到命令: {line}")

    def stop(self):
        """停止监听器，释放资源。"""
        self.running = False
        if self.listener and self.listener.running:
            self.listener.stop()
        logger.info("键盘监听器已停止")


# ═══════════════════════════════════════════════════════════
#  启动入口
# ═══════════════════════════════════════════════════════════

def main():
    log_file = Path(__file__).parent / "keyboard_tui.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)-7s] [%(threadName)s] %(name)s - %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(str(log_file), mode="w", encoding="utf-8"),
        ],
    )
    logging.getLogger("textual").setLevel(logging.WARNING)

    logger.info("===== 键盘监控 TUI 启动，日志文件: %s =====", log_file)

    app = KeyboardApp()

    completion_dict = {
        "start": "启动键盘监听（如果已停止）",
        "stop": "停止键盘监听",
        "clear": "清屏提示（功能预留）",
        "quit": "退出程序（按 Ctrl+C）",
    }

    try:
        app.run(completion_dict=completion_dict, input_prompt="⌨️ 键盘监控 > ")
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在退出...")
    finally:
        app.stop()
        logger.info("程序已退出")


if __name__ == "__main__":
    main()