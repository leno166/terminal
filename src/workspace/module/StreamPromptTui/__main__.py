"""
@文件: __main__.py
@作者: 雷小鸥
@日期: 2026/5/30 21:11
@许可: MIT License
@描述: 开发测试入口 — 演示 EventBus 对外接入：
       1. IBridge：队列 + 后台线程消费，外部只跟 str 打交道
       2. 外部回显用户输入（追加 "xxx"）
       3. 后台定时输出数字（1s 一个字符串）
@版本: Version 0.1
"""
import logging
import threading
import time
from pathlib import Path

from . import _IBridge

logger = logging.getLogger("StreamPromptTui")


# ═══════════════════════════════════════════════════════════
#  业务层：继承 IBridge，实现 on_data
# ═══════════════════════════════════════════════════════════

class MyBridge(_IBridge):
    """收到任意行文本时，追加 'xxx' 后 send 回去。"""

    def __init__(self):
        super().__init__()
        threading.Thread(target=self._bg_number_output, daemon=True).start()
        logger.info("后台数字输出线程已启动")

    # ── 后台线程：每秒 send 一个数字字符串 ──────────────
    def _bg_number_output(self) -> None:
        i = 0
        while True:
            time.sleep(1)
            i += 1
            self.send(str(i))

    # ── 实现 IBridge.on_data ───────────────────────────

    def on_data(self, line: str) -> None:
        logger.info("外部收到: %s", line)
        self.send(f"{line}xxx")


# ═══════════════════════════════════════════════════════════
#  main
# ═══════════════════════════════════════════════════════════

def main():
    # ── 日志配置 ──────────────────────────────────────────
    log_file = Path(__file__).parent / "stream_prompt_tui.log"

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)-7s] "
               "[%(threadName)s:%(thread)d] "
               "%(name)s "
               "%(filename)s:%(lineno)d "
               "%(funcName)s() - %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(str(log_file), mode="w", encoding="utf-8"),
        ],
    )

    logging.getLogger("textual").setLevel(logging.WARNING)

    logger = logging.getLogger("StreamPromptTui")
    logger.info("===== 启动 log=%s =====", log_file)

    # ── 构建桥接器并启动应用 ──────────────────────────────
    bridge = MyBridge()
    bridge.run({
        "hello": "greeting",
        "help": "show help",
        "quit": "exit app",
        "status": "check status",
    })


if __name__ == "__main__":
    main()
