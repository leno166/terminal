# Diag 模块设计文档

> **版本**: 0.1 | **作者**: 雷小鸥 | **更新**: 2026-06-02

---

## 文件结构（当前实际）

```
src/workspace/module/Diag/
├── __init__.py              # 导出 Session, Service, UdsResponse
├── __main__.py              # 使用演示（从 connections.yaml 加载配置）
├── uds.py                   # UDS 层：KeepAlive + Session
├── service.py               # 业务层：Service(Session)，高层诊断操作
├── doip.py                  # DoIP 层：Sock → SocketManager → Protocol → DoIPEndpoint
├── helper.py                # 工具函数：收帧、类型转换
├── response.py              # UdsResponse 数据类：正/负响应解析
├── errors.py                # 自定义异常：DoIpProtocolError
└── config/
    ├── __init__.py           # 配置包
    ├── loader.py             # YAML 配置加载器（PIN Code + 连接配置）
    ├── secrets.yaml          # 🔴 PIN Code 查找表（GITIGNORED）
    ├── secrets.example.yaml  # 密钥模板（提交）
    ├── connections.yaml      # 🟠 IP/ECU/平台连接配置（GITIGNORED）
    └── connections.example.yaml  # 连接配置模板（提交）
```

**核心模型**：一个 `Session` 持有一个 `DoIPEndpoint` + 一个 `KeepAlive`。用户通过 `Service(Session)` 获得高层诊断方法。IO 串行化由 `DoIPEndpoint` 内部 `threading.Lock` 保证。

---

## 分层架构

```
┌────────────────────────────────────┐
│  Service (service.py)              │  ← 高层业务：会话/安全等级切换、DID 读写、例程控制、SSH 解锁
│  继承自 Session                     │
├────────────────────────────────────┤
│  Session (uds.py)                  │  ← 用户入口：持有 Endpoint + KeepAlive，ECU 路由，send 原语
│  KeepAlive (uds.py)                │  ← 后台心跳线程
├────────────────────────────────────┤
│  DoIPEndpoint (doip.py)            │  ← 整合层：SocketManager + Protocol + Lock + 自动重连
│  Protocol (doip.py)                │  ← 纯编解码：DoIP 帧 ⇄ UDS payload
│  SocketManager (doip.py)           │  ← Socket 生命周期 + 连接表路由 + 重连
│  Sock (doip.py)                    │  ← 单 socket 封装
├────────────────────────────────────┤
│  UdsResponse (response.py)         │  ← 响应解析：正/负响应、字段提取、NRC 描述
│  helper.py                         │  ← 工具：recv_frame、to_bytes
│  errors.py                         │  ← DoIpProtocolError
├────────────────────────────────────┤
│  config/loader.py                  │  ← 配置加载：YAML → 扁平字典
│  config/secrets.yaml               │  ← 🔴 密钥文件（gitignored）
│  config/connections.yaml           │  ← 🟠 连接文件（gitignored）
└────────────────────────────────────┘
```

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

单 socket 封装，`recv()` 调用 `helper.recv_frame()` 完成 DoIP 帧的粘包/拆包。

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

自身不加锁，由 `DoIPEndpoint` 保证单线程访问。`start()` 时使用 `accept_timeout` 收集初始连接；每个 client socket 使用 `recv_timeout` 等待 UDS 响应。`reconnect` 可传入独立超时。

### Protocol

```python
class Protocol:
    ERROR = DoIpProtocolError
    def __init__(self, version: int, msg_type: int, byte_order: 'little' | 'big')
    def encode(self, uds: bytes, tester: int, ecu: int) -> bytes
    def decode(self, frame: bytes, tester: int, ecu: int) -> bytes
```

纯编解码，无状态。`tester` / `ecu` 每次调用传入。decode 时校验版本反码、Payload Type、长度、源/目标地址。

### DoIPEndpoint

```python
class DoIPEndpoint:
    def __init__(self, ip, port, tester, accept_timeout, recv_timeout,
                 reconnect_timeout, listen_count, version, msg_type,
                 byte_order, sock_type=None)
    def start(self) -> None
    def stop(self) -> None
    def connections(self) -> list[str]
    def select(self, ip: str, ecu: int) -> None
    def send(self, uds: bytes) -> bytes   # encode → 加锁 IO → decode；失败自动重连
```

`send(uds: bytes) -> bytes` 是唯一的 IO 原语。内部持有 `threading.Lock`，通信失败时使用 `reconnect_timeout` 自动重连（不再硬编码 5.0）。

---

## UDS 层 (uds.py)

### KeepAlive

```python
class KeepAlive:
    def __init__(self, fn: Callable[[bytes], bytes], interval: float, payload: bytes)
    def start(self) -> None
    def stop(self) -> None
```

后台守护线程，定时发送 TesterPresent（默认 `0x3E 0x00`）。发送失败时自动停止。

### Session

```python
class Session:
    def __init__(self,
                 ip: str,
                 ecus: dict[str, tuple[str, int]],
                 doip: DoIPConfig | None = None,
                 keepalive: KeepAliveConfig | None = None)

    # 上下文管理器
    def __enter__() -> Self
    def __exit__(exc_type, exc_val, exc_tb) -> None

    # 运算符
    def __rshift__(self, uds: Any) -> Any  # session >> "22DC06"

    # 属性
    @property ecus -> MappingProxyType

    # 公开方法
    def start(self) -> bool
    def stop(self) -> bool
    def on(self, name: str) -> Self          # 切换 ECU
    def send(self, data: str) -> UdsResponse # 发送 UDS 请求
```

**数据流**：`send(data: str)` → `_pre_send`（hex 字符串 → bytes）→ `endpoint.send(payload)` → `_post_receive`（bytes → UdsResponse）

---

## 配置对象 (config dataclasses)

为解决 `Session`/`Service` 参数膨胀问题（当前 ~14 个），将相关参数收敛到三个 dataclass 中。所有参数只在入口处定义默认值。

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class DoIPConfig:
    """DoIP 传输层配置"""
    port: int = 13400
    tester: int = 0x0E80
    accept_timeout: float = 1.5      # 初始 accept 等待 ECU 连接
    recv_timeout: float = 3.0        # 客户端 socket recv 等待 UDS 响应（需比 accept 长）
    reconnect_timeout: float = 5.0   # 断连后重建连接的 accept 等待
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
    """send_until 重试策略（ISO 14229 NRC 0x78 标准）"""
    count: int = 3
    delay: float = 0.5
```

**设计原则**：
- 三个入口参数集各管一摊：`DoIPConfig`（传输层）、`KeepAliveConfig`（心跳）、`RetryConfig`（应用层重试）
- 每个参数**只在 config 对象中定义一次默认值**，Session/Service 不再重复
- `DoIPConfig` 中三个 timeout 拆分：`accept_timeout`（短，连接快）、`recv_timeout`（长，复杂 UDS 操作需数秒）、`reconnect_timeout`（替换原硬编码 5.0）
- 命名去掉冗余前缀：`doip_version` → `version`，`keepalive_interval` → `interval`

---

## 业务层 (service.py)

`Service` 继承 `Session`，在其基础上提供 UDS 标准诊断操作。

```python
class Service(Session):
    def __init__(self, ip=None, ecus=None,
                 doip: DoIPConfig | None = None,
                 keepalive: KeepAliveConfig | None = None,
                 retry: RetryConfig | None = None,
                 **kwargs)

    # 会话 / 安全等级
    def change_session(self, ss_id: int) -> Tuple[bool, UdsResponse]
    def change_level(self, level: int) -> Tuple[bool, UdsResponse]  # L 奇数 0x01~0xFD
    def change_any(self, ss_id=None, level=None) -> bool

    # ECU 控制
    def reset(self, reset_type=0x01) -> bool

    # 数据读写
    def read_did(self, did, ss_id=None, level=None) -> UdsResponse
    def write_did(self, did, data, ss_id=None, level=None) -> UdsResponse

    # 例程控制
    def start_routine(self, routine_id, data=None, ss_id=None, level=None) -> UdsResponse
    def stop_routine(self, routine_id) -> bool
    def get_routine_result(self, routine_id=None) -> UdsResponse

    # 内部辅助
    def send_until(self, data, count=None, retry_delay=None) -> UdsResponse
```

- `ip`/`ecus` 可选，缺省从 `connections.yaml` 加载
- `level` 接收纯 `int`，L 奇数 `0x01`~`0xFD`（ISO 14229 行业惯例）
- `send_until` 自身参数缺省时使用实例 `RetryConfig` 默认值
- `platform`/`serial_version`/`soc_num` 已删除
- `unlock_ssh` 已删除 — 非 UDS 协议方法

### 参数收敛对比

| | 改造前 | 改造后 |
|------|--------|--------|
| Session 参数 | 11 个 | **4 个** (ip, ecus, doip, keepalive) |
| Service 参数 | 14 个（含 7 个透传重复） | **5 个** (ip, ecus, doip, keepalive, retry) |
| 默认值定义位置 | Session + Service 各一份（冲突：timeout 1 vs 1.5） | 各 Config 类唯一一份 |

### 使用方式

```python
# 全默认
ss = Service()
ss.start()

# 单项覆盖
ss = Service(doip=DoIPConfig(timeout=3.0))
ss = Service(retry=RetryConfig(count=5, delay=1.0))

# 完整自定义
ss = Service(
    doip=DoIPConfig(port=20000, tester=0x1234, byte_order='little'),
    keepalive=KeepAliveConfig(interval=2.0),
    retry=RetryConfig(count=5, delay=1.0),
)
```

**安全等级切换流程** (`change_level`)：

```
请求 Seed (27 {level})
  → 收到 Seed
    → 调用注入的 key_calculator(level, seed) → key
      → 发送 Key (27 {level+1} {key})
        → 验证通过
```
> `key_calculator` 由外部注入（`Service.set_key_calculator(fn)`），内部自行管理 PIN 查找 + 算法选择。

---

## 响应解析 (response.py)

```python
@dataclass
class UdsResponse:
    raw: bytes              # 原始字节
    ok: bool                # 正响应为 True
    is_negative: bool       # 负响应为 True

    # 正响应字段
    sid: int | None         # 正响应 SID = 请求 SID + 0x40
    head: bytes | None      # 固定头部（DID、子功能等）
    body: bytes | None      # 可变数据负载

    # 负响应字段
    request_sid: int | None
    nrc: int | None
    nrc_desc: str | None    # 中文 NRC 描述

    # 方法
    def check_fail(sid=None, head=None, body=None) -> bool
    @classmethod 
    def from_bytes(data: bytes) -> Self
```

内置两张静态映射表：
- `_NRC_DESC`：NRC → 中文描述（~30 条）
- `_POSITIVE_HEAD_LEN`：正响应 SID → head 字节长度（~20 条）

---

## 工具函数 (helper.py)

| 函数 | 用途 |
|------|------|
| `recv_exact(sock, size)` | 精确收取指定字节数 |
| `recv_frame(sock)` | 收完整 DoIP 帧（8 字节头 + N 字节载荷） |
| `to_bytes(value, byte_order='big')` | 统一类型 → bytes（bytes/str/int/None）。int 转换时使用指定字节序 |

`Session` 持有的 `_byte_order` 可传入 `to_bytes`，保证 int→bytes 与 DoIP 帧编码一致。

---

## 配置系统 (config/)

### .gitignore 策略

```
src/workspace/module/Diag/config/*.yaml           ← 忽略所有 .yaml
!src/workspace/module/Diag/config/*.example.yaml  ← 但保留模板
```

| 文件 | Git | 内容 |
|------|-----|------|
| `secrets.yaml` | ❌ ignore | PIN Code 查找表，含真实密钥 |
| `secrets.example.yaml` | ✅ commit | 同结构，值全部为 `<PLACEHOLDER>` |
| `connections.yaml` | ❌ ignore | IP、ECU 地址、平台名 |
| `connections.example.yaml` | ✅ commit | 示例结构 + 假数据 |
| `loader.py` | ✅ commit | 加载 / 解析 / 校验逻辑 |

### loader.py 接口

```python
# 连接配置
load_connections() -> dict      # 原始 YAML 字典
get_defaults() -> dict          # defaults 段
get_ecus() -> dict[str, tuple[str, int]]  # {name: (ip, logical_addr)}
```

---

## 🔴 硬编码审计（已处置）

### 1. 密码学密钥 — 已从代码中分离

- **处置**：已拆分到 `config/secrets.yaml`（gitignored）
- `get_pin_code` 将在公共化拆分时删除，其逻辑下沉到外部 `key_calculator`
- **Git 历史**：经核查，密钥从未进入任何 commit（仅在工作区存在过）

### 2. 连接 / 拓扑信息 — 已分离

- **处置**：已拆分到 `config/connections.yaml`（gitignored）
- `Service()` 支持无参构造，自动从配置文件加载
- `__main__.py` 已清理硬编码和调试注释

### 3. 协议常量 — 保留在代码中

`tester=0x0E80`, `port=13400`, `doip_version=0x02` 等均为 ISO 标准值，可安全保留。

---

## 🏗️ 公共化拆分方案（待实施）

当前模块混合了通用 DoIP/UDS 协议实现和公司专有逻辑。核心思路：**专有部分变成可注入的回调**，公共库不关心 PIN Code 从哪来、Key 怎么算。

### 关键设计：`key_calculator` 回调注入（必须实现）

`calculate_key` 和 `get_pin_code` 从模块中**全部删除**。Key 计算完全由外部实现并注入。`change_level` 在调用时检查 `_key_calculator` 是否已设置，未注入则报错。

回调签名：`(level: int, seed: bytes) -> bytes`。内部自行管理 PIN 查找和算法。

### 注入点：`__init__.py` 暴露

```python
# __init__.py

from .uds import Session
from .service import Service
from .service import DoIPConfig, KeepAliveConfig, RetryConfig

# Service 暴露 setter，让外部注入 key 计算逻辑
Service.set_key_calculator = lambda self, fn: setattr(self, '_key_calculator', fn)

__all__ = ['Session', 'Service', 'UdsResponse',
           'DoIPConfig', 'KeepAliveConfig', 'RetryConfig']
```

### 目标结构

```
diag/                              ← pip install diag (公共库)
├── __init__.py                    ← 导出 Session, Service
├── errors.py                      ← DoIpProtocolError
├── doip.py                        ← Sock, SocketManager, Protocol, DoIPEndpoint
├── response.py                    ← UdsResponse
├── uds.py                         ← Session, KeepAlive
├── service.py                     ← Service + 通用 UDS 方法，key_calculator 可注入
└── helper.py                      ← recv_exact, recv_frame, to_bytes

oem/                               ← 公司私有包（内部仓库 / 无仓库）
├── keys.py                        ← 实现 key_calculator(level, seed) → bytes
│                                    内部自行管理 PIN Code 查找 + 算法实现
├── platform.py                    ← unlock_ssh + 其他专有方法
└── config/
    ├── secrets.yaml               ← gitignored
    └── connections.yaml           ← gitignored
```

### 通用 vs 专有分类

| 组件 | 归属 | 处置 |
|------|------|------|
| `doip.py` 全部、`response.py` 全部、`errors.py` | ✅ 公共 | 直接保留 |
| `uds.py` Session / KeepAlive | ✅ 公共 | 直接保留 |
| `helper.py` recv_exact / recv_frame / to_bytes | ✅ 公共 | 直接保留 |
| `helper.py` calculate_key / get_pin_code | 🔴 专有 | **删除**，完全由外部 key_calculator 实现 |
| `service.py` change_session / reset / read/write_did / routine | ✅ 公共 | 直接保留 |
| `service.py` send_until | ✅ 公共 | 直接保留 |
| `service.py` change_level | 🟡 混合 | 改为调用注入的 `_key_calculator(level, seed)` |
| `service.py` unlock_ssh | 🔴 专有 | **删除**，非 UDS 协议，OEM 私有 DID 操作 |
| `config/loader.py` + YAML 配置 | 🔴 专有 | → `oem/config/` |

### change_level 改造后

```python
# service.py

class Service(Session):
    def __init__(self, ..., retry: RetryConfig | None = None):
        ...
        self._key_calculator: Callable[[int, bytes], bytes] | None = None
        self._retry = retry or RetryConfig()

    def set_key_calculator(self, fn: Callable[[int, bytes], bytes]):
        """注入 Key 计算回调。fn(level, seed) -> key_bytes。必须调用。"""
        self._key_calculator = fn

    def change_level(self, level: int) -> Tuple[bool, UdsResponse]:
        # 校验：L 奇数，范围 0x01~0xFD（ISO 14229 行业惯例）
        if not (0x01 <= level <= 0xFD and level % 2 == 1):
            raise ValueError(
                f"安全等级必须为 L 奇数 (0x01~0xFD)，收到: 0x{level:02X}"
            )

        if self._key_calculator is None:
            raise RuntimeError(
                "key_calculator 未注入。"
                "请先调用 service.set_key_calculator(fn)，"
                "fn(level: int, seed: bytes) -> bytes 负责 PIN 查找和 Key 计算。"
            )

        resp = self.send_until(f'27 {level:02X}')
        if resp.check_fail(0x67, level):
            return False, resp

        seed = to_bytes(resp.body)
        key = self._key_calculator(level, seed)          # ← 唯一的调用点
        resp = self.send_until(f'27 {level + 1:02X} {key.hex()}')
        if resp.check_fail(0x67, level + 1):
            return False, resp

        return True, resp

    def send_until(self, data, count=None, retry_delay=None):
        count = count if count is not None else self._retry.count
        retry_delay = retry_delay if retry_delay is not None else self._retry.delay
        ...

```

### 外部使用

```python
from Diag import Service

# ---- 调用者实现 key_calculator ----
def my_key_calculator(level: int, seed: bytes) -> bytes:
    # 内部自行管理：PIN Code 来源 + Key 算法
    pin = my_pin_lookup(level)
    return my_key_algorithm(level, seed, pin)

# ---- 使用 ----
ss = Service()
ss.set_key_calculator(my_key_calculator)   # 必须注入
ss.change_level(0x05)
```

---

## ⚠️ 待解决问题

无。所有专有逻辑已识别并规划删除/外部化。`to_bytes` 已支持 `byte_order` 参数，`Session._byte_order` 可传入。公共化实施后模块零专有代码、零外部依赖。

---

## 实施步骤

| # | 内容 | 状态 |
|---|------|------|
| 1 | 创建 `config/` 目录 + YAML 配置 + loader | ✅ 已完成 |
| 2 | `__main__.py` 清理硬编码和调试注释 | ✅ 已完成 |
| 3 | `.gitignore` 忽略 `*.yaml`，保留 `*.example.yaml` | ✅ 已完成 |
| 4 | `service.py` — `change_level` 改为调用 `_key_calculator(level, seed)`，缺省报错 | ⬜ 待实施 |
| 5 | `helper.py` — 删除 `get_pin_code` 和 `calculate_key` | ⬜ 待实施 |
| 6 | `service.py` — 添加 `DoIPConfig`/`KeepAliveConfig`/`RetryConfig` dataclass | ⬜ 待实施 |
| 7 | `Session` — 收拢 DoIP/KeepAlive 参数为 config 对象 | ⬜ 待实施 |
| 8 | `Service` — 收拢 retry 参数为 config 对象，消除与 Session 的重复默认值 | ⬜ 待实施 |
| 9 | `__init__.py` — 暴露 config 对象 + `Service.set_key_calculator()` | ⬜ 待实施 |
| 10 | `service.py` — 删除 `platform`/`serial_version`/`soc_num`/`unlock_ssh` | ⬜ 待实施 |
| 11 | `helper.py` — 删除 `get_pin_code`/`calculate_key`；`to_bytes` 添加 `byte_order` | ⬜ 待实施 |
| 12 | OEM 包 — 实现 key_calculator（PIN 查找 + 算法，完全自主） | ⬜ 待实施 |