# Diag 模块设计文档

> **版本**: 0.1 | **作者**: 雷小鸥 | **更新**: 2026-06-02

---

## 文件结构（当前实际）

```
src/workspace/module/Diag/
├── __init__.py       # 导出 Session, Service, UdsResponse
├── __main__.py       # 使用演示 / 调试入口
├── uds.py            # UDS 层：KeepAlive + Session
├── service.py        # 业务层：Service(Session)，高层诊断操作
├── doip.py           # DoIP 层：Sock → SocketManager → Protocol → DoIPEndpoint
├── helper.py         # 工具函数：收帧、类型转换、PIN Code、Key 计算
├── response.py       # UdsResponse 数据类：正/负响应解析
└── errors.py         # 自定义异常：DoIpProtocolError
```

**核心模型**：一个 `Session` 持有一个 `DoIPEndpoint` + 一个 `KeepAlive`。用户通过 `Service(Session)` 获得高层诊断方法（`change_session`、`change_level`、`read_did` 等）。IO 串行化由 `DoIPEndpoint` 内部 `threading.Lock` 保证。

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
│  helper.py                         │  ← 工具：recv_frame、to_bytes、calculate_key、get_pin_code
│  errors.py                         │  ← DoIpProtocolError
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

        def start(self, ip, port, listen_count, timeout) -> None

        def stop(self) -> None

        def connections(self) -> list[str]

        def select(self, ip: str) -> None

        def current(self) -> str | None

        def send(self, data: bytes) -> None

        def recv(self) -> bytes

        def reconnect(self, timeout: float) -> None
```

自身不加锁，由 `DoIPEndpoint` 保证单线程访问。`start()` 时进入单次 accept 循环收集初始连接，之后通过 `select(ip)` 切换当前通信目标。

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
    def __init__(self, ip, port, tester, timeout, listen_count,
                 version, msg_type, byte_order, sock_type=None)

        def start(self) -> None

        def stop(self) -> None

        def connections(self) -> list[str]

        def select(self, ip: str, ecu: int) -> None

        def send(self, uds: bytes) -> bytes  # encode → 加锁 IO → decode；失败自动重连
```

`send(uds: bytes) -> bytes` 是唯一的 IO 原语。内部持有 `threading.Lock`，通信失败时自动触发一次重连。

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
                 port: int = 13400,
                 tester: int = 0x0E80,
                 timeout: float = 1,
                 listen_count: int = 10,
                 doip_version: int = 0x02,
                 doip_msg_type: int = 0x8001,
                 byte_order: 'little' | 'big' = 'big',
                 keepalive_interval: float = 0.5,
                 keepalive_payload: bytes = b'\x3E\x00')

    # 上下文管理器
    def __enter__() -> Self

        def __exit__(exc_type, exc_val, exc_tb) -> None

    # 运算符
    def __rshift__(self, uds: Any) -> Any  # session >> "22DC06"

    # 属性
    @property

    ecus -> MappingProxyType

    # 公开方法
    def start(self) -> bool

        def stop(self) -> bool

        def on(self, name: str) -> Self  # 切换 ECU

        def send(self, data: str) -> UdsResponse  # 发送 UDS 请求，返回 UdsResponse
```

**数据流**：`send(data: str)` → `_pre_send`（hex 字符串 → bytes）→ `endpoint.send(payload)` → `_post_receive`（bytes → UdsResponse）

---

## 业务层 (service.py)

`Service` 继承 `Session`，在其基础上提供高层诊断操作：

```python
class Service(Session):
    def __init__(self, ip, ecus, platform, serial_version=2.0, soc_num=1, **kwargs)

    # 会话 / 安全等级
    def change_session(self, ss_id: int) -> Tuple[bool, UdsResponse]

        def change_level(self, level: 'L1' | 'L5' | 'L19') -> Tuple[bool, UdsResponse]

        def change_any(self, ss_id=None, level=None) -> bool

    # ECU 控制
    def reset(self, reset_type=0x01) -> bool

        def unlock_ssh(self) -> bool

    # 数据读写
    def read_data_by_identifier(self, did, ss_id=None, level=None) -> UdsResponse

        def read_did(self, did, ss_id=None, level=None) -> UdsResponse

        def write_data_by_identifier(self, did, data, ss_id=None, level=None) -> UdsResponse

        def write_did(self, did, data, ss_id=None, level=None) -> UdsResponse

    # 例程控制
    def start_routine(self, routine_id, data=None, ss_id=None, level=None) -> UdsResponse

        def stop_routine(self, routine_id) -> bool

        def get_routine_result(self, routine_id=None) -> UdsResponse

    # 内部辅助
    def send_until(self, data, count=3, retry_delay=0.5) -> UdsResponse
```

**安全等级切换流程** (`change_level`)：

```
请求 Seed (27 {level})
  → 收到 Seed
    → 查表获取 PIN Code（get_pin_code）
      → 计算 Key（calculate_key）
        → 发送 Key (27 {level+1} {key})
          → 验证通过
```

---

## 响应解析 (response.py)

```python
@dataclass
class UdsResponse:
    raw: bytes  # 原始字节
    ok: bool  # 正响应为 True
    is_negative: bool  # 负响应为 True

    # 正响应字段
    sid: int | None  # 正响应 SID = 请求 SID + 0x40
    head: bytes | None  # 固定头部（DID、子功能等）
    body: bytes | None  # 可变数据负载

    # 负响应字段
    request_sid: int | None
    nrc: int | None
    nrc_desc: str | None  # 中文 NRC 描述

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

| 函数                                              | 用途                                             |
|-------------------------------------------------|------------------------------------------------|
| `recv_exact(sock, size)`                        | 精确收取指定字节数                                      |
| `recv_frame(sock)`                              | 收完整 DoIP 帧（8 字节头 + N 字节载荷）                     |
| `to_bytes(value)`                               | 统一类型 → bytes（支持 bytes/str/int/None）            |
| `calculate_key(level, seed, pin_code)`          | Seed/Key 安全访问算法（3 字节 seed 走自定义算法，其他走 AES-CMAC） |
| `get_pin_code(level, platform, serial_version)` | **PIN Code 查找表**（详见下方硬编码审计）                    |

---

## 🔴 硬编码审计

### 1. 密码学密钥 — `helper.py:94-122` `get_pin_code()`

PIN Code 查找表包含多个真实整车平台的 ECU 安全访问密钥：

| 等级  | 平台                             | PIN Code                           | 风险      |
|-----|--------------------------------|------------------------------------|---------|
| L1  | `P_30TU`, `P_G30TU`, `P_EEA40` | `FFFF…` (32×F)                     | 🔴 真实密钥 |
| L1  | `P_20_25_25S`                  | `FFFFFFFFFF` (10×F)                | 🔴 真实密钥 |
| L5  | `P_30TU`                       | `E853ECE43ABA6A39CB6CC221FC88B223` | 🔴 真实密钥 |
| L5  | `P_G30TU`, `P_EEA40`           | `51902E1AD902AF40119486A8DFA71708` | 🔴 真实密钥 |
| L5  | `P_20_25_25S` v2.0             | `FE63C818C2`                       | 🔴 真实密钥 |
| L5  | `P_20_25_25S` v2.5             | `7C9143F1BA`                       | 🔴 真实密钥 |
| L19 | 全部平台                           | `FFFF…`                            | 🔴 真实密钥 |

> ⚠️ **这些密钥已提交到 git 历史中。** 拥有密钥 + 网络接入即可对 ECU 执行任意诊断操作。

### 2. 连接 / 拓扑信息 — `__main__.py:26-28`

```python
Service(ip='198.18.44.1', platform='P_G30TU', ecus={
    'mcu': ('198.18.44.49', 0x1301),
    'soc': ('198.18.44.52', 0x1304),
})
```

- IP 地址暴露测试台架/车辆网络拓扑
- ECU 逻辑地址暴露车内节点分配
- 平台字符串暴露目标车型

### 3. 默认构造参数 — `uds.py:64` / `service.py:20`

| 参数                  | 默认值           | 敏感度         |
|---------------------|---------------|-------------|
| `tester`            | `0x0E80`      | 🟡 诊断仪身份    |
| `port`              | `13400`       | 🟢 ISO 标准端口 |
| `doip_version`      | `0x02`        | 🟢 ISO 标准   |
| `doip_msg_type`     | `0x8001`      | 🟢 ISO 标准   |
| `keepalive_payload` | `b'\x3E\x00'` | 🟢 UDS 标准   |

---

## 🏗️ 重构方案：YAML 配置文件 + .gitignore

### 目标

将敏感硬编码值从 Python 源码中分离到配置文件，配置文件 gitignore，模板文件提交。

### 新增文件结构

```
src/workspace/module/Diag/
├── config/
│   ├── secrets.yaml              ← 🔴 密钥（GITIGNORED）
│   ├── secrets.yaml.example      ← 🔴 密钥模板，占位符填充（提交）
│   ├── connections.yaml          ← 🟠 连接配置（GITIGNORED）
│   ├── connections.yaml.example  ← 🟠 连接配置模板（提交）
│   └── loader.py                 ← 配置加载器（提交）
```

### 各文件职责

| 文件                         | Git      | 内容                       |
|----------------------------|----------|--------------------------|
| `secrets.yaml`             | ❌ ignore | PIN Code 查找表，含真实密钥       |
| `secrets.yaml.example`     | ✅ commit | 同结构，值全部为 `<PLACEHOLDER>` |
| `connections.yaml`         | ❌ ignore | IP、ECU 地址、平台名            |
| `connections.yaml.example` | ✅ commit | 示例结构 + 假数据               |
| `loader.py`                | ✅ commit | 加载 / 解析 / 校验逻辑           |

### secrets.yaml 结构设计

```yaml
# ⚠️ 此文件包含 ECU 安全访问密钥，绝对不能提交到 git
# 复制 secrets.example.yaml 并填入真实值

pin_codes:
  - level: 1
    entries:
      - pin: "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"
        platforms: [ P_30TU, P_G30TU, P_EEA40 ]
      - pin: "FFFFFFFFFF"
        platforms: [ P_20_25_25S ]

  - level: 5
    entries:
      - pin: "E853ECE43ABA6A39CB6CC221FC88B223"
        platforms: [ P_30TU ]
      - pin: "51902E1AD902AF40119486A8DFA71708"
        platforms: [ P_G30TU, P_EEA40 ]
      - pin: "FE63C818C2"
        platforms: [ P_20_25_25S ]
        serial_version: 2.0
      - pin: "7C9143F1BA"
        platforms: [ P_20_25_25S ]
        serial_version: 2.5

  - level: 19
    entries:
      - pin: "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"
        platforms: [ P_30TU, P_G30TU, P_EEA40 ]
      - pin: "FFFFFFFFFF"
        platforms: [ P_20_25_25S ]
```

### connections.yaml 结构设计

```yaml
# 连接 / 拓扑配置
defaults:
  ip: "198.18.44.1"
  port: 13400
  tester: 0x0E80
  platform: "P_G30TU"
  serial_version: 2.0

ecus:
  mcu:
    ip: "198.18.44.49"
    logical_addr: 0x1301
  soc:
    ip: "198.18.44.52"
    logical_addr: 0x1304
```

### loader.py 接口设计

```python
# config/loader.py

from dataclasses import dataclass
from typing import Optional
import yaml  # 或 json（标准库零依赖）


@dataclass
class PinEntry:
    pin: str
    platforms: list[str]
    serial_version: Optional[float] = None


@dataclass
class PinCodeConfig:
    entries: dict[tuple[int, str, Optional[float]], str]  # (level, platform, version) → pin


@dataclass
class ConnectionConfig:
    ip: str
    port: int
    tester: int
    platform: str
    serial_version: float
    ecus: dict[str, dict]


def load_pin_codes(path: str = "config/secrets.yaml") -> PinCodeConfig:
    """加载 PIN Code 配置，文件不存在时给出清晰错误提示"""
    ...


def load_connections(path: str = "config/connections.yaml") -> ConnectionConfig:
    """加载连接配置"""
    ...
```

### .gitignore 追加

```gitignore
# Diag 模块 — 敏感配置文件
src/workspace/module/Diag/config/secrets.yaml
src/workspace/module/Diag/config/connections.yaml

# 以防用户直接放在模块根目录
src/workspace/module/Diag/secrets.yaml
src/workspace/module/Diag/connections.yaml
```

### 改造后的 get_pin_code

```python
# helper.py 改造后

from .config.loader import load_pin_codes

_pin_cache: dict | None = None


def get_pin_code(level: int, platform: str, serial_version: float = 2.0) -> str:
    global _pin_cache
    if _pin_cache is None:
        _pin_cache = load_pin_codes()

    # 精确匹配 (level, platform, version)
    key = (level, platform, serial_version)
    if key in _pin_cache:
        return _pin_cache[key]

    # 回退 (level, platform)
    key = (level, platform)
    if key in _pin_cache:
        return _pin_cache[key]

    raise ValueError(f'无 pin code 配置：level={level}, platform={platform}')
```

### Service 构造改造

```python
# service.py 改造后

class Service(Session):
    def __init__(self, ip=None, ecus=None, platform=None, ...):
        # 如果未显式传入，从配置文件加载
        conn = load_connections()
        ip = ip or conn.ip
        ecus = ecus or {
            name: (info['ip'], info['logical_addr'])
            for name, info in conn.ecus.items()
        }
        platform = platform or conn.platform
        ...
```

---

## ⚠️ 待解决问题（README 同步记录）

### 1. Git 历史泄露

密钥已经存在于 git 提交历史中。即使从当前代码中移除，仍可通过 `git log -p` 回溯。

**处置选项**：

- **方案 A**：使用 `git filter-branch` / `BFG Repo-Cleaner` 清除历史（需 force push，全员重新 clone）
- **方案 B**：若仓库尚未对外公开，暂缓处理；尽快轮换所有已泄露的 L5 密钥
- **方案 C**：将仓库设为私有 + 轮换密钥（最小改动）

> **建议**：先确认仓库可见范围。若公开/半公开，立即轮换密钥 + 清理历史；若仅个人使用，优先完成配置分离，后续再清理历史。

### 2. `FFFF…` 密钥的真实性

全真实

### 3. `__main__.py` 调试代码

文件中有大量被注释的调试/测试代码（~50 行），含平台名、DID、例程 ID 等。即使拆出配置，这些注释仍会泄露信息。

直接删除

### 4. 配置加载器依赖

当前项目无 `pyyaml` 依赖。选项：

- **`pyyaml`**：需添加到 `requirements.txt`，支持注释，用户体验好

### 5. 配置文件查找路径

`loader.py` 需要确定配置文件搜索顺序：

模块所在 `config/` 目录（默认）

---

## 实施步骤（建议顺序）

1. **创建 `config/` 目录结构**（`*.yaml`）
2. **改造 `helper.py`** — `get_pin_code()` 从 loader 读取
3. **改造 `service.py`** — 构造参数支持从配置文件读取默认值
4. **改造 `__main__.py`** — 清理硬编码，改为从配置文件读取
5. **更新 `.gitignore`**
6. **清理 `__main__.py` 调试注释**
7. **确认后轮换已泄露的密钥 + 清理 git 历史**

## ⚠️ 待解决问题 — 5 个需要关注的议题：

- Git 历史泄露及三种处置方案 全清理
- FFFF… 密钥真实性待确认 真实
- __main__.py 调试代码污染 删掉
- 配置加载器依赖选择（YAML）
- 配置文件搜索路径策略 默认 config 目录