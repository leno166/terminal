# Diag / Uds 模块设计文档

> **版本**: 0.3 | **更新**: 2026-06-05

---

## 1. 文件结构

```
src/workspace/module/Diag/               src/workspace/module/Uds/
├── __init__.py    # Session, Service,    ├── __init__.py    # UdsApp, session
│                     UdsResponse,        ├── app.py         # UdsApp 基类
│                     KeepAliveConfig     ├── decorators.py  # @session 装饰器
├── uds.py         # Session + KeepAlive  └── __main__.py    # 使用演示
├── service.py     # Service + KeepAliveConfig
├── response.py    # UdsResponse + father 链
└── helper.py      # to_bytes

autodoip (PyPI)                          oem/
├── Endpoint                             ├── platform.py
├── Config                               └── keys.py
└── ProtocolError
```

| 模块 | 定位 | 用户面向 |
|------|------|---------|
| `Uds` | **声明式 API**（新版） | `class MyApp(UdsApp)` + `@session` 装饰器 |
| `Diag` | **命令式 API**（底层） | `Service` 直接调用 `send()` / `change_session()` |
| `autodoip` | DoIP 传输层 | 不直接暴露给用户 |

---

## 2. 分层架构

```
┌──────────────────────────────────────────────┐
│  UdsApp (app.py)                             │  ← 声明式：@session 装饰器
│  @session (decorators.py)                    │     自动管理会话/安全等级/编解码
├──────────────────────────────────────────────┤
│  Service (service.py) : Session              │  ← 命令式：ISO 14229 标准方法
│  Session (uds.py)                            │  ← 持有 autodoip.Endpoint + KeepAlive
├──────────────────────────────────────────────┤
│  autodoip.Endpoint                           │  ← DoIP 传输（ISO 13400）
│  autodoip.Config                             │  ← 传输调优参数
├──────────────────────────────────────────────┤
│  UdsResponse (response.py)                   │  ← 正/负响应 + father 链 + NRC 描述
│  helper.py                                   │  ← to_bytes 类型转换
└──────────────────────────────────────────────┘
```

---

## 3. UdsApp — 声明式 API（新版，推荐）

### 3.1 设计哲学

类似 **pytest fixture** 的 `yield` 模式：`yield` 之前准备请求，`yield` 之后处理响应。

```
yield 之前 ──── yield ──── yield 之后
(准备请求载荷)    (发送)     (处理 UdsResponse)
```

两种写法：

| 模式 | 写法 | 适用场景 |
|------|------|---------|
| **简单模式** | `return self` — self 是解码后的响应值 | 纯数据透传，只需类型转换 |
| **生成器模式** | `resp = yield payload` — 拿到完整 UdsResponse | 需检查 NRC、提取 head/body、错误处理 |

### 3.2 30 秒体验

```python
from Uds import UdsApp, read, write, session
from Diag import UdsResponse

class MyApp(UdsApp):

    # ── 简单模式：参数自动编码 → 发送 → 响应自动解码 → self ──
    @session(name='default')
    def default(self, payload: bytes) -> bytes:
        return self                         # self = 响应的 body bytes

    # ── 生成器模式：yield 划分输入/输出，拿到完整 UdsResponse ──
    @write(did=0x1111)
    def write(self, payload: bytes) -> bytes:
        resp: UdsResponse = yield payload   # ← 发送 payload，得到响应
        if resp.is_negative:
            raise RuntimeError(f"写入失败: {resp.nrc_desc}")
        return resp.body

    @read(did=0xF190)
    def read_vin(self) -> str:
        resp: UdsResponse = yield b''       # ← 空载荷（DID 已在装饰器指定）
        if resp.is_negative:
            return ""
        return resp.body.decode('ascii').strip('\x00')


app = MyApp(ip='198.18.44.1', ecus={'mcu': (0x1301, '198.18.44.49')})
with app:
    # 简单模式
    resp = app.default(payload=b'\x22\xDC\x06')

    # 生成器模式 — 调用方只传业务参数，不写 hex
    result = app.write(payload=b'\x01\x02')
    vin = app.read_vin()                     # → "LSVAU2A38M2100123"
```

### 3.3 UdsApp 基类

```python
class UdsApp:
    """声明式 UDS 应用基类。内部持有 Service 实例。"""

    def __init__(self, ip, ecus, port=13400, tester=0x0E80,
                 transmit=None, keepalive=None):
        self._service = Service(ip=ip, ecus=ecus, port=port, tester=tester,
                                transmit=transmit, keepalive=keepalive)
        self._key_calculator = None

    def set_key_calculator(self, fn):
        self._service.set_key_calculator(fn)

    def start(self):   self._service.start()
    def stop(self):    self._service.stop()
    def __enter__(self): self.start(); return self
    def __exit__(self, *a): self.stop()
```

### 3.4 装饰器

#### 通用参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `session` | `int` | 诊断会话 ID（`0x01` 默认、`0x02` 编程、`0x03` 扩展） |
| `level` | `int` | 需要的安全等级（L 奇数 0x01~0xFD） |
| `did` | `int` | DID（仅 `@read` / `@write`） |
| `rid` | `int` | 例程 ID（仅 `@routine`） |
| `sub` | `int` | 例程子功能 `0x01`=启动、`0x02`=停止、`0x03`=取结果（仅 `@routine`，默认 `0x01`） |

> **兼容简写**：`@session(sid=0x03)` → `session=0x03`。`@read(sid=0xF190)` → `did=0xF190`。

#### 装饰器一览

| 装饰器 | 自动构建的 UDS 请求 | 用途 |
|--------|-------------------|------|
| `@session` | 无（由方法参数决定） | 通用：透传原始 UDS |
| `@read(did=DID)` | `22 {DID}` | 读取 DID |
| `@write(did=DID)` | `2E {DID} {payload}` | 写入 DID |
| `@routine(rid=RID, sub=0x01)` | `31 {sub} {RID}` | 例程控制 |

### 3.5 两种方法模式

#### 简单模式（无 yield）

```python
@read(sid=0xF190)
def read_vin(self) -> str:
    return self    # self = 自动解码后的响应 body（str）
```

框架自动完成：编码参数 → 发送 → 收响应 → 按返回类型解码 body → 作为 `self` 传入方法体 → 返回值直接返回给调用方。

#### 生成器模式（有 yield）— 类似 pytest fixture

```
方法调用 → 创建生成器 → next(gen) 跑到 yield → 拿到请求载荷
→ 框架发送 → 收响应 → gen.send(response) 把 UdsResponse 喂回去
→ 方法体处理响应 → return 最终结果
```

```python
@write(sid=0x1111)
def write(self, payload: bytes) -> bytes:
    # ═══ yield 之前：准备请求 ═══
    # payload 由调用方传入，框架已编码为 bytes

    resp: UdsResponse = yield payload   # ← 发送 payload，阻塞等待响应

    # ═══ yield 之后：处理响应 ═══
    # resp 是完整的 UdsResponse 对象，可检查一切
    if resp.is_negative:
        raise RuntimeError(f"NRC 0x{resp.nrc:02X}: {resp.nrc_desc}")
    if not resp.ok:
        raise RuntimeError("响应异常")
    return resp.body                    # 返回给调用方
```

**调用**：`app.write(payload=b'\x01\x02')` 或 `app.write(0x1111)` → 内部 `yield` 发送，返回处理后的 `resp.body`。

### 3.6 实现骨架

```python
import functools, inspect

def _is_generator(func):
    return inspect.isgeneratorfunction(func)

def read(sid=None, name=None, level=None):
    """@read(sid=DID) — 自动构建 UDS 0x22 请求"""
    return _session_decorator(uds_sid=0x22, did=sid, name=name, level=level)

def write(sid=None, name=None, level=None):
    """@write(sid=DID) — 自动构建 UDS 0x2E 请求"""
    return _session_decorator(uds_sid=0x2E, did=sid, name=name, level=level)

def _session_decorator(uds_sid=None, did=None, name=None, sid=None, level=None):
    def decorator(func):
        func._uds_sid = sid
        func._uds_level = level
        func._uds_did = did
        func._uds_request_sid = uds_sid

        @functools.wraps(func)
        def wrapper(app_self, *args, **kwargs):
            service = app_self._service

            # ① 环境准备
            if sid is not None and service.session != sid:
                service.change_session(sid)
            if level is not None and service.level < level:
                service.change_level(level)

            # ② 编码请求
            sig = inspect.signature(func)
            bound = sig.bind(app_self, *args, **kwargs)
            bound.apply_defaults()

            # 自动拼装 UDS 帧头（如 22 {did}）
            payload_parts = []
            if uds_sid:
                payload_parts.append(bytes([uds_sid]))
            if did:
                payload_parts.append(did.to_bytes(2, 'big'))

            # 追加方法参数
            for pname, value in bound.arguments.items():
                if pname == 'self':
                    continue
                payload_parts.append(_encode(value, sig.parameters[pname].annotation))

            payload = b''.join(payload_parts)

            # ③ 判断模式
            if _is_generator(func):
                # 生成器模式：yield 划分输入/输出
                gen = func(app_self, **{k: v for k, v in bound.arguments.items() if k != 'self'})
                request = gen.send(None)           # 跑到 yield，拿到实际请求
                final_payload = request if request else payload
                hex_str = final_payload.hex(' ') if isinstance(final_payload, bytes) else _encode(final_payload, bytes).hex(' ')
                resp = service.send(hex_str)
                try:
                    result = gen.send(resp)        # 把 UdsResponse 喂回方法体
                except StopIteration as e:
                    result = e.value               # return 的值
                return result
            else:
                # 简单模式
                hex_str = payload.hex(' ')
                resp = service.send(hex_str)
                if resp.is_negative:
                    return None
                decoded = _decode(resp.body, sig.return_annotation)
                return func(app_self, decoded)

        return wrapper
    return decorator

# session 是通用装饰器，不自动拼 UDS 帧头
def session(name=None, sid=None, level=None):
    return _session_decorator(name=name, sid=sid, level=level)
```

### 3.7 参数编码规则

| 参数类型 | 编码规则 | 示例 |
|---------|---------|------|
| `int` | 最小 big-endian bytes | `0xDC06` → `b'\xDC\x06'` |
| `float` | ×1000 → int → bytes | `3.14` → `b'\x0C\x44'` |
| `bytes` | 直接使用 | `b'\x01\x02'` |
| `str` | ASCII 编码 | `'hello'` → `b'hello'` |
| `bool` | `True` → `b'\x01'` | |

### 3.8 返回值解码规则

| 返回类型 | 解码规则 |
|---------|---------|
| `bytes` | 原样返回 |
| `int` | big-endian → int |
| `float` | int ÷ 1000 |
| `str` | ASCII 解码，strip null |
| `UdsResponse` | 不解码，返回原始响应对象 |

### 3.9 完整示例

```python
from Uds import UdsApp, read, write, routine, session
from Diag import UdsResponse
from oem import make_key_calculator

class MyApp(UdsApp):

    # ── 简单模式：透传 ──
    @session(name='default')
    def default(self, payload: bytes) -> bytes:
        return self               # self = 解码后的响应 body

    # ── 生成器模式：读 VIN ──
    @read(did=0xF190, session=0x03, level=0x05)
    def read_vin(self) -> str:
        resp: UdsResponse = yield b''
        if resp.is_negative:
            return ""
        return resp.body.decode('ascii').strip('\x00')

    # ── 生成器模式：写 DID ──
    @write(did=0xF190, session=0x03, level=0x05)
    def write_vin(self, vin: str) -> bool:
        resp: UdsResponse = yield vin.encode('ascii')
        return resp.ok

    # ── 生成器模式：例程控制 ──
    @routine(rid=0xFF01, session=0x02, level=0x05)
    def erase_memory(self) -> UdsResponse:
        resp: UdsResponse = yield b'\x01'   # sub-function 0x01 = start
        return resp                         # 调用方自行判断 resp.ok


with MyApp(ip='198.18.44.1', ecus={'mcu': (0x1301, '198.18.44.49')}) as app:
    app.set_key_calculator(make_key_calculator('P_G30TU'))

    # 简单模式
    raw = app.default(payload=b'\x22\xDC\x06')

    # 生成器模式 — 调用方只传业务参数
    vin = app.read_vin()
    ok = app.write_vin(vin='LSVAU2A38M2100999')
    resp = app.erase_memory()
```

---

## 4. Session / Service — 命令式 API（底层）

`UdsApp` 内部持有 `Service`，`Service` 继承 `Session`。以下为底层 API 参考。

### 4.1 配置对象

```python
@dataclass
class KeepAliveConfig:
    """TesterPresent 保活配置"""
    interval: float = 1.5
    payload: bytes = b'\x3E\x00'
```

`port` / `tester` 直接作为 `Session`/`Service` 构造参数。传输调优参数使用 `autodoip.Config`。

### 4.2 DoIP 传输层 → autodoip

DoIP 传输完全委托给 `autodoip` 包（PyPI），Diag 不再包含自己的 DoIP 实现。

| 概念 | autodoip 对应 |
|------|-------------|
| Socket 连接管理 | `autodoip.Endpoint` |
| DoIP 帧编解码 | `autodoip._Protocol`（内部） |
| 传输配置 | `autodoip.Config` |
| 协议错误 | `autodoip.ProtocolError`（在 Diag `__init__` 重导出） |

---

### 4.3 Session (uds.py)

```python
class Session:
    def __init__(self, ip: str, ecus: dict[str, tuple],
                 port: int = 13400, tester: int = 0x0E80,
                 transmit: autodoip.Config | None = None,
                 keepalive: KeepAliveConfig | None = None)

    # ecus 格式: {name: (logical_addr, ip)} 或 {name: (logical_addr, ip, port)}
    # port=0 表示使用 session 默认端口

    # 上下文管理器
    def __enter__() -> Self
    def __exit__(...)

    # 运算符: session >> "22DC06"
    def __rshift__(self, uds: Any) -> UdsResponse

    # 属性
    @property ecus -> MappingProxyType              # {name: (logical_addr, ip)}

    # 方法
    def start() -> bool
    def stop() -> bool
    def on(name: str) -> Self                       # 切换 ECU
    def send(data: str) -> UdsResponse              # hex → bytes → conversation → UdsResponse
```

`send()` 在单次 `conversation()` 内持续等待，遇到 NRC 0x78 自动通过 `father` 链记录，直到收到非 0x78 响应。只等待，不重发。

### 4.4 Service (service.py)

```python
class Service(Session):
    def __init__(self, ip: str, ecus: dict[str, tuple],
                 port: int = 13400, tester: int = 0x0E80,
                 transmit: autodoip.Config | None = None,
                 keepalive: KeepAliveConfig | None = None)

    # 注入点
    def set_key_calculator(self, fn: Callable[[int, bytes], bytes]) -> None

    # UDS 标准方法（均调用 self.send()，0x78 已在 send 内处理）
    def change_session(self, ss_id: int) -> Tuple[bool, UdsResponse]      # 0x10
    def change_level(self, level: int) -> Tuple[bool, UdsResponse]        # 0x27
    def change_any(self, ss_id=None, level=None) -> bool
    def reset(self, reset_type=0x01) -> bool                               # 0x11
    def read_did(self, did, ss_id=None, level=None) -> UdsResponse         # 0x22
    def write_did(self, did, data, ss_id=None, level=None) -> UdsResponse  # 0x2E
    def start_routine(self, routine_id, data=None, ...) -> UdsResponse     # 0x31
    def stop_routine(self, routine_id) -> bool
    def get_routine_result(self, routine_id=None) -> UdsResponse
```

> **已移除**：`send_until()`、`RetryConfig` — `send()` 内置了 0x78 流控等待。

### 4.5 使用示例（命令式）

```python
from Diag import Service

ss = Service(ip='198.18.44.1', ecus={'mcu': (0x1301, '198.18.44.49')})

with ss:
    ss.change_session(0x03)
    print(ss >> '22DC06')
```

---

## 5. 响应与工具

### 5.1 UdsResponse (response.py)

```python
@dataclass
class UdsResponse:
    raw: bytes                       # 原始字节
    ok: bool                         # True = 正响应
    is_negative: bool                # True = 负响应
    sid: int | None                  # 正响应 SID = 请求 SID + 0x40
    head: bytes | None               # 正响应 head
    body: bytes | None               # 正响应 body
    request_sid: int | None          # 负响应专用
    nrc: int | None                  # NRC
    nrc_desc: str | None             # 中文 NRC 描述
    father: Optional['UdsResponse']  # 响应链：指向前一个 0x78（如有）

    def check_fail(sid=None, head=None, body=None) -> bool
    def iter_chain(self):            # 从最新响应回溯到最初请求
    @classmethod def from_bytes(data: bytes) -> Self
```

内置 `_NRC_DESC`（~30 条 NRC 中文描述）和 `_POSITIVE_HEAD_LEN`（~20 条 SID head 长度）。

`father` 链：一次 `send()` 可能经历多个 NRC 0x78，它们通过 `father` 串链。调用方可用 `list(resp.iter_chain())` 回溯完整过程。

### 5.2 helper.py

| 函数 | 用途 |
|------|------|
| `to_bytes(value, byte_order='big')` | 统一类型 → bytes |

`recv_exact` / `recv_frame` 已移除（由 autodoip 提供）。

---

## 6. 注入点 (Diag `__init__.py`)

```python
from autodoip import ProtocolError
from .uds import Session
from .service import Service, KeepAliveConfig
from .response import UdsResponse

__all__ = ['Session', 'Service', 'UdsResponse', 'KeepAliveConfig', 'ProtocolError']
```

---

## 7. 审计发现 & 处理策略

| # | 问题 | 状态 |
|----|------|------|
| 1 | DoIP Routing Activation 缺失 | **不改** — tester-as-server 跳过路由激活是合法拓扑变体 |
| 2 | `_filter_ecus()` 覆盖原始 ECU 列表 | **待修** — 保存 `_ecus_original` 原始列表 |
| 3 | 无 ECU 地址/IP 校验 | **待修** — 添加轻量校验 |
| 4 | accept 单次循环 | **设计取舍** — 非 bug |
| 5 | `NRC 0x78` 处理 | **已解决** — `send()` 内置 0x78 等待链 |
| 6 | `_POSITIVE_HEAD_LEN` 未覆盖全部 SID | **低优先** — fallback 已正确处理 |

---

## 8. OEM 包

专有代码（Key 算法、PIN 查找表、unlock_ssh）移入独立 OEM 包。

```
src/workspace/oem/
├── __init__.py
├── keys.py        # calculate_key + make_key_calculator 工厂
├── platform.py    # unlock_ssh 等 OEM 专有方法
└── config/        # secrets.yaml + connections.yaml（gitignored）
```

| 函数 | 用途 |
|------|------|
| `make_key_calculator(platform) -> Callable` | 工厂：组装 `(level, seed) -> bytes`，注入 Diag/Uds |
| `unlock_ssh(service, soc_num=1) -> bool` | OEM 私有 DID 0xDC06 SSH 解锁 |

---

## 9. 从 v0.2 升级到 v1.0

### API 对照

```python
# ── v0.2（命令式）────────────────    # ── v1.0（声明式）────────────────
from Diag import Service               from Uds import UdsApp, session

ss = Service(ip=..., ecus=...)         class MyApp(UdsApp):
with ss:                                   @session(name='default')
    ss.change_session(0x03)                def default(self, payload): ...
    ss.change_level(0x05)
    resp = ss.read_did(0xF190)         with MyApp(ip=..., ecus=...) as app:
                                            # 装饰器声明 需要会话/需要安全等级
                                            resp = app.default(payload=b'...')
```

### 迁移步骤

1. **现有 `Service` 调用保持不变** — `Diag` 模块继续可用
2. **新建 `Uds` 模块** — `UdsApp` + `@session` 装饰器
3. **`UdsApp` 内部持有 `Service`** — 复用已稳定的命令式 API
4. **逐步迁移** — 新项目用 `UdsApp`，旧项目继续用 `Service`

### 关键变化

| 维度 | v0.2 (`Diag`) | v1.0 (`Uds`) |
|------|--------------|-------------|
| **注册方式** | 直接调用 `change_session()` / `send()` | `@session` 装饰器声明 |
| **会话管理** | 显式调用 | 装饰器 `sid=` 自动切换 |
| **安全访问** | 显式调用 | 装饰器 `level=` 自动执行 |
| **编解码** | 手动 hex ↔ bytes 转换 | 参数/返回类型标注 → 自动 |
| **NRC 0x78** | `send()` 内置等待链 | 同左（复用底层 `send()`） |
| **回退出口** | `ss >> '22DC06'` | `app.default(payload=b'\x22\xDC\x06')` |

