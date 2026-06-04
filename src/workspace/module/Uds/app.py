"""
@文件: app.py
@描述: UdsApp — 声明式 UDS 应用基类
      内部持有 Service，提供连接生命周期。
      业务逻辑由 @read / @write / @workflow 装饰器定义在子类中。
"""
from typing import Optional
from autodoip import Config


class UdsApp:
    """声明式 UDS 应用基类。

    Usage:
        class MyApp(UdsApp):
            @read(did=0xF190)
            def vin(self):
                payload = yield
                return payload.decode()

        with MyApp(ip='...', ecus={'mcu': (0x1301, '198.18.44.49')}) as app:
            print(app.vin())
    """

    def __init__(self,
                 ip: str,
                 ecus: dict,
                 port: int = 13400,
                 tester: int = 0x0E80,
                 transmit: Optional[Config] = None,
                 keepalive=None):
        from .service import Service, KeepAliveConfig

        keepalive = keepalive if keepalive is not None else KeepAliveConfig()
        self._service = Service(
            ip=ip, ecus=ecus, port=port, tester=tester,
            transmit=transmit, keepalive=keepalive,
        )

    # ── 注入 ─────────────────────────────────

    def set_key_calculator(self, fn):
        """注入 Key 计算回调: fn(level, seed) -> key_bytes"""
        self._service.set_key_calculator(fn)

    # ── 只读属性 ─────────────────────────────

    @property
    def session(self) -> int:
        return self._service.session

    @property
    def level(self) -> int:
        return self._service.level

    @property
    def ecus(self):
        return self._service.ecus

    # ── 生命周期 ─────────────────────────────

    def start(self):
        self._service.start()

    def stop(self):
        self._service.stop()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
