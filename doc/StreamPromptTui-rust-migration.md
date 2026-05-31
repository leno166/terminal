# StreamPromptTui → Rust 迁移方案

> **目标：** 将 StreamPromptTui 从 Python/Textual 重写为 Rust/ratatui，编译成 C ABI 原生共享库（`.dll` / `.so` / `.dylib`），供任意语言链接使用。

---

## 动机

### 当前状态

StreamPromptTui 目前是纯 Python 模块，基于 Textual 框架。UI 和业务逻辑都在 Python 进程内运行。

### 目标工作流

```
阶段 1: Python 快速验证后端
  ┌──────────────┐  ctypes   ┌──────────────────┐
  │ Python 后端   │──────────▶│ libstreamtui.so  │
  │ (原型, 1-2天) │◀──────────│ (预编译 TUI 库)   │
  └──────────────┘  callback └──────────────────┘
  
  后端逻辑在 Python 中快速迭代，TUI 是同一个 native 库。

阶段 2: 后端重写为 C / Rust
  ┌──────────────┐  链接     ┌──────────────────┐
  │ C / Rust 后端 │──────────▶│ libstreamtui.so  │
  │ (生产代码)     │◀──────────│ (同一个 .so)     │
  └──────────────┘  callback └──────────────────┘
  
  后端逻辑用 C/Rust 重写，TUI 侧零改动。

阶段 3: 发布
  ┌──────────────────────────┐
  │ MyApp (单文件二进制)       │
  │ ├── 后端 (C/Rust)         │
  │ └── TUI (静态链接)         │  ← 零运行时依赖
  └──────────────────────────┘
```

**核心理念：** TUI 库是独立的、语言无关的 native 组件。Python 是验证后端逻辑的"快速画布"，不是最终运行时。

---

## 技术选型

### Rust + ratatui + crossterm

| 选择 | 理由 |
|------|------|
| **Rust** | 零成本抽象、无 GC、原生 `extern "C"` 导出、`cargo` 跨平台构建 |
| **ratatui** | Rust 生态最成熟的 TUI 库，widget 体系完备，社区活跃 |
| **crossterm** | 纯 Rust 跨平台终端后端，Windows/Linux/macOS 原生支持 |
| **tui-textarea** | ratatui 生态的文本输入组件，填补 ratatui 无内置 Input 的缺口 |

### 与替代方案的对比

| | 纯 C | Rust + ratatui |
|--|------|---------------|
| TUI 框架 | 无，全部手写 | ratatui 提供 Layout/Widget/样式 |
| 跨平台终端 | 手写两套后端 | crossterm 统一抽象 |
| Input 输入框 | 手写 | tui-textarea crate |
| C ABI 导出 | 原生 | `extern "C"` + `cbindgen` |
| 预估代码量 | 4000-6000 行 | ~2000 行 |
| 预估工期 | 4-6 周 | ~2 周 |

---

## C ABI 公开接口

TUI 库暴露纯 C 接口，保持与 Python IBridge 相同的哲学。

```c
// streamtui.h — 对外唯一头文件

/* 不透明句柄 */
typedef struct StreamPromptTui StreamPromptTui;

/* 用户输入回调。line 仅在回调期间有效，需自行拷贝。 */
typedef void (*tui_on_data_fn)(const char* line, void* user_data);

/* 创建 TUI 实例。user_data 透传给所有回调。 */
StreamPromptTui* tui_create(tui_on_data_fn on_data, void* user_data);

/* 启动 TUI 主循环。阻塞直到用户退出。
 * completions 为 NULL-terminated 字符串数组。
 * 返回 0 正常退出，非 0 异常。 */
int tui_run(StreamPromptTui*     tui,
            const char* const*   completions,
            const char*          input_prompt);

/* 从任意线程推送文本到输出区。线程安全。 */
void tui_send(StreamPromptTui* tui, const char* line);

/* 请求退出 TUI 主循环。线程安全，可在回调/信号处理器中调用。 */
void tui_quit(StreamPromptTui* tui);

/* 销毁实例，释放所有资源。 */
void tui_destroy(StreamPromptTui* tui);
```

### 与 Python IBridge 的对应

| Python IBridge | C ABI | 说明 |
|---------------|-------|------|
| `class MyBridge(App):` | `tui_create(on_data, ctx)` | 继承 → 注册回调 |
| `on_data(self, line)` | `tui_on_data_fn(line, user_data)` | 用户输入回调 |
| `self.send(line)` | `tui_send(tui, line)` | 推送输出 |
| `self.app.exit()` | `tui_quit(tui)` | 退出 |
| `bridge.run(dict, prompt)` | `tui_run(tui, keys, prompt)` | 启动主循环 |

---

## Rust 侧模块架构

```
streamtui/
├── Cargo.toml
├── cbindgen.toml              # 自动生成 streamtui.h 的配置
├── src/
│   ├── lib.rs                 # C ABI 入口 + extern "C" 导出
│   ├── app.rs                 # 主组件树 + 主循环
│   ├── event_bus.rs           # 轻量发布-订阅
│   ├── messages.rs            # 消息枚举 + Role 枚举
│   ├── input.rs               # KeyController（按键→消息映射）
│   ├── render.rs              # LineRenderer（按 Role 渲染文本）
│   └── views/
│       ├── mod.rs
│       ├── show.rs            # ShowView — 输出展示区
│       ├── editor.rs          # EditorView — 编辑输入区
│       └── completion.rs      # CompletionView — 水平补全栏
└── tests/
    └── integration.rs         # C ABI 集成测试
```

### 模块职责映射

| Python 文件 | Rust 模块 | 职责 |
|------------|----------|------|
| `__init__.py` (IBridge) | `lib.rs` | C ABI 导出、队列+后台线程、生命周期管理 |
| `app.py` (TuiApp) | `app.rs` | 组件树 compose、全局按键拦截、主循环 |
| `event_bus.py` | `event_bus.rs` | 消息类型 → 订阅回调列表，`emit()`/`on()`/`off()` |
| `messages.py` | `messages.rs` | `LineMsg` 结构体、`Role` 枚举、其他消息类型 |
| `helper.py` (KeyController) | `input.rs` | 按键→消息查表映射 |
| `helper.py` (LineRenderer) | `render.rs` | `LineMsg` → `ratatui::text::Text` 渲染 |
| `views.py` (3 个 View) | `views/` (3 个文件) | 三个独立 widget |

---

## 组件实现方案

### 1. EventBus（event_bus.rs）

不使用 tokio。手写轻量订阅列表，约 60 行。

```rust
use std::any::{Any, TypeId};
use std::collections::HashMap;

type Callback = Box<dyn Fn(&dyn Any) + Send + Sync>;

pub struct EventBus {
    subscribers: HashMap<TypeId, Vec<Callback>>,
}

impl EventBus {
    pub fn emit<M: 'static>(&self, msg: M) {
        let tid = TypeId::of::<M>();
        if let Some(callbacks) = self.subscribers.get(&tid) {
            for cb in callbacks {
                cb(&msg);
            }
        }
    }

    pub fn on<M: 'static>(&mut self, callback: impl Fn(&M) + Send + Sync + 'static) {
        let tid = TypeId::of::<M>();
        let wrapper: Callback = Box::new(move |any: &dyn Any| {
            if let Some(m) = any.downcast_ref::<M>() {
                callback(m);
            }
        });
        self.subscribers.entry(tid).or_default().push(wrapper);
    }
}
```

**设计要点：**
- 用 `TypeId` 做消息类型路由，等价于 Python Blinker 的 `type(msg).__name__` 作为信号名
- `Send + Sync` 保证线程安全（`tui_send` 从外部线程调 `emit`）
- 不需要 tokio runtime

### 2. 消息体系（messages.rs）

```rust
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Role {
    Input,   // 用户输入
    Bridge,  // 外部推送
}

pub struct LineMsg {
    pub role: Role,
    pub line: String,
    pub params: HashMap<String, String>,
}

// 内部消息
pub struct SubmitMsg;
pub struct HistoryMsg { pub direction: i32 }
pub struct CompletionMsg;
pub struct CompletionSelectMsg { pub direction: i32 }
pub struct CompletionListMsg { pub completions: Vec<String> }
```

**设计要点：**
- `Role` 枚举直接对应 Python `Role(Enum)`
- `params` 用 `HashMap<String, String>` 替代 Python `dict`
- 消息 struct 不带行为，纯数据

### 3. KeyController（input.rs）

crossterm 的 `KeyCode` 枚举比 Python Textual 的 `key` 属性更精细。

```rust
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

pub fn dispatch(event: KeyEvent) -> Option<InternalMsg> {
    match event.code {
        KeyCode::Up    => Some(HistoryMsg { direction: -1 }.into()),
        KeyCode::Down  => Some(HistoryMsg { direction:  1 }.into()),
        KeyCode::Tab   => Some(CompletionMsg.into()),
        KeyCode::PageUp   => Some(CompletionSelectMsg { direction:  1 }.into()),
        KeyCode::PageDown => Some(CompletionSelectMsg { direction: -1 }.into()),
        KeyCode::Enter => Some(SubmitMsg.into()),
        _ => None,
    }
}
```

### 4. ShowView（views/show.rs）

等价于 Python 的 `ShowView` + `RichLog`。ratatui 没有 `RichLog`，用 `Paragraph` + 手动滚动状态封装。

```rust
pub struct ShowView {
    lines: Vec<String>,        // 所有输出行
    scroll_offset: usize,      // 滚动偏移
    auto_scroll: bool,         // 是否自动滚到底部
}

impl ShowView {
    pub fn append(&mut self, line: String) {
        self.lines.push(line);
    }

    pub fn render(&self, area: Rect, frame: &mut Frame) {
        let visible = self.visible_lines(area.height as usize);
        let text = Text::from(visible.join("\n"));
        frame.render_widget(
            Paragraph::new(text).block(Block::default()),
            area,
        );
    }

    fn visible_lines(&self, height: usize) -> &[String] {
        if self.auto_scroll {
            let start = self.lines.len().saturating_sub(height);
            &self.lines[start..]
        } else {
            let start = self.scroll_offset;
            &self.lines[start..self.lines.len().min(start + height)]
        }
    }
}
```

### 5. EditorView（views/editor.rs）

**ratatui 没有内置 Input 组件。** 引入 `tui-textarea` crate 填补。

```rust
use tui_textarea::TextArea;

pub struct EditorView {
    textarea: TextArea<'static>,
    history: Vec<String>,
    history_idx: isize,           // -1 = 新鲜输入
    in_history_mode: bool,
}

impl EditorView {
    pub fn new(prompt: &str) -> Self {
        let mut ta = TextArea::default();
        ta.set_placeholder_text(prompt);
        ta.set_block(Block::default());
        Self {
            textarea: ta,
            history: Vec::new(),
            history_idx: -1,
            in_history_mode: false,
        }
    }

    pub fn submit(&mut self) -> String {
        let text = self.textarea.lines().join("");
        self.history.push(text.clone());
        self.textarea = TextArea::default();
        self.history_idx = -1;
        text
    }

    pub fn navigate_history(&mut self, direction: i32) {
        // 与 Python 版逻辑一致：边界循环
        let len = self.history.len() as isize;
        if len == 0 { return; }
        self.history_idx = (self.history_idx + direction as isize).rem_euclid(len);
        let entry = &self.history[self.history_idx as usize];
        self.textarea = TextArea::from(entry.as_str());
    }
}
```

### 6. CompletionView（views/completion.rs）

水平滚动候选栏。用 `Paragraph` 渲染单行，手动管理偏移。

```rust
pub struct CompletionView {
    completions: Vec<String>,
    offset: usize,
}

impl CompletionView {
    pub fn render(&self, area: Rect, frame: &mut Frame) {
        let visible = self.fit_to_width(area.width as usize);
        let text = Text::from(visible.join("  "));
        frame.render_widget(
            Paragraph::new(text)
                .style(Style::default().fg(Color::DarkGray))
                .block(Block::default()),
            area,
        );
    }

    fn fit_to_width(&self, width: usize) -> Vec<&str> {
        let mut remaining = width;
        self.completions.iter()
            .skip(self.offset)
            .take_while(|c| {
                if remaining >= c.len() + 2 {
                    remaining -= c.len() + 2;
                    true
                } else {
                    false
                }
            })
            .map(|s| s.as_str())
            .collect()
    }
}
```

### 7. LineRenderer（render.rs）

```rust
pub trait LineRenderer {
    fn render(&self, msg: &LineMsg) -> Text;
}

pub struct DefaultRenderer {
    pub input_prompt: String,
}

impl LineRenderer for DefaultRenderer {
    fn render(&self, msg: &LineMsg) -> Text {
        match msg.role {
            Role::Input => {
                let styled = format!("{}{}", self.input_prompt, msg.line);
                Text::styled(styled, Style::default().fg(Color::Green))
            }
            Role::Bridge => {
                Text::raw(&msg.line)
            }
        }
    }
}
```

### 8. App 主循环（app.rs）

```rust
use ratatui::layout::{Constraint, Direction, Layout};

pub struct TuiApp {
    pub show_view: ShowView,
    pub editor_view: EditorView,
    pub completion_view: CompletionView,
    pub event_bus: EventBus,
    pub renderer: Box<dyn LineRenderer>,
    pub running: bool,
}

impl TuiApp {
    pub fn main_loop(&mut self, completions: &[String], prompt: &str) -> io::Result<()> {
        let mut terminal = Terminal::new(CrosstermBackend::new(stdout()))?;
        terminal.clear()?;

        self.completion_view.set_completions(completions.to_vec());
        self.editor_view.set_prompt(prompt);

        while self.running {
            terminal.draw(|frame| self.render(frame))?;

            if event::poll(Duration::from_millis(50))? {
                if let Event::Key(key) = event::read()? {
                    self.handle_key(key);
                }
            }
        }

        terminal.clear()?;
        Ok(())
    }

    fn render(&self, frame: &mut Frame) {
        let area = frame.area();
        let layout = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(1),   // Header
                Constraint::Fill(1),     // ShowView
                Constraint::Length(3),   // EditorView
                Constraint::Length(1),   // CompletionView
                Constraint::Length(1),   // Footer
            ])
            .split(area);

        self.render_header(frame, layout[0]);
        self.show_view.render(layout[1], frame);
        self.editor_view.textarea.render(layout[2], frame);
        self.completion_view.render(layout[3], frame);
        self.render_footer(frame, layout[4]);
    }
}
```

### 9. C ABI 层（lib.rs）

```rust
use std::ffi::{CStr, CString};
use std::os::raw::c_char;
use std::sync::mpsc;

pub struct StreamPromptTui {
    app: TuiApp,
    tx: mpsc::Sender<String>,      // send() → 后台线程 → app
    on_data: extern "C" fn(*const c_char, *mut c_void),
    user_data: *mut c_void,
}

#[no_mangle]
pub extern "C" fn tui_create(
    on_data: extern "C" fn(*const c_char, *mut c_void),
    user_data: *mut c_void,
) -> *mut StreamPromptTui {
    let (tx, rx) = mpsc::channel::<String>();
    let tui = Box::new(StreamPromptTui {
        app: TuiApp::new(),
        tx,
        on_data,
        user_data,
    });
    let ptr = Box::into_raw(tui);

    // 启动后台消费线程（等价于 Python IBridge 的 daemon 线程）
    let tx_clone = ptr as usize; // 用指针做弱引用标识
    std::thread::spawn(move || {
        for line in rx {
            // 入队文本 → LineMsg → EventBus → ShowView 渲染
            // (通过某种方式通知主线程)
        }
    });

    ptr
}

#[no_mangle]
pub extern "C" fn tui_run(
    ptr: *mut StreamPromptTui,
    completions: *const *const c_char,
    prompt: *const c_char,
) -> i32 {
    let tui = unsafe { &mut *ptr };

    // 解析 completions 数组
    let keys = unsafe { cstr_array_to_vec(completions) };
    let prompt_str = unsafe { CStr::from_ptr(prompt).to_string_lossy() };

    // 进入主循环
    match tui.app.main_loop(&keys, &prompt_str) {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("TUI error: {e}");
            1
        }
    }
}

#[no_mangle]
pub extern "C" fn tui_send(ptr: *mut StreamPromptTui, line: *const c_char) {
    let tui = unsafe { &*ptr };
    let text = unsafe { CStr::from_ptr(line).to_string_lossy() };
    let _ = tui.tx.send(text.into_owned());
}

#[no_mangle]
pub extern "C" fn tui_quit(ptr: *mut StreamPromptTui) {
    let tui = unsafe { &mut *ptr };
    tui.app.running = false;
}

#[no_mangle]
pub extern "C" fn tui_destroy(ptr: *mut StreamPromptTui) {
    if ptr.is_null() { return; }
    unsafe {
        let _ = Box::from_raw(ptr); // Drop
    }
}
```

---

## 线程模型

```
外部调用线程                      TUI 主线程
─────────────                    ───────────
tui_send()                        tui_run() 主循环
  │                                 │
  ├─ mpsc::tx.send(line)            ├─ terminal.draw()
  │  (立即返回)                      ├─ crossterm::event::poll()
  │                                 ├─ 按键 → dispatch() → emit()
  └─ 线程安全入队                    │     │
                                    │     ├─ SubmitMsg → EditorView.submit()
                                    │     │    → LineMsg(INPUT) → on_data 回调
                                    │     │       (回调在 TUI 线程内执行)
                                    │     │
                                    │     └─ 其他消息 → 对应 View 处理
                                    │
后台消费线程                         │
─────────────                       │
mpsc::rx.recv()                     │
  └─ 取到文本                       │
     → LineMsg(BRIDGE)              │
     → emit → ShowView.append() ────┘
```

**线程安全保证：**
- `tui_send()` 只做 `mpsc::tx.send()`，无锁竞争，可在任意线程调用
- 后台消费线程将字符串封成 `LineMsg` 后，通过 `EventBus` 投递到 TUI 主线程
- `on_data` 回调在 TUI 主线程内同步执行，调用方需保证回调不长时间阻塞

---

## 构建与分发

### 开发阶段

```bash
# Rust 侧
cargo build --release                    # → target/release/libstreamtui.so

# 自动生成头文件
cbindgen --config cbindgen.toml          # → streamtui.h
```

### Python 原型阶段（ctypes 加载同一个库）

```python
import ctypes

lib = ctypes.CDLL("./target/release/libstreamtui.so")

TUI_ON_DATA = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_void_p)

@TUI_ON_DATA
def on_data(line, ctx):
    print(f"收到: {line.decode()}")
    lib.tui_send(ctx, f"echo: {line.decode()}".encode())

tui = lib.tui_create(on_data, None)
lib.tui_run(tui, [b"help", b"quit", None], b">> ")
lib.tui_destroy(tui)
```

### 发布阶段（静态链接）

```toml
# 后端项目的 Cargo.toml
[dependencies]
streamtui = { path = "../streamtui" }  # 或 git 依赖
```

```rust
// 后端直接依赖 Rust crate，无需经过 C ABI
fn main() {
    let mut app = streamtui::TuiApp::new();
    app.set_on_data(|line| {
        // 后端业务逻辑
        app.send(format!("echo: {line}"));
    });
    app.run(&["help", "quit"], ">> ");
}
```

编译产物是单个静态链接的二进制文件，零运行时依赖。

---

## 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| ratatui 无内置 Input | 需自行实现输入框 | 引入 `tui-textarea` crate，社区已验证 |
| C ABI 字符串转换开销 | `CStr` ↔ `String` 每次调用都分配 | 可接受。TUI 场景数据量小，非热路径 |
| 回调在 TUI 主线程内执行 | `on_data` 阻塞会卡 UI | 文档明确约定：回调应快速返回，耗时操作另起线程 |
| 跨平台终端行为差异 | Windows Terminal vs Linux 终端模拟器 | crossterm 已处理绝大多数差异 |
| cbindgen 生成头文件质量 | 复杂类型可能生成不理想 | 公开 API 只用 C 基本类型（`*const c_char`、`i32`、`void*`），不存在问题 |

---

## 开发步骤

| 步骤 | 产出 | 预估时间 |
|------|------|---------|
| 1. 搭建 Cargo 项目骨架 + 依赖 | `Cargo.toml`、空 `lib.rs` 能编译 | 0.5 天 |
| 2. EventBus + Messages | `event_bus.rs`、`messages.rs` + 单元测试 | 0.5 天 |
| 3. C ABI 层 | `lib.rs` — `extern "C"` 全部 5 个函数 + `cbindgen` 配置 | 1.5 天 |
| 4. App 主循环 + Layout | `app.rs` — 组件树、渲染循环、按键分发 | 1 天 |
| 5. ShowView | `views/show.rs` — 输出区 + 自动滚动 | 1 天 |
| 6. EditorView | `views/editor.rs` — 集成 `tui-textarea` + 历史 | 1 天 |
| 7. CompletionView | `views/completion.rs` — 水平补全栏 | 1 天 |
| 8. LineRenderer + KeyController | `render.rs`、`input.rs` | 0.5 天 |
| 9. C ABI 集成测试 | `tests/integration.rs` — C 调用方视角验证 | 0.5 天 |
| 10. Python ctypes 验证 | Python 脚本调 DLL，跑通回显 demo | 0.5 天 |
| **总计** | | **≈ 8-9 天** |