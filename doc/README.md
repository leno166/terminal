# StreamPromptTui

> 一个面向开发者的即插即用**流观测框架**。接入任意数据源，立即获得统一的交互式调试界面。

---

## 这是什么

你在调试串口设备、操作诊断协议、或者监控外设输入时，是不是每次都得写一堆 `while True: print(input(">> "))` 的循环？

**StreamPromptTui** 给你一个开箱即用的交互式终端界面。你只需要实现两个方法——`on_data(str)` 接收输入、`send(str)` 推送输出。剩下的——历史翻阅、Tab 补全、自动滚屏、按键映射——全部内置。

```
你的数据源（UART / DoIP / Socket / …）  ←→  StreamPromptTui  ←→  你的眼睛和键盘
     ↑                        ↑
   你只需关心这里             这个你完全不用管
```

## 核心能力

| 能力 | 说明 |
|------|------|
| 行协议双向通信 | `send(str)` 推输出，`on_data(str)` 收输入，其余不关心 |
| 历史翻阅 | ↑↓ 键浏览命令历史，支持边界循环 |
| Tab 补全 | 预置命令字典，输入前缀匹配后一键补全 |
| 补全栏浏览 | PgUp/PgDn 滚动查看所有可用命令 |
| 自动滚屏 | 输出区自动追随最新内容 |
| 非文本扩展 | 通过 `params` 字典传递富文本，`line` 退化为标识 |
| 线程安全 | `send()` 可在任意线程调用，内部队列保证顺序 |

## 快速开始

```bash
pip install StreamPromptTui   # 待发布
```

### 最小示例：回显

```python
from StreamPromptTui import App

class MyConsole(App):
    def on_data(self, line: str) -> None:
        """用户在终端输入了什么。"""
        self.send(f"echo: {line}")   # 输出到屏幕

MyConsole().run(
    completion_dict={"hello": "打招呼", "quit": "退出"},
    input_prompt=">> ",
)
```

跑起来就是：一个带提示符、补全、历史的交互式终端。你只写了 3 行逻辑。

### 进阶：后台线程推送

```python
import threading
import time

class MonitorConsole(App):
    def __init__(self):
        super().__init__()
        # 后台每秒推送一次状态
        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self):
        while True:
            time.sleep(1)
            self.send(f"[{time.ctime()}] 系统正常")

    def on_data(self, line: str):
        if line == "status":
            self.send("状态：运行中")
        elif line == "quit":
            self.send("再见")
            self.app.exit()
```

## 典型场景

### 🖥️ 串口设备调试

```python
import serial
import threading

class UARTConsole(App):
    def __init__(self, port: str, baud: int):
        super().__init__()
        self._ser = serial.Serial(port, baud, timeout=0.1)
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self):
        """持续从串口读数据，推送到屏幕。"""
        while True:
            line = self._ser.readline().decode().strip()
            if line:
                self.send(f"📥 {line}")

    def on_data(self, line: str):
        """用户输入 → 发到串口。"""
        self._ser.write((line + "\r\n").encode())

# 打开串口，立即获得交互式监控界面
UARTConsole(port="COM3", baud=115200).run(
    completion_dict={"AT": "AT 指令", "AT+VERSION": "查询版本"},
    input_prompt="UART> ",
)
```

### 🔌 DoIP 诊断操作

```python
class DoIPConsole(App):
    def __init__(self, ip, ecus):
        super().__init__()
        self._session = DoIpSession(ip=ip, ecus=ecus)

    def on_data(self, line: str):
        parts = line.split()
        if parts[0] == "@":
            self._session @ parts[1]          # 切换 ECU
            self.send(str(self._session))
        else:
            resp = self._session >> parts[0]   # 发诊断指令
            self.send(f"RX: {resp}")

DoIPConsole(ip="198.18.32.1", ecus={"198.18.36.1": 0x1301}).run(
    completion_dict={"1001": "默认会话", "1003": "扩展会话", "@": "切换ECU"},
    input_prompt="DoIP> ",
)
```

### 🐭 监控外设输入

```python
import pynput

class MouseMonitor(App):
    def __init__(self):
        super().__init__()
        def on_move(x, y):
            self.send(f"鼠标位置: ({x}, {y})")
        self._listener = pynput.mouse.Listener(on_move=on_move)
        self._listener.start()

    def on_data(self, line: str):
        if line == "stop":
            self._listener.stop()
            self.send("监听已停止")

MouseMonitor().run(
    completion_dict={"stop": "停止监听"},
    input_prompt="🐭> ",
)
```

### 📡 子进程输出监控

```python
import subprocess
import threading

class ProcConsole(App):
    def __init__(self, cmd: list[str]):
        super().__init__()
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self):
        for line in self._proc.stdout:
            self.send(line.rstrip())

    def on_data(self, line: str):
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

ProcConsole(["python", "my_server.py"]).run(
    completion_dict={"reload": "重载配置", "status": "查看状态"},
    input_prompt="server> ",
)
```

## 架构概览

```
                    ┌─────────────┐
  你的数据源  ◀─────│  IBridge    │──────▶  终端屏幕
  (串口/Socket/…)   │             │        (输出展示)
                    │ on_data()   │
  你的业务逻辑  ────▶│ send()      │◀─────  用户键盘
                    └─────────────┘        (编辑输入)
```

你只需要关心两件事：
1. **`on_data(line: str)`** — 用户在终端输入了一行，你要怎么处理？
2. **`send(line: str)`** — 你想在屏幕上显示什么？

其余的——提示符样式、历史管理、补全匹配、按键映射、滚屏策略——框架已经做了。

## 设计哲学

- **不关心你的数据语义。** 你传的是 AT 指令、诊断码、还是 JSON，框架一律当字符串处理。含义由你定义。
- **不强占控制流。** `send()` 是线程安全的，你可以在后台线程、回调、协程中任意调用。输入和输出完全解耦。
- **提供脚手架而非围墙。** 基础交互（历史、补全、滚屏）开箱即用；高级需求（语法高亮、自定义渲染）通过 `renderer_kwargs` 扩展。
- **一文件接入。** 外部使用者只需 `from StreamPromptTui import App`，不需要知道 `EventBus`、`LineMsg`、`KeyController` 的存在。

## 对比

| | 裸 print/input | StreamPromptTui |
|------|-----------------|-----------------|
| 历史翻阅 | ❌ | ✅ ↑↓ 浏览 |
| Tab 补全 | ❌ | ✅ 命令字典驱动 |
| 滚动输出 | 手动清屏 | ✅ RichLog 自动滚屏 |
| 后台推送 | print 线程不安全 | ✅ 内部队列保证 |
| 界面 | 纯文本流 | ✅ Header/Footer/补全栏 |
| 代码量 | 每次重写循环 | ✅ 继承 IBridge 即可 |

## 内部文档

代码结构、消息体系、组件树等细节见 [`StreamPromptTui-design.md`](StreamPromptTui-design.md)。

## License

MIT