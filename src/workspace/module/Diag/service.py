"""
@文件: service.py
@作者: 雷小鸥
@日期: 2026/6/2 14:44
@许可: MIT License
@描述:
@版本: Version 0.1
"""
import time

from .uds import Session
from .response import UdsResponse
from .helper import to_bytes, get_pin_code, calculate_key
from typing import Literal, Tuple, Optional


class Service(Session):
    def __init__(self, ip: Optional[str] = None,
                 ecus: Optional[dict[str, tuple[str, int]]] = None,
                 platform: Optional[str] = None,
                 serial_version: float = 2.0, soc_num: int = 1,
                 port: int = 13400, tester: int = 0x0E80,
                 timeout: float = 1.5, listen_count: int = 10,
                 doip_version: int = 0x02, doip_msg_type: int = 0x8001,
                 byte_order: Literal['little', 'big'] = 'big',
                 keepalive_interval: float = 1.5,
                 keepalive_payload: bytes = b'\x3E\x00'):
        # --- 从配置文件加载默认值 ---
        try:
            from .config.loader import get_defaults, get_ecus as _load_ecus
        except ImportError:
            from config.loader import get_defaults, get_ecus as _load_ecus

        cfg = get_defaults()
        ip = ip or cfg.get('ip')
        ecus = ecus or _load_ecus()
        platform = platform or cfg.get('platform')

        if not ip:
            raise ValueError("ip 未提供且配置文件中未设置 defaults.ip")
        if not ecus:
            raise ValueError("ecus 未提供且配置文件中未设置 ecus 段")
        if not platform:
            raise ValueError("platform 未提供且配置文件中未设置 defaults.platform")

        super().__init__(
            ip, ecus, port, tester, timeout, listen_count,
            doip_version, doip_msg_type, byte_order,
            keepalive_interval, keepalive_payload
        )

        self._platform = platform
        self._serial_version = serial_version or cfg.get('serial_version', 2.0)
        self._soc_num = soc_num

        self.session = 0x1
        self.level: Literal['L1', 'L5', 'L19'] = 'L1'
        self._level_table: dict[str, int] = {
            'L1' : 0x01,
            'L5' : 0x05,
            'L19': 0x19,
        }

        # 用于记录最近启动的例程 ID（供 get_routine_result 使用）
        self._current_routine_id: int | None = None

    def send_until(self, data: str, count: int = 3, retry_delay: float = 0.5) -> UdsResponse:
        for attempt in range(count):
            resp = self.send(data)

            if resp.is_negative and resp.nrc == 0x78:
                time.sleep(retry_delay)
                continue

            return resp

        raise RuntimeError("请求重复失败，已达到最大重试次数")

    def change_session(self, ss_id: int) -> Tuple[bool, UdsResponse]:
        """
        切换诊断会话（UDS 服务 0x10）
        :param ss_id: 会话类型，如 1=默认会话，2=编程会话，3=扩展会话等
        :return: 成功返回 True，失败返回 False
        """
        resp = self.send_until(f"10 {ss_id:02X}")

        if resp.check_fail(0x50, ss_id):
            return False, resp

        self.session = ss_id
        return True, resp

    def change_level(self, level: Literal['L1', 'L5', 'L19']) -> Tuple[bool, UdsResponse]:
        level_int = self._level_table.get(level, 0)
        if not level_int:
            raise ValueError(f'输入的等级错误')
        resp = self.send_until(f'27 {level_int:02X}')
        if resp.check_fail(0x67, level_int):
            return False, resp

        seed = to_bytes(resp.body)

        pin_code = get_pin_code(level=level_int, platform=self._platform, serial_version=self._serial_version)

        key = calculate_key(level=level_int, seed=seed, pin_code=pin_code)
        resp = self.send_until(f'27 {level_int + 1:02X} {key.hex()}')
        if resp.check_fail(0x67, level_int + 1):
            return False, resp

        self.level = level
        return True, resp

    def change_any(
            self, ss_id: int | None = None, level: Literal['L1', 'L5', 'L19'] | None = None,
    ) -> bool:
        if ss_id:
            step_success, _ = self.change_session(ss_id)
            if not step_success:
                return False
        if level:
            step_success, _ = self.change_level(level=level)
            if not step_success:
                return False
        return True

    def reset(self, reset_type: int = 0x01):
        """
        ECU 复位（UDS 服务 0x11）
        :param reset_type: 复位类型，0x01=硬复位，0x02=钥匙关/开复位，0x03=软复位等
        :return: 成功返回 True，失败返回 False
        """
        resp = self.send_until(f"11 {reset_type:02X}")
        if resp.check_fail(sid=0x51, head=reset_type):
            return False
        return True

    def read_data_by_identifier(self, did: int, ss_id: int | None = None, level: Literal['L1', 'L5', 'L19'] | None = None) -> UdsResponse:
        self.change_any(ss_id, level)

        return self.send_until(f'22 {did:04X}')

    def read_did(self, did: int, ss_id: int | None = None, level: Literal['L1', 'L5', 'L19'] | None = None) -> UdsResponse:
        return self.read_data_by_identifier(did, ss_id, level)

    def write_data_by_identifier(self, did: int, data: bytes, ss_id: int | None = None, level: Literal['L1', 'L5', 'L19'] | None = None) -> UdsResponse:
        self.change_any(ss_id, level)

        return self.send_until(f'2E {did:04X} {data.hex()}')

    def write_did(self, did: int, data: bytes, ss_id: int | None = None, level: Literal['L1', 'L5', 'L19'] | None = None) -> UdsResponse:
        return self.write_data_by_identifier(did, data, ss_id, level)

    def start_routine(self, routine_id: int, data: bytes | None = None, ss_id: int | None = None, level: Literal['L1', 'L5', 'L19'] | None = None) -> UdsResponse:
        """
        启动例程（UDS 服务 0x31，子功能 0x01）
        :param routine_id: 例程标识符（2 字节）
        :param data:    可选的例程输入参数
        :param ss_id:   可选，临时切换到的会话 ID
        :param level:   可选，临时切换到的安全等级
        :return:        响应对象（肯定响应为 0x71 + 子功能 0x01 + 例程 ID + 可选输出参数）
        """
        self.change_any(ss_id, level)

        parts = [f"31 01 {routine_id:04X}"]
        if data:
            parts.append(data.hex())
        req_str = ' '.join(parts)
        resp = self.send_until(req_str)

        # 记录当前例程 ID，供 get_routine_result 使用（仅当子功能为 0x01 启动例程时记录）
        if not resp.is_negative and resp.head and len(resp.head) >= 1 and resp.head[0] == 0x01:
            self._current_routine_id = routine_id
        return resp

    def stop_routine(self, routine_id: int) -> bool:
        """
        停止例程（UDS 服务 0x31，子功能 0x02）
        :param routine_id: 例程标识符（2 字节）
        :return: 停止成功返回 True，失败返回 False
        """
        req_str = f"31 02 {routine_id:04X}"
        resp = self.send_until(req_str)

        # 肯定响应应为 0x71, 子功能 0x02, 例程 ID
        if resp.check_fail(sid=0x71, head=0x02):
            return False

        # 可选：校验响应中的例程 ID 是否一致
        if resp.head and len(resp.head) >= 2:
            returned_rid = int.from_bytes(resp.head[1:3], self._byte_order)
            if returned_rid != routine_id:
                return False

        return True

    def get_routine_result(self, routine_id: int | None = None) -> UdsResponse:
        """
        获取例程结果（UDS 服务 0x31，子功能 0x03）
        :param routine_id: 例程标识符（2 字节）。若不提供，则使用最近 start_routine 中记录的 ID
        :return: 响应对象（肯定响应中包含例程输出参数）
        """
        rid = routine_id if routine_id is not None else self._current_routine_id
        if rid is None:
            raise ValueError("未提供例程 ID 且没有最近启动的例程记录")

        req_str = f"31 03 {rid:04X}"
        return self.send_until(req_str)

    def unlock_ssh(self) -> bool:
        target = '40' if self._soc_num == 1 else '50'

        resp = self.read_did(0xDC06)
        if resp.check_fail(0x62, 0xDC06):
            return False

        status = to_bytes(resp.body)[-2:]
        if to_bytes(status) != to_bytes(target):
            return False

        resp = self.write_did(0xDC06, to_bytes(target))
        if resp.check_fail(0x6E, 0xDC06):
            return False

        return True