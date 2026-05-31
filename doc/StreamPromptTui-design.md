# StreamPromptTui 总体设计

基于 Python Textual 框架的流式终端交互 UI，只负责 UI 交互，不涉及业务逻辑。对外通过 `IBridge` 抽象类接入——外部只需实现 `on_data(str)` 回调、调用 `send(str)` 推送。若需传递非文本内容（如 `rich.Text` 等结构体），可将富文本对象放入 `params` 字典，`line` 退化为辅助标识即可，完全不用接触消息类或 Textual 内部细节。

## 文件结构

```
StreamPromptTui/
├── __init__.py    # IBridge — 行桥接器（包的门户），队列+后台线程消费
├── __main__.py    # 开发测试入口 — 演示 IBridge 继承与 EventBus 对接
├── app.py         # TuiApp — 应用根组件，组装视图、捕获全局按键
├── helper.py      # KeyController（按键→消息映射器）+ LineRenderer（行文本渲染）
├── event_bus.py   # EventBus — 基于 Blinker 的广播事件总线
├── messages.py    # 所有消息类定义 + Role 枚举
└── views.py       # EditorView / ShowView / CompletionView
```

## 架构概览

```
外部业务层                            TUI 内部
─────────                            ────────
IBridge.send(str)  ──队列──▶ 后台线程拼 LineMsg ──▶ event_bus.emit(msg)
IBridge.on_data(str) ◀── LineMsg 解包 ◀── event_bus.on(LineMsg, ...)


                         ┌── event_bus.emit(msg) ──┐
                         │                         │
按键 → TuiApp.on_key → KeyController.dispatch()    │
                                                    ▼
                                        ┌───────────────────┐
                                        │     EventBus      │
                                        │  (Blinker 广播)    │
                                        └───────────────────┘
                                         │    │    │    │
                               SubmitMsg │    │    │    │ LineMsg
                                         ▼    ▼    ▼    ▼
                                    EditorView  CompletionView  ShowView  IBridge
                                   (on_mount 中订阅各自关心的消息)
```

**两层设计：** 内部消息（SubmitMsg、HistoryMsg 等）走 EventBus 广播，组件间解耦；外部接口通过 IBridge 桥接——外部只需和字符串打交道，IBridge 内部完成字符串 ↔ LineMsg 的转换。

Textual 仅负责 `on_key` 原始按键捕获和组件生命周期（`compose`、`on_mount`）。应用层消息全部走 EventBus 广播，不依赖 DOM 位置，任意组件都能收到。

## 组件树

```
TuiApp (App)
├── Header
├── Container#show_container
│   └── ShowView
│       └── RichLog              ← 输出展示区，auto_scroll
├── Container#editor_container
│   └── EditorView
│       └── Input                ← 编辑输入区
├── Container#completion_container
│   └── CompletionView           ← 水平滚动补全栏
└── Footer
```

CSS 布局：`show_container: 1fr`（弹性），`editor_container: 3`（固定），`completion_container: 1`（固定）。

---

## 消息体系

所有消息继承 `textual.message.Message`。通过 `event_bus.emit(msg)` 广播、`event_bus.on(MsgCls, handler)` 订阅。

| 消息 | 字段 | 含义 |
|------|------|------|
| `SubmitMsg` | 无 | 提交当前输入 |
| `HistoryMsg` | `direction: int`（-1 上 / 1 下） | 翻阅历史命令 |
| `CompletionMsg` | 无 | 请求补全 |
| `CompletionSelectMsg` | `direction: int`（1 尾 / -1 头） | 补全栏滚动 |
| `CompletionListMsg` | `completions: list[str]` | 补全候选项列表 |
| `LineMsg` | `role: Role`, `line: str`, `params: dict` | **双向公共通道** |

### Role 枚举

`LineMsg.role` 使用 `Role` 枚举（定义在 `messages.py`），不使用裸字符串：

```python
class Role(Enum):
    INPUT  = auto()   # 用户输入行（EditorView → ShowView / IBridge）
    BRIDGE = auto()   # IBridge 外部推送 → ShowView
```

业务层可通过扩展 `Role` 枚举或使用 `params` 字典添加自定义角色。

### LineMsg 双向通道

```
发送方：
  - EditorView 提交时 emit（role=Role.INPUT） → ShowView 渲染 + IBridge 转发
  - IBridge.send() 入队后 emit（role=Role.BRIDGE）→ ShowView 渲染

接收方：
  - ShowView.on_line_msg   → LineRenderer → RichLog
  - IBridge._on_event_msg  → 过滤 BRIDGE 来源 → on_data(str)
```

IBridge 内部跳过 `role == Role.BRIDGE` 的消息防止回环。

---

## EventBus（event_bus.py）

基于 [Blinker](https://github.com/pallets-eco/blinker) 的 `Namespace`，提供三个函数：

```python
event_bus.emit(msg)                # 广播消息，信号名 = type(msg).__name__
event_bus.on(MsgCls, handler)      # 订阅消息类型
event_bus.off(MsgCls, handler)     # 取消订阅
```

信号名取自消息类名（如 `SubmitMsg` → 信号 `"SubmitMsg"`），一一对应。每个 View 在 `on_mount` 中订阅自己关心的消息。

---

## IBridge（__init__.py）— 对外桥接层

IBridge 是包的门户，封装了字符串 ↔ LineMsg 的转换，外部使用者无需了解消息类或 EventBus。

```python
from StreamPromptTui import App  # App 即 IBridge 的别名

class MyBridge(App):
    def on_data(self, line: str) -> None:
        """收到行文本时的回调。子类必须实现。"""
        print(f"收到: {line}")

bridge = MyBridge()
bridge.run(completion_dict={"help": "show help"}, input_prompt=">> ")
```

### 公开方法

| 方法 | 说明 |
|------|------|
| `send(line: str)` | 入队一个字符串，后台线程取出后拼成 LineMsg(role=BRIDGE) 并 emit。**线程安全**，可在任意线程调用。 |
| `on_data(line: str)` | 收到行文本时的回调入口。**抽象方法，子类必须实现**。UI 内产生的 LineMsg（非 BRIDGE 来源）均会解包后调用此方法。 |
| `run(completion_dict, input_prompt, renderer_kwargs)` | 启动 TUI 应用。阻塞直到退出。`renderer_kwargs` 可选，传递给 LineRenderer 用于扩展（如高亮开关）。 |

### 内部实现

```
send(str)  ──push──▶  Queue[str]  ──pop──▶  后台线程 (daemon)
                                              │
                                              ▼
                                    LineMsg(role=Role.BRIDGE, line=str)
                                              │
                                              ▼
                                       event_bus.emit()
                                              │
                          ┌───────────────────┤
                          ▼                   ▼
                    ShowView 渲染        其他订阅者

外部输入 (editor view)  ──emit──▶  LineMsg(role=Role.INPUT)  ──▶  IBridge._on_event_msg
                                                                   │
                                                                   ▼ 解包 msg.line
                                                             on_data(str)  ← 子类实现
```

- **队列 + 单消费线程**：`send()` 只 push 队列，不直接 emit。实际 emit 在后台 `daemon` 线程中排队执行，保证 `on_data` 里调用 `send` 不会嵌套 emit。
- **来源过滤**：`_on_event_msg` 中跳过 `role == Role.BRIDGE` 的消息，避免自己发的消息回环。
- **构造函数即启动**：`__init__` 中自动订阅 `LineMsg` 并启动后台消费线程，子类无需手动调用。

---

## 各组件详解

### TuiApp（app.py）

- `compose()` 构建组件树，注入 `completion_dict`（通过 `CompletionView.set_completion_dict()`）和 `input_prompt`
- `on_key()` 拦截非打印按键 → `KeyController.dispatch()` → `event_bus.emit(msg)` 广播

### KeyController（helper.py）

纯数据类，不继承 Widget。`_KEY_MAP` 字典做查表映射：

| 按键 | 消息 |
|------|------|
| ↑ / ↓ | `HistoryMsg(direction)` |
| Tab | `CompletionMsg()` |
| PgUp / PgDn | `CompletionSelectMsg(direction)` |
| Enter | `SubmitMsg()` |

### EditorView（views.py）

编辑区，内嵌 `Input`。在 `on_mount` 订阅 `SubmitMsg`、`HistoryMsg`、`CompletionListMsg`。

- **提交**：收到 `SubmitMsg` → 读输入 → 清空 → 写历史 → `event_bus.emit(LineMsg(role=Role.INPUT, ...))`
- **历史翻阅**：收到 `HistoryMsg` → 按 direction 取 `_history[_idx]` → `_set_input_value()`，支持上下边界循环
- **补全替换**：收到 `CompletionListMsg` → 前缀匹配当前输入 → 唯一匹配时替换
- **手动编辑检测**：`on_input_changed` 中退出历史模式

关键状态：`_history`、`_idx`（-1=新鲜输入）、`_suppress_change_event`。

### ShowView（views.py）

内嵌 `RichLog`。在 `on_mount` 订阅 `LineMsg`。
收到 `LineMsg` → `LineRenderer.render()` → `RichLog.write()`。

`LineRenderer` 实例在 `compose()` 时由外部注入，渲染控制权完全在 `LineRenderer`，`RichLog` 的 `highlight`/`markup` 参数不启用。

### CompletionView（views.py）

单行水平滚动补全栏。在 `on_mount` 订阅 `CompletionMsg`、`CompletionSelectMsg`。
- 通过 `set_completion_dict()` 注入补全数据（由 `TuiApp.compose()` 调用）
- 收到 `CompletionMsg` → 取出全部 keys → `event_bus.emit(CompletionListMsg(...))`
- 收到 `CompletionSelectMsg` → 滚动 `_offset`，越界循环
- `render()` 自适应宽度，超出截断显示 `...`

### LineRenderer（helper.py）

将 `LineMsg` 转为 `rich.text.Text`。按 `role` 选择渲染策略：

- `role == Role.INPUT`：拼接 `params['prompt']` + `line`，还原用户输入时的完整提示符行
- 其他 role：原样输出 `line`

预留扩展：代码/日志/shell 高亮、markdown 渲染。扩展参数通过 `__init__(**kwargs)` 传入，存储在 `_options` 中。

---

## 对外接口

**推荐方式：继承 IBridge。** 外部只需实现 `on_data(str)`，调用 `send(str)` 推送输出，完全不用接触消息类。

```python
from StreamPromptTui import App  # App = IBridge

class MyBridge(App):
    def on_data(self, line: str) -> None:
        """收到用户输入行。"""
        # 处理用户输入...
        self.send(f"echo: {line}")  # 推送输出到屏幕

bridge = MyBridge()
bridge.run(
    completion_dict={"hello": "greeting", "quit": "exit"},
    input_prompt=">> ",
)
```

**高级方式：直接使用 EventBus。** 需要直接操作 LineMsg 时可用。

```python
from StreamPromptTui import event_bus
from StreamPromptTui.messages import LineMsg, Role

# 订阅用户输入（跳过 BRIDGE 来源避免回环）
def my_input_handler(msg: LineMsg):
    if msg.role != Role.BRIDGE:
        print(f"用户输入: {msg.line}")

event_bus.on(LineMsg, my_input_handler)

# 推送输出到屏幕
event_bus.emit(LineMsg(role=Role.BRIDGE, line="Hello"))
```