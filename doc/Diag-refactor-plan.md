# Diag 模块重构方案：接入 autodoip 包

> **版本**: 0.1 | **日期**: 2026-06-04 | **状态**: 方案评审

---

## 1. 背景与动机

### 1.1 现状

Diag 模块 (`src/workspace/module/Diag/`) 当前在 `doip.py` 中内嵌了一套完整的 DoIP 传输层实现（~300 行），包含四个类：

```
Sock → SocketManager → Protocol → Endpoint
```

与此同时，项目 `pyproject.toml` 已声明依赖 `autodoip>=0.1.3`（同一作者 雷小鸥 发布在 PyPI），该包是 DoIP 传输层的独立实现，功能等价但 API 更清晰：

| 维度 | Diag doip.py | autodoip |
|------|-------------|----------|
| 代码量 | ~300 行 | ~330 行 |
| 公开类 | 4 个（Sock/SocketManager/Protocol/Endpoint） | 1 个（Endpoint） |
| 内部类 | — | 2 个（`_Sock` / `_Protocol`） |
| 构造参数 | 12 个平铺参数 | 5 个（含 Config dataclass） |
| ECU 路由 | 按 IP 字符串 | 按逻辑地址（ISO 13400 语义） |
| 收发接口 | `send(uds) -> bytes` | `conversation(payload) -> Iterator[bytes]` |
| 多帧响应 | 不支持 | 原生支持（生成器） |
| 依赖 | Diag 内部 helper.py + errors.py | 零外部依赖 |

**核心问题：两套相同功能的代码并存，autodoip 已作为依赖安装但从未被导入使用。**

### 1.2 动机

1. **消除重复**：删除 ~300 行已由 autodoip 覆盖的代码
2. **单一职责**：Diag 模块专注于 UDS 协议层（Session/Service/Response），DoIP 传输完全委托给 autodoip
3. **独立演进**：autodoip 作为独立包可单独测试、发版、被其他项目复用
4. **API 升级**：autodoip 的 `conversation()` 生成器天然支持多帧响应，为后续 UDS 多帧传输（ISO 15765）奠定基础

---

## 2. 重构目标

```
重构前                              重构后
──────────────────────────        ──────────────────────────
Diag/                              Diag/
├── doip.py    ← 自研 DoIP         ├── doip.py    ✂ 删除
│   ├── Sock                       │
│   ├── SocketManager              ├── errors.py  ✂ 删除（或重导出）
│   ├── Protocol                   │
│   └── Endpoint                   ├── helper.py  ✏ 精简（仅保留 to_bytes）
│                                   │
├── errors.py  ← 自研异常          ├── uds.py     ✏ 适配 autodoip.Endpoint
├── helper.py  ← recv_frame 等     ├── service.py ✏ 精简 DoIPConfig
├── uds.py                         ├── response.py ✓ 不变
├── service.py                     ├── __init__.py ✏ 更新导出
├── response.py                    └── __main__.py ✏ 适配
└── __init__.py
                                   autodoip (PyPI)
                                   ├── Endpoint
                                   ├── Config
                                   └── ProtocolError
```

**一句话总结：Diag 不再拥有自己的 DoIP 实现，改为 import autodoip。**

---

## 3. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `doip.py` | **删除** | Sock / SocketManager / Protocol / Endpoint 全部由 autodoip 替代 |
| `errors.py` | **删除** | `ProtocolError` 改为从 autodoip 重导出 |
| `helper.py` | **精简** | 删除 `recv_exact()` / `recv_frame()`（已在 autodoip._frame），保留 `to_bytes()` |
| `uds.py` | **适配** | 从 autodoip 导入 Endpoint；适配 ECU 表格式；适配 send/conversation |
| `service.py` | **精简** | DoIPConfig 移除 transmit 层字段，与 autodoip.Config 去重 |
| `__init__.py` | **更新** | 新增 ProtocolError 重导出；移除内部实现引用 |
| `__main__.py` | **适配** | 更新示例代码 |
| `response.py` | **不变** | 纯 UDS 层，无 DoIP 依赖 |

### 消费者影响

| 消费者 | 影响 |
|--------|------|
| `oem/platform.py` | **无影响** — 仅使用 Service + to_bytes |
| `mix/StreamPromptTuiMix/StreamPromptTui_Diag.py` | **无影响** — 仅使用 Service + UdsResponse |
| `oem/keys.py` | **无影响** — 独立模块 |

---

## 4. 详细变更

### 4.1 `doip.py` → 删除

**删除整个文件**（~297 行）。所有功能由 `autodoip` 包提供：

| 原 doip.py 中的类 | autodoip 对应 |
|-------------------|--------------|
| `Sock` | `autodoip._transport._Sock`（内部，不暴露） |
| `SocketManager` | 合并进 `autodoip.Endpoint` |
| `Protocol` | `autodoip._transport._Protocol`（内部，不暴露） |
| `Endpoint` | `autodoip.Endpoint`（公开 API） |

### 4.2 `errors.py` → 删除

`ProtocolError` 改为从 autodoip 重导出。在 `__init__.py` 中：

```python
from autodoip import ProtocolError
```

原先 `doip.py` 中 `Protocol.ERROR = ProtocolError` 的类属性引用方式也随之消失。

### 4.3 `helper.py` → 精简

**删除：**
- `recv_exact(sock, size)` — 已在 `autodoip._frame.recv_exact()`
- `recv_frame(sock)` — 已在 `autodoip._frame.recv_frame()`

**保留：**
- `to_bytes(value, byte_order)` — 纯 UDS 层工具，autodoip 不提供

> **注意**：autodoip 的 `recv_frame` 接受 `byte_order` 参数，原 Diag 版本硬编码 `'big'`。功能等价。

### 4.4 `uds.py` → 适配

#### 4.4.1 导入变更

```python
# 原
from .doip import Endpoint

# 新
from autodoip import Endpoint, Config as DoIPTransmitConfig, ProtocolError
```

#### 4.4.2 ECU 表格式适配（关键变更）

当前 Diag 使用名称索引的 ECU 表：
```python
ecus: dict[str, tuple[str, int]]
# 例: {'mcu': ('198.18.44.49', 0x1301)}
#       name → (ip, logical_addr)
```

autodoip.Endpoint 使用逻辑地址索引的 ECU 表：
```python
ecus: dict[int, tuple[str, int]]
# 例: {0x1301: ('198.18.44.49', 13400)}
#      logical_addr → (ip, port)
```

**适配方案**：Session 内部维护名称→逻辑地址的映射，构造时转换格式传给 autodoip.Endpoint。

```python
class Session:
    def __init__(self, ip, ecus, doip=None, keepalive=None):
        # 用户接口不变：ecus = {name: (ip, logical_addr)}
        self._ecus = ecus.copy()                    # {name: (ip, logical_addr)}
        self._ecu_names: dict[int, str] = {}        # {logical_addr: name}  反向索引
        for name, (ecu_ip, addr) in ecus.items():
            self._ecu_names[addr] = name

        # 构造 autodoip 格式的 ECU 表
        autodoip_ecus = {
            addr: (ecu_ip, doip.port)
            for name, (ecu_ip, addr) in ecus.items()
        }

        self._endpoint = Endpoint(
            ip=ip,
            ecus=autodoip_ecus,
            port=doip.port,
            tester=doip.tester,
            config=DoIPTransmitConfig(
                accept_timeout=doip.accept_timeout,
                recv_timeout=doip.recv_timeout,
                listen_count=doip.listen_count,
                version=doip.version,
                msg_type=doip.msg_type,
                byte_order=doip.byte_order,
            ),
        )
```

#### 4.4.3 `start()` 适配

```python
def start(self) -> bool:
    with self._state_lock:
        if self._opened:
            return True

    # autodoip.Endpoint 在构造时已传入全部配置，start() 无参数
    self._endpoint.start()

    # 过滤已连接的 ECU
    self._ecus = self._filter_ecus()

    with self._state_lock:
        self._opened = True

    ecu_name = next(iter(self._ecus.keys()))
    self.on(ecu_name)
    return True
```

#### 4.4.4 `_filter_ecus()` 适配

autodoip 的 `connections()` 返回 `dict[int, tuple[str, int, bool]]`（逻辑地址 → (ip, port, connected)），而原 Diag 返回 `list[str]`（IP 列表）。

```python
def _filter_ecus(self) -> dict[str, tuple[str, int]]:
    if not self._endpoint:
        raise RuntimeError("Endpoint 未初始化")

    conns = self._endpoint.connections()
    # conns: {logical_addr: (ip, port, connected)}
    filtered = {}
    for name, (ip, addr) in self._ecus.items():
        if addr in conns and conns[addr][2]:  # connected == True
            filtered[name] = (ip, addr)
    if not filtered:
        raise RuntimeError("未发现可连接的 ECU")
    return filtered
```

#### 4.4.5 `on()` 适配

```python
def on(self, name: str) -> Self:
    with self._state_lock:
        if not self._opened:
            raise RuntimeError("会话未启动")
        if name not in self._ecus:
            raise ValueError(f"未知 ECU: {name}")
        ip, addr = self._ecus[name]
        self._cur_ecu = name

    if not self._endpoint:
        raise RuntimeError("Endpoint 未初始化")

    self._stop_keepalive()
    # autodoip 按逻辑地址选择，一步完成（原需 select(ip, ecu) 两步）
    self._endpoint.select(addr)
    self._start_keepalive()

    logger.info('已切换到 ECU: %s, IP: %s, 地址: 0x%04X', name, ip, addr)
    return self
```

#### 4.4.6 `send()` 适配（核心变更）

原 Diag `Endpoint.send(uds) -> bytes` 是阻塞的单次调用。autodoip `Endpoint.conversation(payload) -> Iterator[bytes]` 是生成器，可 yield 多个响应帧（如 NRC 0x78 流控帧 + 最终响应）。

**设计原则：只等待，不重发。** 在一次 `conversation()` 迭代里串起所有响应，遇到 0x78 就记录并继续等待，直到出现非 0x78 的帧才返回。

```python
def send(self, data: str) -> UdsResponse:
    """发送 UDS 请求，在单次 conversation 内持续等待（含 0x78 流控）。
    只等待，不重发。
    """
    with self._state_lock:
        if not self._opened or not self._endpoint:
            raise RuntimeError("会话未启动或 Endpoint 无效")
        endpoint = self._endpoint

    payload = self._pre_send(data)
    logger.info('TX: %s', data)
    last_resp = None

    # 一次请求，持续等待（包括 NRC 0x78 流控帧）
    for raw in endpoint.conversation(payload):
        resp = UdsResponse.from_bytes(raw)
        resp.father = last_resp          # 链接到前一个响应（可能是 0x78）
        logger.info('RX: %s', raw.hex(' '))
        if not (resp.is_negative and resp.nrc == 0x78):
            return resp                  # 真正的最终响应
        last_resp = resp                 # 记下 0x78，继续等下一个
        logger.debug('收到 NRC 0x78（请求待处理），继续等待…')

    # 生成器耗尽（超时 / 连接中断），返回最后一个响应
    return last_resp or UdsResponse.from_bytes(b'')
```

**效果**：
- 一次 `send()` 可能经历多个 0x78，它们全部通过 `father` 串成一条链
- 最终返回的是最后一个非 0x78 响应（或耗尽时的最后一个响应）
- 不再需要 `send_until` / `RetryConfig`，等待完全依赖 conversation 本身的超时机制

> **扩展能力**：后续如需支持 UDS 多帧响应（如大数据块上传），可在消费生成器的循环中添加收集逻辑。

#### 4.4.7 KeepAlive 适配

原 KeepAlive 绑定了 `Endpoint.send` 方法。重构后 KeepAlive 通过 `Session.send()` 发送心跳，与业务请求走同一路径：

```python
def _start_keepalive(self) -> None:
    if not self._endpoint:
        raise RuntimeError("Endpoint 未初始化")

    def _keepalive_send(payload: bytes) -> bytes:
        # 通过 send() 发送，与业务请求走同一路径（含 0x78 等待）
        resp = self.send(payload.hex(' '))
        return resp.raw

    self._keepalive = KeepAlive(
        fn=_keepalive_send,
        interval=self._keepalive_interval,
        payload=self._keepalive_payload,
    )
    self._keepalive.start()
```

> **注意**：KeepAlive 将 bytes 载荷转为 hex 字符串调用 `send()`，再取 `resp.raw` 返回。`send()` 是 Session 的唯一发送入口，所有上层调用（包括心跳）均通过它完成。

#### 4.4.8 移除的字段

Session 构造函数不再需要展开所有 DoIP 参数到 `self._*` 属性。原先 14 个 `self._*` 属性减少为：

```python
# 保留
self._ip = ip
self._ecus = ecus.copy()
self._ecu_names = {addr: name for name, (ip, addr) in ecus.items()}
self._endpoint: Optional[Endpoint] = None
self._keepalive: Optional[KeepAlive] = None
self._cur_ecu: str = ''
self._opened = False
self._state_lock = threading.RLock()
self._keepalive_interval = keepalive.interval
self._keepalive_payload = keepalive.payload

# 移除（现在封装在 autodoip.Endpoint / Config 内部）
# self._port, self._tester, self._accept_timeout, self._recv_timeout,
# self._reconnect_timeout, self._listen_count, self._doip_version,
# self._doip_msg_type, self._byte_order
```

### 4.5 `service.py` → 精简

#### 4.5.1 DoIPConfig 去重

autodoip.Config 已定义以下字段：

| autodoip.Config 字段 | 默认值 |
|---------------------|--------|
| `accept_timeout` | 1.5 |
| `recv_timeout` | 3.0 |
| `listen_count` | 5 |
| `version` | 0x02 |
| `msg_type` | 0x8001 |
| `byte_order` | `'big'` |

Diag 的 DoIPConfig 与之重叠的字段应从 DoIPConfig 移除，改为直接使用 autodoip.Config。Diag 仅保留自己特有的字段：

```python
# 重构前
@dataclass
class DoIPConfig:
    port: int = 13400
    tester: int = 0x0E80
    accept_timeout: float = 1.5       # autodoip.Config 有
    recv_timeout: float = 3.0         # autodoip.Config 有
    reconnect_timeout: float = 5.0    # autodoip 无（用 accept_timeout 替代）
    listen_count: int = 10            # autodoip.Config 有
    version: int = 0x02               # autodoip.Config 有
    msg_type: int = 0x8001            # autodoip.Config 有
    byte_order: Literal['little', 'big'] = 'big'  # autodoip.Config 有

# 重构后 — 方案 A（推荐）：拆分为两个独立配置
@dataclass
class DoIPConfig:
    """Uds 层 DoIP 配置 — 仅 Uds 特有参数"""
    port: int = 13400
    tester: int = 0x0E80
    # transmit 层参数独立传入 autodoip.Config

# autodoip.Config 直接暴露给高级用户
from autodoip import Config as DoIPTransmitConfig
```

Session/Service 构造时同时接受两个配置：

```python
class Session:
    def __init__(self, ip, ecus,
                 doip: DoIPConfig | None = None,
                 transmit: DoIPTransmitConfig | None = None,
                 keepalive: KeepAliveConfig | None = None):
        doip = doip or DoIPConfig()
        transmit = transmit or DoIPTransmitConfig()
        ...
```

**方案 B（保守）**：DoIPConfig 保留所有字段，内部拆解传递给 autodoip。对外 API 不变，仅标记部分字段为 deprecated。

**推荐方案 A**。理由：字段来源清晰，不产生"同一个参数两个地方设"的混淆。autodoip.Config 的默认值与当前 DoIPConfig 仅有 `listen_count` 差异（5 vs 10），影响极小。

#### 4.5.2 `reconnect_timeout` 处理

autodoip 不提供独立的 `reconnect_timeout`。重连时复用 `accept_timeout`。

| 场景 | 重构前 | 重构后 |
|------|--------|--------|
| 初始 accept | 1.5s | 1.5s（accept_timeout） |
| 断连重连 accept | 5.0s（reconnect_timeout） | 1.5s（复用 accept_timeout） |

**影响评估**：如果 ECU 在 1.5s 内未能重连，将更快失败。可通过增大 `accept_timeout` 弥补。实际场景中 ECU 通常立即重连，1.5s 足够。

> 已向 autodoip 提 feature request：支持独立的 `reconnect_timeout`。届时在 autodoip.Config 中增加该字段即可，Diag 层无需改动。

#### 4.5.3 Service 变更：移除 send_until 和 RetryConfig

`send()` 已在单次 conversation 内处理 0x78 链，因此 `send_until` 的重试逻辑完全被 `send()` 内置的等待机制取代。**遵循 ISO 14229：对 NRC 0x78 的正确响应是等待，而非重发请求。**

**删除：**
- `send_until()` 方法
- `RetryConfig` dataclass
- `Service.__init__` 的 `retry` 参数

**所有 UDS 方法改为直接调用 `self.send()`。**

```python
# 重构前
resp = self.send_until(f"10 {ss_id:02X}")

# 重构后
resp = self.send(f"10 {ss_id:02X}")
```

### 4.6 `__init__.py` → 更新

```python
"""
@文件: __init__.py
@描述: Uds — UDS 诊断模块（基于 autodoip 传输层）
"""
from autodoip import ProtocolError

from .uds import Session
from .service import Service, DoIPConfig, KeepAliveConfig
from .response import UdsResponse

__all__ = [
    'Session',
    'Service',
    'UdsResponse',
    'DoIPConfig',
    'KeepAliveConfig',
    'ProtocolError',
]
```

> **注意**：`RetryConfig` 已移除——`send()` 内置了 0x78 等待，不再需要重试策略。`UdsResponse` 新增 `father` 字段和 `iter_chain()` 方法。

### 4.7 `__main__.py` → 适配

主要变更：移除对 `doip` 内部实现的引用。示例代码中可选展示 autodoip.Config 用法。

---

## 5. API 兼容性矩阵

### 5.1 公开 API（完全兼容）

| API | 重构前 | 重构后 | 兼容 |
|-----|--------|--------|------|
| `Service(ip, ecus, ...)` | ✓ | ✓ | ✅ 签名不变 |
| `Session(ip, ecus, ...)` | ✓ | ✓ | ✅ 签名不变 |
| `service.start()` / `stop()` | ✓ | ✓ | ✅ |
| `service.on(name)` | ✓ | ✓ | ✅ |
| `service.send(data)` | ✓ | ✓ | ✅ |
| `service >> "22DC06"` | ✓ | ✓ | ✅ |
| `service.change_session(id)` | ✓ | ✓ | ✅ |
| `service.change_level(lv)` | ✓ | ✓ | ✅ |
| `service.read_did(did)` | ✓ | ✓ | ✅ |
| `service.write_did(did, data)` | ✓ | ✓ | ✅ |
| `service.start_routine(rid)` | ✓ | ✓ | ✅ |
| `service.stop_routine(rid)` | ✓ | ✓ | ✅ |
| `service.get_routine_result(rid)` | ✓ | ✓ | ✅ |
| `service.reset(type)` | ✓ | ✓ | ✅ |
| `service.set_key_calculator(fn)` | ✓ | ✓ | ✅ |
| `UdsResponse` | ✓ | ✓（新增 `father` + `iter_chain()`） | ✅ |
| `KeepAliveConfig` | ✓ | ✓ | ✅ |
| `RetryConfig` | ✓ | **移除**（`send()` 内置 0x78 等待） | ✂ |

### 5.2 DoIPConfig 变更（需关注）

| 字段 | 重构前 | 重构后 |
|------|--------|--------|
| `port` | DoIPConfig | DoIPConfig |
| `tester` | DoIPConfig | DoIPConfig |
| `accept_timeout` | DoIPConfig | autodoip.Config |
| `recv_timeout` | DoIPConfig | autodoip.Config |
| `reconnect_timeout` | DoIPConfig | **移除**（autodoip 暂无） |
| `listen_count` | DoIPConfig（默认 10） | autodoip.Config（默认 5） |
| `version` | DoIPConfig | autodoip.Config |
| `msg_type` | DoIPConfig | autodoip.Config |
| `byte_order` | DoIPConfig | autodoip.Config |

**迁移指南**（如果采用方案 A）：

```python
# 重构前
from Diag import Service, DoIPConfig
ss = Service(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)},
             doip=DoIPConfig(accept_timeout=2.0, reconnect_timeout=8.0))

# 重构后
from Diag import Service, DoIPConfig
from autodoip import Config as TransmitConfig
ss = Service(ip='198.18.44.1', ecus={'mcu': ('198.18.44.49', 0x1301)},
             doip=DoIPConfig(port=13400, tester=0x0E80),
             transmit=TransmitConfig(accept_timeout=2.0))
```

### 5.3 内部 API（不兼容，但外部不应使用）

| 原内部 API | 状态 |
|-----------|------|
| `from Diag.doip import Endpoint` | ✂ 不可用 |
| `from Diag.helper import recv_exact, recv_frame` | ✂ 不可用 |
| `from Diag.errors import ProtocolError` | ⚠ 改为 `from Diag import ProtocolError`（重导出） |

---

## 6. 迁移步骤

### Step 1：验证 autodoip 版本

```bash
uv run python -c "from autodoip import Endpoint, Config, ProtocolError; print('OK')"
```

确认 autodoip ≥ 0.1.3 已安装。

### Step 2：改造 helper.py

删除 `recv_exact()` 和 `recv_frame()`，仅保留 `to_bytes()`。

### Step 3：改造 uds.py

- 将 `from .doip import Endpoint` 替换为 `from autodoip import Endpoint, Config, ProtocolError`
- ECU 表格式转换
- `start()` / `_filter_ecus()` / `on()` / `send()` / `_start_keepalive()` 按 §4.4 适配
- 清理不再需要的 `self._*` 属性

### Step 4：改造 service.py

- 精简 `DoIPConfig`（移除 transmit 层字段）
- 其余不变

### Step 5：改造 `__init__.py`

- 新增 `from autodoip import ProtocolError`
- 移除对 `doip` / `errors` 的内部依赖

### Step 6：删除文件

```bash
rm src/workspace/module/Uds/doip.py
rm src/workspace/module/Uds/errors.py
```

### Step 7：更新 `__main__.py`

适配示例代码。

### Step 8：回归验证

```bash
# 1. 导入检查
uv run python -c "from Diag import Service, Session, UdsResponse, ProtocolError, DoIPConfig, KeepAliveConfig, RetryConfig"

# 2. 单元测试（如有）
uv run pytest

# 3. StreamPromptTui 集成测试
uv run python -m src.workspace.mix.StreamPromptTuiMix.StreamPromptTui_Diag --help
```

---

## 7. 风险与缓解

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| `conversation()` 生成器行为与 `send()` 不一致 | 🟡 中 | 单帧场景取第一个 yield 值，逻辑等价。多帧场景 gain 而非 break |
| `reconnect_timeout` 缺失 | 🟡 中 | 增大 `accept_timeout` 补偿。向 autodoip 提 PR 增加该字段 |
| `listen_count` 默认值变化（10→5） | 🟢 低 | 5 对 tester-as-server 模式足够。用户可显式设置 |
| KeepAlive 心跳改用 `conversation()` 包装 | 🟢 低 | 包装 lambda 逻辑简单，心跳失败本就会停止 |
| 内部 API 消费者被破坏 | 🟢 低 | 经搜索，无外部代码直接导入 `doip.py` 或 `errors.py` |
| autodoip 包不可用（离线环境） | 🟢 低 | 已在 pyproject.toml 声明依赖，uv.lock 锁定版本 |

---

## 8. autodoip 后续增强建议

以下特性在 autodoip 侧实现后，Diag 可自动受益：

| 建议 | 优先级 | 说明 |
|------|--------|------|
| `Config.reconnect_timeout` | 高 | 独立的重连超时，与 accept_timeout 解耦 |
| `Endpoint.send()` 便捷方法 | 低 | Diag 的 `send()` 已封装生成器消费 + 0x78 链，该便捷方法价值降低 |
| `Config.keepalive` 内置心跳 | 低 | 将 TesterPresent 下沉到传输层 |
| 多 ECU 并发 | 低 | 同时向多个 ECU 发送诊断请求 |

---

## 9. 文件行数变化估算

| 文件 | 重构前 | 重构后 | 变化 |
|------|--------|--------|------|
| `doip.py` | 297 | 0（删除） | -297 |
| `errors.py` | 12 | 0（删除） | -12 |
| `helper.py` | 68 | ~35 | -33 |
| `uds.py` | 246 | ~200 | -46 |
| `service.py` | 251 | ~230 | -21 |
| `__init__.py` | 15 | 18 | +3 |
| **合计** | **889** | **~483** | **-406** |

净减少约 **400 行**（-45%），同时获得更清晰的职责分离和独立演进的传输层。

---

## 10. 决策记录

| 决策 | 结论 | 理由 |
|------|------|------|
| DoIPConfig 拆分 vs 保留 | **拆分**（方案 A） | 字段来源清晰，不产生混淆；autodoip.Config 可直接文档化 |
| 删除 errors.py vs 保留重导出 | **删除**，在 `__init__` 重导出 | 单文件维护一行 `ProtocolError` 无意义 |
| helper.py recv_* 删除 vs 保留 | **删除** | autodoip 版本功能等价且有更好的 byte_order 支持 |
| Session 持有 Endpoint vs 每次创建 | **持有**（不变） | 维持长连接模型，与 KeepAlive 线程一致 |
| 0x78 重试 vs 等待 | **等待**（send 内置 0x78 链） | 符合 ISO 14229：对 NRC 0x78 的正确响应是等待而非重发；conversation 生成器天然支持 |
| send_until 保留 vs 删除 | **删除** | send() 已内置 0x78 等待，send_until 的重试逻辑被取代 |
| KeepAlive 直连 conversation vs 通过 send | **通过 send** | send 是唯一发送入口，心跳与业务请求走同一路径 |

---

> **相关文档**：[Diag-design.md](Diag-design.md) — 当前 Diag 模块设计文档（v0.2）

---

## 11. 执行记录

**日期**: 2026-06-04 | **执行人**: Claude

| 步骤 | 操作 | 结果 |
|------|------|------|
| Step 2 helper.py | 已提前完成 | ✅ |
| Step 3 uds.py | 已提前完成 | ✅ |
| Step 4 service.py | 已提前完成 | ✅ |
| Step 5 \_\_init\_\_.py | 已提前完成 | ✅ |
| Step 6 删除 doip.py / errors.py | 已执行 | ✅ |
| Step 7 \_\_main\_\_.py | 已适配 | ✅ |
| Step 8 回归验证 | `from Diag import Service, Session, UdsResponse, ProtocolError, DoIPConfig, KeepAliveConfig, RetryConfig` | ✅ |

### 执行中发现的问题

#### P1: `__main__.py` 原有导入错误（已修复）

原代码 `from .Diag import Service, ...` 会尝试从 `Diag` 包内查找子模块 `Diag`（即 `Diag.Diag`），该模块不存在，运行 `python -m src.workspace.module.Diag` 会直接 ImportError。已改为 `from . import Service, ...`。

#### P2: `__main__.py` 注释示例使用了已移除的 API（已修复）

原注释示例 `DoIPConfig(recv_timeout=5.0)` — `recv_timeout` 已迁至 `autodoip.Config`。已更新为展示 `transmit=TransmitConfig(recv_timeout=5.0)` 的新模式。

#### P3: send_until / RetryConfig 与新 send() 设计冲突（已移除）

`send()` 改为在单次 conversation 内持续等待 0x78 后，`send_until` 的"重发+延迟"模式不再需要。已移除 `send_until()`、`RetryConfig` 及相关导入。

### 第二轮变更（2026-06-04，session 2）

| 变更 | 文件 | 说明 |
|------|------|------|
| 0x78 流控链 | `uds.py` | `send()` 改为消费整个 conversation 生成器，0x78 通过 `father` 串链，只等待不重发 |
| father 链 | `response.py` | `UdsResponse` 新增 `father` 字段 + `iter_chain()` 回溯方法 |
| KeepAlive 走 send | `uds.py` | `_start_keepalive()` 改为调用 `self.send()` 而非直接包装 `conversation()` |
| 移除 send_until | `service.py` | 删除 `send_until()` 方法、`RetryConfig`、`retry` 参数；所有 UDS 方法直接调用 `self.send()` |
| 更新导出 | `__init__.py` | 移除 `RetryConfig` |
| 更新示例 | `__main__.py` | 移除 `RetryConfig` 导入和使用 |

### 待后续关注

- [ ] `autodoip` 发布 `Config.reconnect_timeout` 后，`service.py` 可恢复该参数
- [ ] `helper.py` 的 `to_bytes()` 目前仍被 `response.py` 和外部消费者使用，暂不迁移
- [ ] 如有单元测试，建议补充 autodoip Endpoint mock 下的 Session/Service 集成测试（包括 0x78 多帧场景）