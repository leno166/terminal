"""
@文件: response.py
@作者: 雷小鸥
@日期: 2026/6/2 14:10
@许可: MIT License
@描述:
@版本: Version 0.1
"""
from dataclasses import dataclass, field
from typing import Self
from .helper import to_bytes


@dataclass
class UdsResponse:
    raw: bytes

    # 响应类型标识
    ok: bool = False
    is_negative: bool = False

    # 正响应通用字段
    sid: int | None = None  # 正响应 SID = 请求 SID + 0x40
    head: bytes | None = None
    body: bytes | None = None

    # 负响应专用字段
    request_sid: int | None = None
    nrc: int | None = None
    nrc_desc: str | None = None

    # 辅助属性：十六进制字符串表示（便于打印）
    hex_str: str = field(default="", init=False, repr=False)

    # NRC 描述映射（中文）
    _NRC_DESC: dict[int, str] = field(default_factory=lambda: {
        0x10: "常规拒绝",
        0x11: "服务不支持",
        0x12: "子功能不支持",
        0x13: "报文长度错误或格式无效",
        0x14: "响应过长",
        0x21: "忙，请重复请求",
        0x22: "条件不满足",
        0x24: "请求序列错误",
        0x25: "子网组件无响应",
        0x26: "故障阻止执行请求的操作",
        0x31: "请求超出范围",
        0x33: "安全访问被拒绝",
        0x35: "密钥无效",
        0x36: "超过尝试次数",
        0x37: "所需延时未到",
        0x70: "上传下载不被接受",
        0x71: "数据传输暂停",
        0x72: "一般编程错误",
        0x73: "错误的块序列计数",
        0x78: "请求正确接收，响应待定",
        0x7E: "当前会话不支持子功能",
        0x7F: "当前会话不支持该服务",
    }, init=False, repr=False)

    # 正响应 head 长度映射（字节数）
    _POSITIVE_HEAD_LEN: dict[int, int] = field(default_factory=lambda: {
        # ==========================
        # 诊断与通信管理
        # ==========================
        0x50: 1,  # 10 诊断会话控制：会话类型（1字节）
        0x51: 1,  # 11 ECU 复位：复位类型（1字节）
        0x68: 1,  # 28 通信控制：子功能（1字节）
        0x7E: 1,  # 3E 保持激活状态：子功能（1字节）
        0x54: 0,  # 14 清除诊断信息：无额外数据

        # ==========================
        # 安全访问
        # ==========================
        0x67: 1,  # 27 安全访问：子功能（1字节）

        # ==========================
        # 数据传输
        # ==========================
        0x62: 2,  # 22 通过标识符读取数据：DID（2字节）
        0x6E: 2,  # 2E 通过标识符写入数据：DID（2字节）
        0x63: 0,  # 23 通过地址读取内存：无固定头，负载为读取的数据
        0x7D: 0,  # 3D 通过地址写入内存：无固定头，通常无数据

        # ==========================
        # DTC（故障码）
        # ==========================
        0x59: 1,  # 19 读取 DTC 信息：子功能（1字节）
        0x85: 1,  # 85 控制 DTC 设置：子功能（1字节）

        # ==========================
        # 例程控制
        # ==========================
        0x71: 3,  # 31 例程控制：例程控制类型（1字节）+ 例程 ID（2字节）

        # ==========================
        # 下载 / 上传
        # ==========================
        0x74: 1,  # 34 请求下载：长度格式标识符（1字节）
        0x75: 1,  # 35 请求上传：长度格式标识符（1字节）
        0x76: 1,  # 36 传输数据：块序列计数器（1字节）
        0x77: 0,  # 37 请求退出传输：无数据

        # ==========================
        # 动态 DID
        # ==========================
        0x6C: 1,  # 2C 动态定义数据标识符：子功能（1字节）

        # ==========================
        # IO 控制
        # ==========================
        0x6F: 2,  # 2F 通过标识符输入输出控制：DID（2字节）

        # ==========================
        # 认证（ISO14229-1:2020）
        # ==========================
        0x69: 1,  # 29 认证：子功能（1字节）

        # ==========================
        # 补充常见服务
        # ==========================
        0x83: 1,  # 83 访问时序参数：子功能（1字节）
        0x87: 1,  # 87 链路控制：子功能（1字节）
        # 可根据需要继续添加其他服务的 head 长度
    }, init=False, repr=False)

    def _get_nrc_desc(self, nrc: int) -> str:
        return self._NRC_DESC.get(nrc, f"保留或未知 NRC (0x{nrc:02X})")

    def _parse_negative(self, data: bytes) -> None:
        self.is_negative = True
        self.ok = False
        if len(data) >= 3:
            self.request_sid = data[1]
            self.nrc = data[2]
            self.nrc_desc = self._get_nrc_desc(self.nrc)
        else:
            raise ValueError('负响应帧长度不足 3 字节')

    def _parse_positive(self, data: bytes) -> None:
        self.ok = True
        self.is_negative = False
        self.sid = data[0]
        payload = data[1:] if len(data) > 1 else b''

        head_len = self._POSITIVE_HEAD_LEN.get(self.sid)
        if not head_len or len(payload) < head_len:
            self.head = payload
            return

        self.head = payload[:head_len]
        self.body = payload[head_len:] if len(payload) > head_len else None

    def check_fail(
            self, sid: int | str | bytes | bytearray | None = None,
            head: int | str | bytes | bytearray | None = None,
            body: int | str | bytes | bytearray | None = None,
    ):
        if self.is_negative:
            return True

        if sid and to_bytes(self.sid) != to_bytes(sid):
            return True

        if head and to_bytes(self.head) != to_bytes(head):
            return True

        if body and to_bytes(self.body) != to_bytes(body):
            return True

        return False

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        """
        类方法：解析原始 UDS 响应字节，创建并返回填充好的 UdsResponse 实例。
        外部调用示例：resp = UdsResponse.from_bytes(b'\x62\x12\x34\xAB')
        """
        # 创建实例，保存原始字节
        instance = cls(raw=data)

        # 生成十六进制字符串并保存
        instance.hex_str = ' '.join(f'{b:02X}' for b in data)

        if not data:
            return instance

        # 负响应处理
        if data[0] == 0x7F:
            instance._parse_negative(data)
        else:
            instance._parse_positive(data)

        return instance

