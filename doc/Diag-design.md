# diag 设计文档

## 文件结构

```
src/module/diag/
├── doip.py           # DoIP 层：Sock, SocketManager, Protocol, DoIPEndpoint
├── handlers.py       # IHandler 抽象 + 内置实现（escape hatch，复杂逻辑用）
├── uds.py            # UDS 层：View, KeepAlive, Session + 编排引擎
├── helper.py         # 工具函数 + 预注册编码/解码/计算函数字典
├── errors.py         # 异常定义
├── core.py           # 旧版（待废弃）
├── Protocol.py       # 旧版（待废弃）
└── Abstractmethod.py # 旧版（待废弃）
```

**核心模型**：一个 Session 持有一个 DoIPEndpoint + 一个 View + 一个 KeepAlive。用户输入字符串/bytes/hex/结构体 → 统一编排引擎 → 查表编码 → send → 查表解码 → 返回。多线程并发安全由 Endpoint 内部锁保证。

---

## DoIP 层 (doip.py)

### Sock

```python
class Sock:
    def __init__(self, sock: socket.socket)
    def send(self, msg: bytes) -> None
    def recv(self) -> bytes
    def close(self) -> None
```

### SocketManager

```python
class SocketManager:
    def __init__(self, sock_type: type[Sock] | None = None)
    def start(self, ip: str, port: int, listen_count: int, timeout: float) -> None
    def stop(self) -> None
    def connections(self) -> list[str]
    def select(self, ip: str) -> None
    def current(self) -> str | None
    def send(self, data: bytes) -> None
    def recv(self) -> bytes
    def reconnect(self, timeout: float) -> None
```

自身不加锁，由 Endpoint 保证单线程访问。

### Protocol

```python
class Protocol:
    ERROR = DoIpProtocolError
    def __init__(self, version: int, msg_type: int, byte_order: Literal['little', 'big'])
    def encode(self, uds: bytes, tester: int, ecu: int) -> bytes
    def decode(self, frame: bytes, tester: int, ecu: int) -> bytes
```

纯编解码，无状态。`tester`/`ecu` 每次调用传入。

### DoIPEndpoint

```python
class DoIPEndpoint:
    def __init__(self, ip, port, tester, timeout, listen_count,
                 version, msg_type, byte_order, sock_type=None)
    def start(self) -> None
    def stop(self) -> None
    def connections(self) -> list[str]
    def select(self, ip: str, ecu: int) -> None
    def send(self, uds: bytes) -> bytes   # encode → IO（加锁，失败自动重连）→ decode
```

`send(uds: bytes) -> bytes` 是编排引擎唯一调用的 IO 原语。

---

## UDS 层 — 表驱动编排引擎

### 核心思路

不再为每个 UDS 服务写一个 IHandler 子类。用 **一套通用编排引擎 + 三张声明式表** 描述所有服务行为：

```
用户输入 ──→ 统一入口 encode(data, 编码表)
                │  (字符串→argparse解析→查表→assemble bytes)
                │  (hex str→fromhex         →透传)
                │  (bytes                   →透传)
                │  (结构体                  →.encode()或查表)
                ▼
            ┌─────────────────┐
            │  编排引擎        │
            │                 │
            │  for step in steps:                    │
            │    bytes = encode(step, ctx)           │
            │    resp  = endpoint.send(bytes)        │
            │    result = decode(resp, step.error,   │
            │                    错误表, 解码表)     │
            │    ctx.update(result.extracted)        │
            │    if nrc → 按错误表决策               │
            │      raise / retry / return / ignore   │
            │    goto step.next                      │
            │                 │
            │  final_error 决定最终抛还是返回        │
            └─────────────────┘
                │
                ▼
统一输出 decode(resp, 输出格式)
  (hex str / bytes / 结构体 / 语义化字符串)
```

### 预注册函数字典 (helper.py)

编码、解码、计算逻辑全部预注册为命名函数，表中用函数名引用：

```python
# helper.py

# --- 编码函数：参数元组 → bytes ---
ENCODE_FUNCS: dict[str, Callable] = {
    "uds_simple": lambda sid, **params: bytes([sid]) + bytes(params.values()),
}

# --- 解码函数：bytes → dict ---
DECODE_FUNCS: dict[str, Callable] = {
    "uds_positive": lambda resp: {"sid": resp[0], "data": resp[1:]},
    "uds_negative": lambda resp: {"sid": resp[0], "req_sid": resp[1], "nrc": resp[2]},
}

# --- 计算函数：多轮步骤间的数据变换 ---
COMPUTE_FUNCS: dict[str, Callable] = {
    "xor_ff": lambda seed: bytes([b ^ 0xFF for b in seed]),
}
```

### 服务定义表（编码 + 解码 + 错误 合并）

每个 UDS 服务一张定义，编码/解码/错误在一处，以内聚为原则。

以 **Diagnostic Session Control (0x10)** 为蓝图：

```python
SERVICE_TABLE = {
    "session": {
        # ===== 编码 =====
        "sid": 0x10,
        "params": [
            ("type", {
                "default":     0x01,
                "programming": 0x02,
                "extended":    0x03,
                "safety":      0x04,
            }),
        ],
        "encode": "uds_simple",   # 引用 ENCODE_FUNCS

        # ===== 解码 =====
        "decode": {
            "positive": {
                "match": "sid == 0x50",
                "decode": "uds_positive",  # 引用 DECODE_FUNCS
            },
            "negative": {
                "match": "sid == 0x7F",
                "decode": "uds_negative",
            },
        },

        # ===== 错误表 =====
        "nrc_actions": {
            0x11: "raise",    # serviceNotSupported → 抛异常
            0x12: "raise",    # subFunctionNotSupported
            0x13: "raise",    # incorrectMessageLength
            0x22: "return",   # conditionsNotCorrect → 返回错误信息不抛
            0x78: "retry",    # requestCorrectlyReceived-ResponsePending → 重发本条
        },
        "default_nrc": "raise",
    },
}
```

### 输入形式

`encode(data, service_def)` 统一处理四种输入：

| 输入形式 | 例子 | 处理 |
|---------|------|------|
| 命令行字符串 | `"session default"` | argparse 解析 → 查 params → assemble bytes |
| hex 字符串 | `"10 01"` | fromhex → 直接当 bytes，不查表 |
| bytes | `b'\x10\x01'` | 原样透传 |
| 结构体 | `UDSRequest(sid=0x10, ...)` | 调 `.encode()` 或走查表 |

### 输出形式

`decode(resp, service_def)` 统一处理四种输出：

| 输出形式 | 例子 | 处理 |
|---------|------|------|
| hex 字符串 | `"50 01"` | `resp.hex(' ')` |
| bytes | `b'\x50\x01'` | 原样返回 |
| 结构体/dict | `{"sid": 0x50, "echo_type": 0x01}` | 查解码表 → extract 字段 |
| 语义化字符串 | `"session default accepted"` | extract → 格式模板渲染 |

### 错误处理策略

编排引擎在每步 decode 后查错误表决定行为：

| 策略 | 行为 |
|------|------|
| `raise` | 抛异常，终止整个交互 |
| `return` | 不抛异常，返回错误信息的语义化解码结果 |
| `retry` | 重发当前步骤的请求（次数可配上限） |
| `ignore` | 忽略此错误，继续下一步 |

中间步骤（多轮非最后一步）错误必抛（不能 return/ignore，因为后续步骤依赖中间数据）。最终步骤按 `final_error` 标志决定是否抛出。

### 多轮服务 — 步骤链

在单轮服务定义的基础上加 `steps` 字段。以 **Security Access (0x27)** 为例：

```python
SERVICE_TABLE["security_l1"] = {
    # 编码（入口参数）
    "sid": 0x27,
    "params": [
        ("level", {"l1": 0x01, "l2": 0x03, "l3": 0x05}),
    ],
    "encode": "uds_simple",

    # 多轮步骤链
    "steps": [
        {
            "name": "request_seed",
            "encode": "uds_simple",          # 组装 27 01
            "decode": {
                "positive": {"match": "sid==0x67", "decode": "uds_positive"},
                "negative": {"match": "sid==0x7F", "decode": "uds_negative"},
            },
            "extract": {"seed": "data"},     # 从正响应取 seed 放入上下文
            "nrc_actions": {0x11: "raise"},
            "default_nrc": "raise",
            "next": "send_key",
        },
        {
            "name": "send_key",
            "encode": {
                "func": "uds_simple",
                "sid": 0x27,
                "subfunc": "${level} + 1",    # 引用上下文变量
                "data": "$xor_ff(${seed})",   # 引用计算函数 + 上下文变量
            },
            "decode": {
                "positive": {"match": "sid==0x67", "decode": "uds_positive"},
                "negative": {"match": "sid==0x7F", "decode": "uds_negative"},
            },
            "nrc_actions": {0x35: "raise"},  # invalidKey
            "default_nrc": "raise",
            "next": None,                     # 最后一步
        },
    ],
    "final_error": "return",  # 最终步错误：返回不抛
}
```

步骤间数据流：

```
request_seed: encode(27 01) → send → decode → extract $seed
    │
    ▼ $seed → compute xor_ff → $key
    │
send_key:    encode(27 02 + $key) → send → decode → return
```

### View

View 退化为服务选择器：用户通过 `view(name)` 切换当前使用的服务定义（`"session"`、`"security_l1"` 等）。

```python
class View:
    def __init__(self, service_table: dict)
    def use(self, name: str) -> 'View'       # 切换到 service_table[name]
        
    def current(self) -> dict                 # 当前服务定义
        
    @property
    def list(self) -> list[dict]              # 所有已注册服务信息
```

### IHandler — escape hatch

对于编码表/解码表无法描述的复杂逻辑（自定义加密算法、TLV 解析等），保留 IHandler 作为代码出口。`steps` 中的 `encode` 或 `decode` 可以直接引用一个 IHandler 实例而非函数名字符串，编排引擎检测到后直接调用其 `handle()` 或 `execute()`。

### Session

```python
class Session:
    def __init__(self, ip: str, ecus: dict[str, tuple[str, int]],
                 port: int = 13400, tester: int = 0x0E80,
                 timeout: float = 0.5, listen_count: int = 10,
                 doip_version: int = 0x02, doip_msg_type: int = 0x8001,
                 byte_order: Literal['little', 'big'] = 'big',
                 service_table: dict | None = None,
                 keepalive_interval: float = 0.5,
                 keepalive_payload: bytes = b'\x3E\x00')

    # 属性
    @property ecus
    @property views -> list[dict]

    # 方法
    def start(self) -> bool
    def stop(self) -> bool
    def on(self, name: str) -> Self       # 切换 ECU
    def view(self, name: str) -> Self     # 切换服务
    def send(self, data: Any) -> Any      # 统一入口：encode → 编排引擎 → decode → 返回
```

**`send` 数据流**：

```python
def send(self, data: Any) -> Any:
    service = self._view.current()
    steps = service.get("steps")

    if steps is None:
        # 单轮：encode → send → decode
        payload = encode(data, service, ENCODE_FUNCS)
        resp = self._endpoint.send(payload)
        return decode(resp, service, DECODE_FUNCS, service["nrc_actions"])
    else:
        # 多轮：走编排引擎
        return self._orchestrator.run(
            data, steps, service["final_error"],
            send=self._endpoint.send,
        )
```

**使用示例**：

```python
session = Session(ip, ecus)
session.start()

# 单轮：hex 直通
session >> "10 01"                         # → "50 01"

# 单轮：命令行字符串，查编码表
session.view("session") >> "session default"   # → "50 01"

# 多轮：安全访问
session.view("security_l1") >> "security l1"   # 自动两轮，返回最终结果

# 组合：多轮 + 最终不抛错误
session.view("security_l1") >> "security l1"   # key 错误时返回 NRC 信息而非抛异常
```
