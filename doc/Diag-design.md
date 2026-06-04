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
├── doip.py           # DoIP 层：Sock → SocketManager → Protocol → Endpoint
├── helper.py         # 工具函数：recv_exact, recv_frame, to_bytes
├── response.py       # UdsResponse 数据类：正/负响应解析
└── errors.py         # ProtocolError
```

**核心模型**：`Session` 持有 `Endpoint` + `KeepAlive`。`Service(Session)` 提供 ISO 14229 标准诊断方法。IO 串行化由 `Endpoint` 内部 `threading.Lock` 保证。专有部分（PIN Code、Key 算法、unlock_ssh）已移入独立 `oem/` 包，通过 `set_key_calculator()` 注入。

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
│  Endpoint (doip.py)            │  ← SocketManager + Protocol + Lock + 自动重连
│  Protocol (doip.py)                │  ← DoIp 帧编解码（ISO 13400）
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
class Config:
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

---

## 审计发现 & 处理策略

以下问题已识别，不修改 Diag 模块本身。

### #1 DoIP Routing Activation（不改）

ISO 13400 要求发送 UDS 前先发路由激活请求（0x0005）。当前设计为 tester-as-server 模式，ECU 主动连接，跳过路由激活。这是合法的 DoIP 拓扑变体，不修改。

### #2 `_filter_ecus()` 覆盖原始 ECU 列表

**问题**：`Session.start()` 调用 `_filter_ecus()` 后，`self._ecus` 被替换为当前已连接 ECU 子集。`stop()` → `start()` 时，只有上次过滤后的 ECU 可被重连。

**策略**：`__init__` 中保存 `self._ecus_original` 原始列表，`_filter_ecus()` 和 `start()` 始终从原始列表过滤，不修改 `self._ecus`。`self._ecus` 只读返回当前已连接的子集。

### #3 无 ECU 地址/IP 校验

**问题**：`ecus` dict 中的 logical_addr 无范围校验（合法 0x0001~0xFFFF），IP 字符串未校验格式。

**策略**：在 `Session.__init__` 中添加轻量校验：
- logical_addr: `0x0001 <= addr <= 0xFFFF`
- IP: `socket.inet_aton()` 格式检查
校验失败抛 `ValueError`，成本低，不影响性能路径。

### #4 accept 单次循环

**问题**：`_accept_once()` 在 `accept_timeout`（默认 1.5s）内循环收连接，超时即退出。ECU 若延迟上线会被永久遗漏。

**策略**：这是设计取舍，非 bug。tester-as-server 模式下 ECU 应在 tester 启动前已在线。如需后上线支持，调用方可在外部重试 `start()` 或使用 `reconnect()` 针对特定 IP 重连。不修改。

### #5 send_until 仅重试 NRC 0x78

**问题**：ISO 14229 也定义了 NRC 0x21（busy）可重试。当前仅处理 0x78（responsePending）。

**策略**：NRC 0x78 是唯一必须重试的码（ECU 明确要求等待）。0x21 在实践中极少触发，且重试可能加剧 ECU 负载。保持现状。如需扩展，`RetryConfig` 可加 `retryable_nrcs: set[int]` 字段。

### #6 _POSITIVE_HEAD_LEN 未覆盖全部 SID

**问题**：缺少 0x61(WriteMemoryByAddress)、0x72/0x73(Download/Upload) 等较新的 UDS 服务。

**策略**：fallback 逻辑已正确处理未知 SID — `head = payload` 即全部作为 head 返回。不影响解析正确性，仅语义标注稍粗。后续按需补充表项即可，非紧急。

---

## OEM 包

专有代码（Key 算法、PIN 查找表、unlock_ssh）移入独立 OEM 包，与 MIT 许可的 Diag 模块彻底分离。

### 位置

```
src/workspace/oem/
├── __init__.py              # 导出 make_key_calculator, unlock_ssh
├── keys.py                  # calculate_key + get_pin_code + make_key_calculator 工厂
├── platform.py              # unlock_ssh 等 OEM 专有方法
└── config/
    ├── __init__.py
    ├── secrets.yaml          # 🔴 PIN Code 查找表（gitignored）
    ├── secrets.example.yaml  # 模板（提交）
    ├── connections.yaml      # 🟠 连接配置（gitignored）
    └── connections.example.yaml  # 模板（提交）
```

### 使用方式

```python
from module.Diag import Service
from oem import make_key_calculator, unlock_ssh

ss = Service(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)})

# 注入 OEM 的 key_calculator
ss.set_key_calculator(make_key_calculator('P_G30TU'))

with ss:
    ss.change_session(0x03)
    ss.change_level(0x05)

    # OEM 扩展方法
    unlock_ssh(ss, soc_num=1)
```

### keys.py 接口

| 函数 | 签名 | 用途 |
|------|------|------|
| `calculate_key` | `(level, seed, pin_code) -> bytes` | 3字节 seed 自定义算法 + AES-CMAC |
| `get_pin_code` | `(level, platform, serial_version) -> str` | 平台→PIN Code 查找 |
| `make_key_calculator` | `(platform, serial_version) -> Callable` | 工厂：组装成 `(level, seed) -> bytes` 可注入 Diag |

### platform.py 接口

| 函数 | 签名 | 用途 |
|------|------|------|
| `unlock_ssh` | `(service, soc_num=1) -> bool` | OEM 私有 DID 0xDC06 SSH 解锁 |


---

## UX 远景：终极用户体验 (v1.0 目标)

> **像 HTTP 客户端一样用 UDS。零查表，零背定义，全中文，全语义。**

---

### 1. 设计哲学

| 原则 | 说明 |
|------|------|
| **声明式** | 用装饰器声明"这是什么服务"，不用写 hex 字符串 |
| **零查表** | 无需记忆 SID、NRC、DID 编码。框架自动处理 |
| **双语言** | 继承中文类得中文 API，继承英文类得英文 API。用户自选，互不干扰 |
| **类型驱动** | 声明 Python 类型 → 框架自动编解码 |
| **渐进式** | 空子类即用全部标准服务；装饰器扩展自定义服务；`原始诊断()` 兜底 |

类比：

| 概念 | HTTP 世界 | UDS 诊断世界 |
|------|----------|-------------|
| 请求方法 | `@app.get("/path")` | `@读取DID(0xF190)` |
| 参数解析 | FastAPI 的类型推导 | 参数类型 → 自动编码 |
| 响应解析 | `response.json()` | `响应.转为字符串()` / `响应.转为整数()` |
| 错误处理 | `response.status_code` | `响应.成功` / `响应.错误描述` |
| 原始请求 | `requests.post(...)` | `原始诊断(0x22, 0xF190)` |

---

### 2. 30 秒体验

```python
from Diag import 诊断仪, 读取DID, 写入DID, 诊断服务, 例程控制

class 我的诊断仪(诊断仪):
    """零代码即可使用全部标准 UDS 服务。装饰器只定义 OEM 扩展。"""

    @读取DID(0xF190, 名称="VIN码", 需要会话=0x03)
    def 读VIN(self) -> str:
        """返回类型 str → 框架自动 bytes → ASCII 解码。self 已经是解码后的字符串。"""
        if len(self) != 17:
            raise ValueError(f"VIN 长度应为 17，实际 {len(self)}")
        return self

    @读取DID(0xF120, 名称="车速", 单位="km/h")
    def 读车速(self) -> int:
        """返回类型 int → 框架自动 1 字节无符号整数。"""
        return self

    @读取DID(0xF113, 名称="电池电压", 单位="V")
    def 读电池电压(self) -> float:
        """返回类型 float → 框架自动 2 字节整数 ÷ 1000。"""
        return self  # mV → V

    @写入DID(0xF190, 名称="VIN码", 需要安全等级=0x05)
    def 写VIN(self, vin: str) -> None:
        """参数 str → 框架自动 ASCII 编码。需要安全等级自动触发 安全访问。"""
        if len(vin) != 17:
            raise ValueError("VIN 必须 17 位")

    @诊断服务(0x31, 子功能=0x01, 名称="启动刷写")
    def 启动刷写(self, 例程ID: int = 0xFF00) -> 诊断响应:
        """自定义 SID。返回原始响应供调用方自行解析。"""
        return self  # self 是 诊断响应


# ── 使用：像 HTTP 客户端一样自然 ──
with 我的诊断仪(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)}) as d:

    # 一级 API：语义方法，自动管理会话和安全等级
    vin = d.读VIN()              # → "LSVAU2A38M2100123"
    车速 = d.读车速()            # → 120
    电压 = d.读电池电压()        # → 12.5

    # 一级 API 不够用时，用二级 API 裸发送
    resp = d.原始诊断(0x22, 0xF121)
    rpm = resp.转为整数()         # → 3000

    # 标准服务自动可用（空子类继承）
    d.切换会话(0x03)
    d.安全访问(0x05, key_calculator=my_key_calc)
    d.ECU复位(复位类型=0x01)
```

---

### 3. 中文优先 API 总览

#### 3.1 核心类：一个基类，两个入口

所有逻辑集中在私有基类 `_诊断仪基类`。用户不直接继承它，而是二选一：

```
_诊断仪基类 (核心逻辑，不对外)
  ├── 诊断仪   (中文 API)
  └── DiagTester (English API)
```

```python
# 中文用户
from Diag import 诊断仪, 诊断配置, 诊断响应, 诊断结构

# 英文用户
from Diag import DiagTester, DiagConfig, DiagResponse, DiagStruct
```

两者功能完全等价，仅方法名和字段名不同。装饰器也有中英文两套：

```python
# 中文装饰器
from Diag import 读取DID, 写入DID, 诊断服务, 例程控制

# 英文装饰器
from Diag import read_did, write_did, diag_service, routine_control
```

| 中文 | 英文 | 说明 |
|------|------|------|
| `诊断仪` | `DiagTester` | 用户继承的入口类。持有传输层 + 服务注册表 |
| `诊断配置` | `DiagConfig` | 合并 DoIP + KeepAlive + Retry 配置 |
| `诊断响应` | `DiagResponse` | 中文/英文语义响应 dataclass |
| `诊断结构` | `DiagStruct` | 复合 DID 数据结构的装饰器基类 |
| `_诊断仪基类` | (private) | 所有逻辑实现，不对外暴露 |

#### 3.2 一级 API：语义方法（空子类自动继承）

所有逻辑在私有基类中实现。`诊断仪` 和 `DiagTester` 仅方法名不同，功能完全等价：

| 对应 UDS | `诊断仪` (中文) | `DiagTester` (English) | 说明 |
|---------|----------------|----------------------|------|
| — | `启动()` | `start()` | 启动 DoIP 监听，接受 ECU 连接 |
| — | `停止()` | `stop()` | 关闭所有连接，停止心跳 |
| — | `切换ECU(名称)` | `switch_ecu(name)` | 切换到指定 ECU 的逻辑通道 |
| — | `ECU列表` (属性) | `ecu_list` (属性) | 已连接 ECU 的只读映射 |
| 0x10 | `切换会话(会话ID)` | `change_session(id)` | 切换诊断会话 |
| 0x27 | `安全访问(安全等级)` | `security_access(level)` | 安全访问（seed/key） |
| 0x22 | `读取DID(DID)` | `read_did(did)` | 通过标识符读取数据 |
| 0x2E | `写入DID(DID, 数据)` | `write_did(did, data)` | 通过标识符写入数据 |
| 0x31 | `启动例程(例程ID)` | `start_routine(rid)` | 启动例程（子功能 0x01） |
| 0x31 | `停止例程(例程ID)` | `stop_routine(rid)` | 停止例程（子功能 0x02） |
| 0x31 | `获取例程结果(例程ID)` | `get_routine_result(rid)` | 获取例程结果（子功能 0x03） |
| 0x11 | `ECU复位(复位类型)` | `ecu_reset(type)` | ECU 复位 |
| — | `设置密钥计算器(fn)` | `set_key_calculator(fn)` | 注入 `(level, seed) -> key` 回调 |
| 任意 | `原始诊断(SID, 子功能, 数据)` | `uds(sid, sub, data)` | 二级 API — 原始回退出口 |

#### 3.3 二级 API：原始诊断回退

当一级 API 和装饰器注册的方法都不覆盖某个 SID 时，用 `原始诊断()` / `uds()` 直接发送任意 UDS 请求：

```python
# 中文
resp = d.原始诊断(0x23, 数据=bytes([0x12, 0x34, 0x00, 0x10]))

# English
resp = d.uds(0x23, data=bytes([0x12, 0x34, 0x00, 0x10]))
```

#### 3.4 使用模式：选中文还是英文，用户决定

```python
# ── 中文用户：继承 诊断仪，用中文装饰器 ──
from Diag import 诊断仪, 读取DID, 写入DID, 诊断服务

class 我的诊断仪(诊断仪):
    """零代码即可用。装饰器只定义 OEM 扩展。"""

    @读取DID(0xF190, 名称="VIN码")
    def 读VIN(self) -> str: return self

    @写入DID(0xF190, 名称="VIN码")
    def 写VIN(self, vin: str) -> None: pass


# ── 英文用户：继承 DiagTester，用英文装饰器 ──
from Diag import DiagTester, read_did, write_did, diag_service

class MyTester(DiagTester):
    """Same as above, English API."""

    @read_did(0xF190, name="VIN")
    def read_vin(self) -> str: return self

    @write_did(0xF190, name="VIN")
    def write_vin(self, vin: str) -> None: pass
```

**两个类的实例完全等价，只是调用方式不同：**

```python
# 中文风格
with 我的诊断仪(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)}) as d:
    print(d.读VIN())
    d.切换会话(0x03)
    resp = d.原始诊断(0x22, 0xDC06)
    if not resp.成功:
        print(resp.错误描述)

# English style — same behavior, different names
with MyTester(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)}) as d:
    print(d.read_vin())
    d.change_session(0x03)
    resp = d.uds(0x22, 0xDC06)
    if not resp.ok:
        print(resp.error_desc)
```

---

### 4. 装饰器驱动的服务注册

#### 4.1 空子类即用

```python
class 我的诊断仪(诊断仪):
    """零代码。所有标准一级 API 直接可用。"""
    pass

with 我的诊断仪(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)}) as d:
    d.切换会话(0x03)
    resp = d.读取DID(0xF190)
    if resp.成功:
        print(resp.转为字符串())
```

#### 4.2 `@读取DID` / `@写入DID`

```python
class 我的诊断仪(诊断仪):

    @读取DID(0xF190, 名称="VIN码", 描述="车辆识别码（17位）", 需要会话=0x03)
    def 读VIN(self) -> str:
        """
        流程（框架自动）：
        1. 发送 22 F190
        2. 接收响应，提取 body
        3. body bytes → ASCII str（因为返回类型是 str）
        4. 将转换后的 str 通过 self 传入本方法
        5. 此处可做额外校验
        """
        return self

    @写入DID(0xF190, 名称="VIN码", 需要会话=0x03, 需要安全等级=0x05)
    def 写VIN(self, vin: str) -> None:
        """
        流程（框架自动）：
        1. 如果当前会话 ≠ 0x03 → 自动 切换会话(0x03)
        2. 如果当前安全等级 < 0x05 → 自动 安全访问(0x05)
        3. str → ASCII bytes
        4. 发送 2E F190 + <编码后的数据>
        """
        pass  # 无需 return，写入结果由框架验证
```

#### 4.3 `@诊断服务`（自定义 SID）

当 UDS 标准服务（0x22/0x2E 等）不够用时，用 `@诊断服务` 注册任意 SID：

```python
class 我的诊断仪(诊断仪):

    @诊断服务(0x2F, 名称="IO控制", 需要安全等级=0x05)
    def IO控制(self, DID: int, 控制参数: int) -> 诊断响应:
        """自定义 IO 控制。框架自动编码两个 int 参数。"""
        return self  # self 是 诊断响应

    @诊断服务(0x31, 子功能=0x01, 名称="OEM自检")
    def OEM自检(self, 参数: bytes = None) -> 诊断响应:
        """OEM 自定义例程。"""
        return self

# 使用
with 我的诊断仪(...) as d:
    resp = d.IO控制(DID=0x1234, 控制参数=0x01)
    if not resp.成功:
        print(f"IO 控制失败: {resp.错误描述}")
```

#### 4.4 `@例程控制`

```python
class 我的诊断仪(诊断仪):

    @例程控制(0xFF01, 名称="擦除内存", 需要会话=0x02, 需要安全等级=0x05)
    def 擦除内存(self) -> 诊断响应:
        """框架自动发送 31 01 FF01。"""
        return self

    @例程控制(0xFF02, 名称="检查编程依赖", 子功能=0x03)
    def 检查依赖(self) -> 诊断响应:
        """非标准子功能，显式指定 sub=0x03。"""
        return self
```

#### 4.5 装饰器通用参数

每个注册装饰器（`@读取DID` / `@写入DID` / `@诊断服务` / `@例程控制`）都接受以下**可选关键字参数**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `名称` | `str` | 方法名 | 中文显示名，用于日志和错误消息 |
| `描述` | `str` | `None` | 人类可读的描述文字，可用于自动生成文档 |
| `需要会话` | `int` | `None` | 如 `0x03`。框架在调用前自动 `切换会话` 到目标值 |
| `需要安全等级` | `int` | `None` | 如 `0x05`。框架在调用前自动 `安全访问` 到目标等级 |
| `重试` | `bool` | `True` | 是否自动处理 NRC 0x78（responsePending） |
| `重试次数` | `int` | 取自配置 | 单次请求最大重试次数 |

**"自动升级"语义**：
- `需要会话=0x03` → 如果当前会话不是 0x03，框架先调 `切换会话(0x03)`
- `需要安全等级=0x05` → 如果当前等级 < 0x05，框架先调 `安全访问(0x05)`
- 若 `安全访问` 需要密钥计算器但未注入，抛 `RuntimeError("请先调用 设置密钥计算器()")`
- 用户无需显式调用 `切换会话` 或 `安全访问`，除非有特殊流程需求

---

### 5. 自动类型转换

#### 5.1 返回类型 → 解析规则

装饰器方法通过**返回类型标注**决定如何解析 UDS 响应 body：

```python
class 我的诊断仪(诊断仪):

    @读取DID(0xF190)
    def 读VIN(self) -> str: ...       # bytes → ASCII 字符串（自动 strip null）

    @读取DID(0xF121)
    def 读RPM(self) -> int: ...       # bytes → big-endian 无符号整数

    @读取DID(0xF113)
    def 读电压(self) -> float: ...    # bytes → int → ÷ 1000 → float

    @读取DID(0xF100)
    def 读原始(self) -> bytes: ...    # 不转换，直接返回 body bytes

    @读取DID(0xF101)
    def 读开关(self) -> bool: ...     # b'\x01' → True, b'\x00' → False

    @读取DID(0xF102)
    def 读多字节(self) -> list[int]: ... # 按 1 字节步长拆分为整数列表
```

| 返回类型 | 转换规则 | 示例 |
|---------|---------|------|
| `str` | bytes → ASCII 解码，strip null | `b'LSVAU2\x00'` → `"LSVAU2"` |
| `int` | bytes → big-endian unsigned int | `b'\x0B\xB8'` → `3000` |
| `float` | bytes → int → `÷ 1000` | `b'\x30\xD4'` → `12.5` |
| `bool` | `b'\x01'` → `True`, `b'\x00'` → `False` | |
| `bytes` | 不转换，原样返回 | |
| `list[int]` | 每 1 字节一个整数，组成列表 | `b'\x01\x02\x03'` → `[1, 2, 3]` |
| `诊断结构` 子类 | 按结构字段声明解包 | 见 §5.3 |

#### 5.2 参数类型 → 编码规则

对于 `@写入DID` 和 `@诊断服务`，参数类型决定编码规则：

```python
class 我的诊断仪(诊断仪):

    @写入DID(0xF190)
    def 写VIN(self, vin: str) -> None:      # str → ASCII bytes

    @写入DID(0xF121)
    def 写RPM阈值(self, rpm: int) -> None:  # int → 最小 big-endian bytes

    @写入DID(0xF120)
    def 写车速上限(self, 速度: float) -> None: # float → ×1000 → int → bytes
        pass
```

| 参数类型 | 编码规则 |
|---------|---------|
| `str` | ASCII 编码为 bytes |
| `int` | 转为最小 big-endian bytes（如 `3000` → `b'\x0B\xB8'`） |
| `float` | `×1000` → `int` → bytes |
| `bytes` | 直接发送，不做转换 |
| `bool` | `True` → `b'\x01'`, `False` → `b'\x00'` |

如有两个 `int` 参数，框架按声明顺序编码后拼接。用户可以覆盖 `int` 参数的字节宽度：

```python
@诊断服务(0x2F, 名称="IO控制")
def IO控制(self, DID: int(2), 控制参数: int(1)) -> 诊断响应:
    """DID 固定 2 字节，控制参数固定 1 字节。"""
    return self
```

#### 5.3 `@诊断结构`：复合 DID 解析

当一个 DID 返回多个字段时，用 `@诊断结构` 声明字段布局：

```python
from Diag import 诊断结构

@诊断结构(字节序='big')
class 车速信息:
    """对应一个车载 DID 的复合数据结构，字段偏移自动累加。"""
    车速: int          # 1 byte，单位 km/h
    RPM: int(2)        # 2 bytes，单位 rpm
    温度: int          # 1 byte，offset -40
    故障标志: bool     # 1 byte

    def 温度_摄氏度(self) -> int:
        """字段上的辅助方法。"""
        return self.温度 - 40

class 我的诊断仪(诊断仪):

    @读取DID(0xF120, 结构=车速信息)
    def 读车速信息(self) -> 车速信息:
        """框架自动将 body bytes 按 车速信息 字段声明解包。"""
        return self  # self 是 车速信息 实例

# 使用
info = d.读车速信息()
print(info.车速)           # → 120
print(info.RPM)            # → 3000
print(info.温度_摄氏度())  # → 85
print(info.故障标志)       # → False
```

`@诊断结构` 字段类型默认字节宽度：

| 字段类型 | 默认字节数 | 可覆盖 |
|---------|-----------|--------|
| `int` | 1 | `int(2)`, `int(4)` |
| `float` | 2 | `float(4)` |
| `bool` | 1 | — |
| `bytes` | 剩余全部 | `bytes(8)` |

字段支持 `偏移` 和 `缩放` 参数：

```python
@诊断结构(字节序='big')
class 电池状态:
    电压: float(2, 缩放=1000)      # 2 bytes, mV ÷ 1000 → V
    电流: float(2, 缩放=100)       # 2 bytes, ÷ 100 → A
    SOC: int                        # 1 byte, %
    保留: bytes(偏移=5, 长度=3)    # 显式偏移 + 长度
```

#### 5.4 自定义转换器

用户可以注入自定义类型转换逻辑，覆盖默认规则：

```python
class 我的诊断仪(诊断仪):

    def 自定义转换器(self, 目标类型: type, 原始数据: bytes):
        """
        覆盖此方法以支持自定义类型转换。
        返回 None 则回退到框架默认规则。
        """
        if 目标类型 is 时间戳:
            return 时间戳.从字节解析(原始数据)
        if 目标类型 is 版本号:
            主, 次, 补丁 = 原始数据[0], 原始数据[1], 原始数据[2]
            return 版本号(主=主, 次=次, 补丁=补丁)
        return None  # 其他类型用框架默认规则

    @读取DID(0xF200)
    def 读生产日期(self) -> 时间戳:
        """由 自定义转换器 处理。"""
        return self
```

---

### 6. 中文语义响应

#### 6.1 响应类：同样中英文分离

和诊断仪一样，响应类也是基类 + 两个子类：

```
_诊断响应基类 (核心解析逻辑，不对外)
  ├── 诊断响应   (中文字段)
  └── DiagResponse (English fields)
```

中文版 `诊断响应`：

```python
@dataclass
class 诊断响应:
    """所有诊断操作的统一返回类型（中文版）。"""

    # ── 状态 ──
    成功: bool              # True = 正响应，False = 负响应
    SID: int | None         # 正响应 SID = 请求 SID + 0x40

    # ── 负响应专用 ──
    请求SID: int | None     # 原始请求 SID（仅负响应）
    错误码: int | None      # NRC（Negative Response Code）
    错误描述: str | None    # 中文 NRC 描述

    # ── 数据 ──
    头部: bytes | None      # 正响应 head（SID 特定前缀）
    正文: bytes | None      # 正响应 body
    原始数据: bytes         # 完整原始字节，始终可用

    # ── 便捷转换 ──
    def 转为字符串(self, 编码='ascii') -> str: ...
    def 转为整数(self, 字节序='big') -> int: ...
    def 转为浮点(self, 缩放=1000.0) -> float: ...
    def 转为布尔(self) -> bool: ...
```

英文版 `DiagResponse`（字段名不同，行为一致）：

```python
@dataclass
class DiagResponse:
    """English version. Same logic, English field names."""

    ok: bool
    sid: int | None
    request_sid: int | None
    error_code: int | None
    error_desc: str | None     # still Chinese NRC desc (or set locale)
    head: bytes | None
    body: bytes | None
    raw: bytes

    def as_str(self, encoding='ascii') -> str: ...
    def as_int(self, byte_order='big') -> int: ...
    def as_float(self, scale=1000.0) -> float: ...
    def as_bool(self) -> bool: ...
```

中文字段 ← → 英文字段对照：

| `诊断响应` | `DiagResponse` |
|-----------|---------------|
| `resp.成功` | `resp.ok` |
| `resp.错误码` | `resp.error_code` |
| `resp.错误描述` | `resp.error_desc` |
| `resp.头部` | `resp.head` |
| `resp.正文` | `resp.body` |
| `resp.原始数据` | `resp.raw` |
| `resp.转为字符串()` | `resp.as_str()` |
| `resp.转为整数()` | `resp.as_int()` |

#### 6.2 成功/失败判断模式

```python
# 模式 A：询问式（Pythonic）
resp = d.读取DID(0xF190)
if resp.成功:
    print(resp.转为字符串())
else:
    print(f"读取失败: {resp.错误描述}")  # → "读取失败: 安全访问被拒绝"

# 模式 B：装饰器方法返回具体类型时，失败返回 None
vin = d.读VIN()
if vin is None:
    print("VIN 读取失败")
else:
    print(f"VIN: {vin}")

# 模式 C：抛出式（需要精确错误处理时）
resp = d.原始诊断(0x22, 0xF190)
data = resp.转为字符串()  # 失败时返回空字符串，而不是抛异常
# 或显式要求抛出：
try:
    data = resp.转为字符串(失败时抛出=True)
except ValueError as e:
    print(f"转换失败: {e}")
```

#### 6.3 内置常见 NRC 中文描述

`错误描述` 自动填充中文文本，无需查表：

| NRC | 错误描述 | 说明 |
|-----|---------|------|
| 0x10 | 常规拒绝 | General Reject |
| 0x11 | 服务不支持 | Service Not Supported |
| 0x12 | 子功能不支持 | Sub-Function Not Supported |
| 0x13 | 报文长度错误或格式无效 | Incorrect Message Length |
| 0x22 | 条件不满足 | Conditions Not Correct |
| 0x24 | 请求序列错误 | Request Sequence Error |
| 0x31 | 请求超出范围 | Request Out Of Range |
| 0x33 | 安全访问被拒绝 | Security Access Denied |
| 0x35 | 密钥无效 | Invalid Key |
| 0x36 | 超过尝试次数 | Exceeded Number Of Attempts |
| 0x37 | 所需延时未到 | Required Time Delay Not Expired |
| 0x70 | 上传下载不被接受 | Upload/Download Not Accepted |
| 0x71 | 数据传输暂停 | Transfer Data Suspended |
| 0x72 | 一般编程错误 | General Programming Failure |
| 0x78 | 请求正确接收，响应待定 | Request Correctly Received — Response Pending（框架已自动重试） |
| 0x7E | 当前会话不支持子功能 | Sub-Function Not Supported In Active Session |
| 0x7F | 当前会话不支持该服务 | Service Not Supported In Active Session |

---

### 7. 完整场景示例

#### 7.1 基础诊断流程

```python
from Diag import 诊断仪

class 诊断(诊断仪):
    pass

with 诊断(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)}) as d:
    resp = d.读取DID(0xF190)
    if resp.成功:
        print(f"VIN: {resp.转为字符串()}")
    else:
        print(f"失败: {resp.错误描述}")
```

#### 7.2 多 ECU

```python
class 诊断(诊断仪):

    @读取DID(0xF190, 名称="VIN码")
    def 读VIN(self) -> str: return self

with 诊断(ip='198.18.44.1', ecus={
    'mcu':  ('198.18.44.49', 0x1301),
    'bms':  ('198.18.44.50', 0x1302),
    'obc':  ('198.18.44.51', 0x1303),
}) as d:
    print(d.读VIN())          # MCU 的 VIN
    d.切换ECU('bms')
    print(d.读VIN())          # BMS 的 VIN
    d.切换ECU('obc')
    print(d.读VIN())          # OBC 的 VIN
```

#### 7.3 安全访问 + DID 读写

```python
from oem import make_key_calculator

class 诊断(诊断仪):

    @读取DID(0xF190, 名称="VIN码", 需要会话=0x03)
    def 读VIN(self) -> str: return self

    @写入DID(0xF190, 名称="VIN码", 需要会话=0x03, 需要安全等级=0x05)
    def 写VIN(self, vin: str) -> None: pass

with 诊断(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)}) as d:
    # 注入密钥计算器（仅需一次）
    d.设置密钥计算器(make_key_calculator('P_G30TU'))

    # 框架自动：切换会话(0x03) → 安全访问(0x05) → 写入 VIN
    d.写VIN('LSVAU2A38M2100999')

    # 框架自动：当前会话已是 0x03，安全等级已是 0x05，无需重复
    print(d.读VIN())  # → "LSVAU2A38M2100999"
```

#### 7.4 例程控制

```python
class 诊断(诊断仪):

    @例程控制(0xFF01, 名称="擦除内存", 需要会话=0x02, 需要安全等级=0x05)
    def 擦除内存(self) -> 诊断响应: return self

    @例程控制(0xFF02, 名称="检查编程依赖", 需要会话=0x02)
    def 检查编程依赖(self) -> 诊断响应: return self

with 诊断(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)}) as d:
    d.设置密钥计算器(make_key_calculator('P_G30TU'))

    resp = d.擦除内存()
    if resp.成功:
        print("内存擦除成功")
    else:
        print(f"擦除失败: {resp.错误描述}")
```

#### 7.5 原始诊断回退

```python
class 诊断(诊断仪):
    pass

with 诊断(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)}) as d:
    # 一级 API 不覆盖的 SID：用二级 API
    resp = d.原始诊断(0x23, 数据=bytes([0x12, 0x34, 0x00, 0x10]))
    if resp.成功:
        print(f"内存数据: {resp.正文.hex(' ')}")
    else:
        print(f"读取失败: {resp.错误描述}")
```

#### 7.6 OEM 自定义服务全家桶

```python
from Diag import 诊断仪, 读取DID, 写入DID, 诊断服务, 诊断响应

class OEM诊断仪(诊断仪):

    # ── 标准 DID ──
    @读取DID(0xF190, 名称="VIN码", 需要会话=0x03)
    def 读VIN(self) -> str: return self

    @读取DID(0xF187, 名称="总里程", 单位="km", 需要会话=0x03)
    def 读总里程(self) -> int: return self

    # ── OEM 专用 DID ──
    @读取DID(0xDC06, 名称="SSH状态", 需要会话=0x03)
    def 读SSH状态(self) -> bool:
        """OEM 私有 DID 0xDC06。bool 返回。"""
        return self

    @写入DID(0xDC06, 名称="解锁SSH", 需要会话=0x03, 需要安全等级=0x05)
    def 解锁SSH(self, 端口号: int = 1) -> None:
        """OEM 私有写入 DID。"""
        pass

    @诊断服务(0x31, 子功能=0x01, 名称="OEM自检")
    def OEM自检(self) -> 诊断响应:
        return self


with OEM诊断仪(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)}) as d:
    d.设置密钥计算器(make_key_calculator('P_G30TU'))

    print(f"VIN: {d.读VIN()}")
    print(f"里程: {d.读总里程()} km")
    print(f"SSH: {'已解锁' if d.读SSH状态() else '已锁定'}")
    d.解锁SSH(端口号=22)
    d.OEM自检()
```

---

### 8. 从 v0.2 升级到 v1.0

#### API 对照

```python
# ── v0.2（当前）───────────────  # ── v1.0（远景）─────────────────

# 导入                             # 导入
from Diag import Service          from Diag import 诊断仪 as DiagTester
from Diag import UdsResponse      from Diag import 诊断响应

# 构造                             # 构造
ss = Service(ip=..., ecus=...)    d = 诊断仪(ip=..., ecus=...)
ss.set_key_calculator(fn)         d.设置密钥计算器(fn)

# 上下文                           # 上下文
with ss:                          with d:

    # 手动管理会话/安全                # 装饰器声明 需要会话/需要安全等级 → 全自动
    ss.change_session(0x03)           # 不需要显式调用
    ss.change_level(0x05)             # 不需要显式调用
    resp = ss.read_did(0xF190)        resp = d.读取DID(0xF190)

    # 响应判断                         # 响应判断
    if resp.ok:                       if resp.成功:
        print(resp.body)                  print(resp.正文)

    # 原始发送                         # 原始发送
    ss >> '22DC06'                    d.原始诊断(0x22, 0xDC06)
```

#### 关键变化

| 维度 | v0.2 | v1.0 |
|------|------|------|
| **注册方式** | 子类覆写方法，手写 hex | `@读取DID(0xF190)` 装饰器声明 |
| **会话管理** | 显式调用 `change_session(0x03)` | 装饰器 `需要会话=0x03`，框架自动切换 |
| **安全访问** | 显式调用 `change_level(0x05)` | 装饰器 `需要安全等级=0x05`，框架自动执行 |
| **类型转换** | 手动 `resp.body.decode()` | 返回类型标注 `-> str`，框架自动转换 |
| **响应字段** | 英文 `resp.ok`, `resp.body` | 中文 `resp.成功`, `resp.正文` |
| **错误描述** | 英文 NRC 码 | 中文 `resp.错误描述` |
| **扩展性** | 子类加方法 | 装饰器 + `@诊断服务` + `@诊断结构` |
| **回退出口** | `ss >> "22DC06"` | `d.原始诊断(0x22, 0xDC06)` |

#### 迁移后代码对比

```python
# ── v0.2：手写 hex，手动管理流程 ──
class OldService(Service):
    pass

ss = Service(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)})
ss.set_key_calculator(my_key_calc)

with ss:
    ss.change_session(0x03)
    ss.change_level(0x05)
    resp = ss.read_did(0xF190)
    if resp.ok:
        vin = resp.body.decode('ascii').strip('\x00')
        print(vin)
```

```python
# ── v1.0：装饰器注册，全自动 ──
class NewTester(诊断仪):

    @读取DID(0xF190, 名称="VIN码", 需要会话=0x03, 需要安全等级=0x05)
    def 读VIN(self) -> str: return self

with NewTester(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)}) as d:
    d.设置密钥计算器(my_key_calc)
    print(d.读VIN())  # 一行搞定
```

---

### 9. 装饰器行为规范（总结）

```
┌─ 调用装饰器方法（如 d.读VIN()）────────────────────────────┐
│                                                          │
│  ① 前置检查                                              │
│     ├─ 需要会话 ≠ 当前会话 → 自动 切换会话(需要会话)        │
│     ├─ 需要安全等级 > 当前等级 → 自动 安全访问(需要安全等级) │
│     └─ 安全访问需要密钥但未注入 → RuntimeError              │
│                                                          │
│  ② 编码请求                                              │
│     ├─ 根据参数类型标注编码 payload                       │
│     └─ 调用 原始诊断(SID, 子功能, 编码后的数据)            │
│                                                          │
│  ③ 响应处理                                              │
│     ├─ NRC 0x78 且 重试=True → 等待后回到 ②              │
│     ├─ 负响应 → 返回 诊断响应（成功=False）               │
│     └─ 正响应 → 根据返回类型标注解析 body                 │
│                                                          │
│  ④ 方法体执行                                            │
│     ├─ self 绑定为转换后的值（str/int/结构体/诊断响应）    │
│     ├─ 方法体做校验 / 后处理                              │
│     └─ 返回最终值给调用方                                 │
│                                                          │
└──────────────────────────────────────────────────────────┘
```
