# Diag 模块设计文档

> **版本**: 0.2 | **更新**: 2026-06-03

---

## 文件结构

```
src/workspace/module/Diag/
├── __init__.py       # 导出 Session, Service, UdsResponse + 三个 Config
├── __main__.py       # 使用演示
├── uds.py            # UDS 层：KeepAlive + Session
├── service.py        # 配置 dataclass + Service 业务层
├── doip.py           # DoIP 层：Sock → SocketManager → Protocol → DoIPEndpoint
├── helper.py         # 工具函数：recv_exact, recv_frame, to_bytes
├── response.py       # UdsResponse 数据类：正/负响应解析
└── errors.py         # DoIpProtocolError
```

**核心模型**：`Session` 持有 `DoIPEndpoint` + `KeepAlive`。`Service(Session)` 提供 ISO 14229 标准诊断方法。IO 串行化由 `DoIPEndpoint` 内部 `threading.Lock` 保证。专有部分（PIN Code、Key 算法）由外部通过 `set_key_calculator()` 注入。

---

## 分层架构

```
┌────────────────────────────────────┐
│  Service (service.py)              │  ← UDS 标准：会话/安全/读写/例程
│  继承 Session                       │
├────────────────────────────────────┤
│  Session (uds.py)                  │  ← 持有 Endpoint + KeepAlive，ECU 路由
│  KeepAlive (uds.py)                │  ← 后台 TesterPresent 心跳
├────────────────────────────────────┤
│  DoIPEndpoint (doip.py)            │  ← SocketManager + Protocol + Lock + 自动重连
│  Protocol (doip.py)                │  ← DoIP 帧编解码（ISO 13400）
│  SocketManager (doip.py)           │  ← Socket 生命周期 + 连接表 + 重连
│  Sock (doip.py)                    │  ← 单 socket 封装
├────────────────────────────────────┤
│  UdsResponse (response.py)         │  ← 正/负响应解析 + NRC 描述
│  helper.py                         │  ← recv_frame, to_bytes
│  errors.py                         │  ← DoIpProtocolError
└────────────────────────────────────┘
```

---

## 配置对象 (service.py)

```python
@dataclass
class DoIPConfig:
    """DoIP 传输层配置"""
    port: int = 13400
    tester: int = 0x0E80
    accept_timeout: float = 1.5      # 初始 accept 等待 ECU 连接
    recv_timeout: float = 3.0        # 客户端 recv 等待 UDS 响应
    reconnect_timeout: float = 5.0   # 断连后重建 accept 等待
    listen_count: int = 10
    version: int = 0x02
    msg_type: int = 0x8001
    byte_order: Literal['little', 'big'] = 'big'

@dataclass
class KeepAliveConfig:
    """TesterPresent 保活配置"""
    interval: float = 1.5
    payload: bytes = b'\x3E\x00'

@dataclass
class RetryConfig:
    """send_until 重试策略（ISO 14229 NRC 0x78）"""
    count: int = 3
    delay: float = 0.5
```

所有参数只在 config dataclass 中定义一次默认值。三个 timeout 职责独立：accept（连接快，短）、recv（UDS 响应，需更长）、reconnect（断连重建）。

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
    def start(self, ip, port, listen_count, accept_timeout, recv_timeout) -> None
    def stop(self) -> None
    def connections(self) -> list[str]
    def select(self, ip: str) -> None
    def current(self) -> str | None
    def send(self, data: bytes) -> None
    def recv(self) -> bytes
    def reconnect(self, timeout: float) -> None
```

server socket 用 `accept_timeout`，每个 client socket 用 `recv_timeout`。`reconnect` 接收独立超时。自身不加锁，由 `DoIPEndpoint` 保证单线程。

### Protocol

```python
class Protocol:
    ERROR = DoIpProtocolError
    def __init__(self, version, msg_type, byte_order)
    def encode(self, uds: bytes, tester: int, ecu: int) -> bytes
    def decode(self, frame: bytes, tester: int, ecu: int) -> bytes
```

### DoIPEndpoint

```python
class DoIPEndpoint:
    def __init__(self, ip, port, tester, accept_timeout, recv_timeout,
                 reconnect_timeout, listen_count, version, msg_type,
                 byte_order, sock_type=None)
    def start / stop / connections / select
    def send(self, uds: bytes) -> bytes   # encode → 加锁 IO → decode；失败用 reconnect_timeout 重连
```

---

## UDS 层 (uds.py)

### KeepAlive

```python
class KeepAlive:
    def __init__(self, fn: Callable[[bytes], bytes], interval: float, payload: bytes)
    def start / stop
```

### Session

```python
class Session:
    def __init__(self, ip: str, ecus: dict[str, tuple[str, int]],
                 doip: DoIPConfig | None = None,
                 keepalive: KeepAliveConfig | None = None)

    # 上下文管理器
    def __enter__() -> Self
    def __exit__(...)

    # 运算符: session >> "22DC06"
    def __rshift__(self, uds: Any) -> UdsResponse

    # 属性
    @property ecus -> MappingProxyType

    # 方法
    def start() -> bool
    def stop() -> bool
    def on(name: str) -> Self          # 切换 ECU
    def send(data: str) -> UdsResponse # hex 字符串 → bytes → DoIP → UdsResponse
```

数据流：`send("22DC06")` → hex→bytes → `endpoint.send()` → bytes→`UdsResponse`。

---

## 业务层 (service.py)

### Service

```python
class Service(Session):
    def __init__(self, ip: str, ecus: dict[str, tuple[str, int]],
                 doip: DoIPConfig | None = None,
                 keepalive: KeepAliveConfig | None = None,
                 retry: RetryConfig | None = None)

    # 注入点
    def set_key_calculator(self, fn: Callable[[int, bytes], bytes]) -> None

    # UDS 标准方法
    def change_session(self, ss_id: int) -> Tuple[bool, UdsResponse]      # 0x10
    def change_level(self, level: int) -> Tuple[bool, UdsResponse]        # 0x27
    def change_any(self, ss_id=None, level=None) -> bool
    def reset(self, reset_type=0x01) -> bool                               # 0x11
    def read_did(self, did, ss_id=None, level=None) -> UdsResponse         # 0x22
    def write_did(self, did, data, ss_id=None, level=None) -> UdsResponse  # 0x2E
    def start_routine(self, routine_id, data=None, ...) -> UdsResponse     # 0x31
    def stop_routine(self, routine_id) -> bool
    def get_routine_result(self, routine_id=None) -> UdsResponse

    # 内部
    def send_until(self, data, count=None, retry_delay=None) -> UdsResponse
```

- `level` 接收纯 `int`，L 奇数 `0x01`~`0xFD`，否则抛 `ValueError`
- `change_level` 调用前必须先 `set_key_calculator()`，否则抛 `RuntimeError`
- `send_until` 的 count/delay 缺省取自 `RetryConfig`

### change_level 流程

```
请求 Seed (27 {level})
  → 收到 Seed
    → 调用 key_calculator(level, seed) → key        ← 外部注入
      → 发送 Key (27 {level+1} {key})
        → 验证通过
```

### 使用示例

```python
from Diag import Service, DoIPConfig, RetryConfig

def my_key_calc(level: int, seed: bytes) -> bytes:
    pin = load_my_pin(level)           # 外部自行管理
    return my_algorithm(level, seed, pin)

ss = Service(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)})
ss.set_key_calculator(my_key_calc)

with ss:
    ss.change_session(0x03)
    ss.change_level(0x05)
    print(ss >> '22DC06')
```

---

## 响应解析 (response.py)

```python
@dataclass
class UdsResponse:
    raw: bytes              # 原始字节
    ok: bool
    is_negative: bool
    sid: int | None         # 正响应 SID = 请求 SID + 0x40
    head: bytes | None
    body: bytes | None
    request_sid: int | None # 负响应
    nrc: int | None
    nrc_desc: str | None    # 中文 NRC

    def check_fail(sid=None, head=None, body=None) -> bool
    @classmethod def from_bytes(data: bytes) -> Self
```

内置 `_NRC_DESC`（~30 条 NRC 中文描述）和 `_POSITIVE_HEAD_LEN`（~20 条 SID head 长度）。

---

## 工具函数 (helper.py)

| 函数 | 用途 |
|------|------|
| `recv_exact(sock, size)` | 精确收取指定字节数 |
| `recv_frame(sock)` | 收完整 DoIP 帧（8 字节头 + N 字节载荷） |
| `to_bytes(value, byte_order='big')` | 统一类型 → bytes。int 转换使用指定字节序 |

平台无关，可安全公开。

---

## 注入点 (`__init__.py`)

```python
from .uds import Session
from .service import Service, DoIPConfig, KeepAliveConfig, RetryConfig
from .response import UdsResponse

__all__ = ['Session', 'Service', 'UdsResponse',
           'DoIPConfig', 'KeepAliveConfig', 'RetryConfig']
```

---

## 错误参考

### 自定义异常

| 异常 | 位置 | 触发条件 |
|------|------|---------|
| `DoIpProtocolError` | `errors.py` | DoIP 帧不符合 ISO 13400：版本反码错、Payload Type≠0x8001、长度不匹配、源/目标地址不匹配、帧 < 12 字节 |

### 按层次统计

| 层次 | 异常 | 计数 | 触发场景 |
|------|------|------|---------|
| **helper.py** | | **2** | |
| | `ConnectionError` | 1 | `recv_exact` — 连接在收齐数据前关闭 |
| | `TypeError` | 1 | `to_bytes` — 传入不支持的类型 |
| **response.py** | | **1** | |
| | `ValueError` | 1 | `from_bytes` — 负响应帧 < 3 字节 |
| **doip.py** | | **15** | |
| | `RuntimeError` | 5 | `send`/`recv` 未选中 ECU、`reconnect` 未选中/未启动、`_accept_once` 未启动 |
| | `ConnectionError` | 2 | `select` 的 IP 不在连接表、`reconnect` 收到非预期 IP |
| | `DoIpProtocolError` | 7 | `decode` ×5（帧格式校验）+ `send` ECU 未设置 + `encode`/`send` 路径 |
| | `TimeoutError` | 1 | `send` 重连后 recv 仍失败 |
| **uds.py** | | **11** | |
| | `RuntimeError` | 6 | Endpoint 未初始化 ×3、未发现可连接 ECU、会话未启动 ×2 |
| | `ValueError` | 3 | hex 数据长度非偶数、非法 hex、未知 ECU 名 |
| | `TypeError` | 1 | send 传入非字符串 |
| | (`Exception` 捕获) | 1 | KeepAlive 心跳发送失败（日志记录 + 停止） |
| **service.py** | | **4** | |
| | `RuntimeError` | 2 | key_calculator 未注入、send_until 重试耗尽 |
| | `ValueError` | 2 | change_level level 非 L 奇数或超范围、get_routine_result 无 ID |

### 异常分类

| 类型 | 含义 |
|------|------|
| `ValueError` | 调用方传入非法参数（hex 格式、level 范围、ECU 名） |
| `TypeError` | 调用方传入错误类型（非字符串、不支持的类型） |
| `RuntimeError` | 状态机违规（未启动就操作、未注入 key_calculator） |
| `ConnectionError` | Socket 层连接异常（断开、IP 不匹配） |
| `DoIpProtocolError` | 协议层 — 收到的 DoIP 帧不符合标准 |
| `TimeoutError` | IO 超时 — ECU 无响应 |

### 设计原则

| 原则 | 说明 |
|------|------|
| 零专有代码 | `calculate_key`、`get_pin_code`、`unlock_ssh` 已删除。Key 算法由外部 `set_key_calculator()` 注入 |
| 零外部依赖 | 无 `pycryptodome`、无 `pyyaml`。仅标准库 + `socket` |
| 零配置文件 | `config/` 目录已删除。ip/ecus 显式传入 |
| 参数收敛 | 14 个构造参数 → 5 个（ip, ecus, doip, keepalive, retry），默认值只在 Config dataclass |
| timeout 三分 | `accept_timeout`（连接）、`recv_timeout`（响应）、`reconnect_timeout`（重连）独立 |
