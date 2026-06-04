"""
@文件: __main__.py
@描述: UdsApp 声明式 API 使用演示
@版本: Version 0.4
"""
from . import UdsApp, read, write, workflow
from . import UdsResponse

# ================== 使用演示 ==================
if __name__ == '__main__':
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(funcName)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # ============================================================
    # OEM 层：定义 UDS 应用类
    # ============================================================
    class MyApp(UdsApp):

        # ── @read：读取 DID ──────────────────────────
        @read(did=0xF190)
        def vin(self):
            """bare yield → 框架发送 22 F1 90 → body 返回"""
            payload = yield
            return payload.decode('ascii').strip('\x00')

        @read(did=0xF120, session=0x03)
        def speed(self):
            """需要扩展会话才能读的车速"""
            payload = yield
            return int.from_bytes(payload, 'big')

        # ── @write：写入 DID ──────────────────────────
        @write(did=0xF190, session=0x03, level=0x05)
        def write_vin(self, vin: str):
            """yield 前编码 → 框架发送 2E F1 90 → resp 返回"""
            payload = vin.encode('ascii')
            resp = yield payload
            return resp.ok

        # ── @workflow：多步流程 ──────────────────────
        @workflow(session=0x02, level=0x05)
        def flash(self, routine_id: int = 0xFF00):
            """yield 多次 → 每步 send → 收到 resp → 继续"""
            # Step 1: 切换编程会话
            resp = yield '10 02'
            if not resp.ok:
                return False

            # Step 2: 安全访问
            resp = yield '27 01'
            if not resp.ok:
                return False

            # Step 3: 启动例程
            resp = yield f'31 01 {routine_id:04X}'
            return resp.ok

    # ============================================================
    # 业务层：调用
    # ============================================================
    with MyApp(ip='198.18.44.1', ecus={'mcu': (0x1301, '198.18.44.49')}) as app:

        # @read — bare yield，拿到解码后的值
        vin = app.vin()
        print(f"VIN: {vin}")

        speed = app.speed()
        print(f"车速: {speed} km/h")

        # @write — yield 发送，拿到 resp 判断成功
        ok = app.write_vin('LSVAU2A38M2100999')
        print(f"写入 VIN: {'成功' if ok else '失败'}")

        # @workflow — 多步流程
        ok = app.flash(routine_id=0xFF01)
        print(f"刷写流程: {'成功' if ok else '失败'}")

    # ============================================================
    # 不使用 yield 的简单函数也支持
    # ============================================================
    class SimpleApp(UdsApp):

        @read(did=0xF190)
        def vin(self):
            """无 yield → StopIteration 直接捕获返回值"""
            return 'dummy'  # 不发送任何 UDS 请求

    with SimpleApp(ip='198.18.44.1', ecus={'mcu': (0x1301, '198.18.44.49')}) as app:
        print(app.vin())  # → 'dummy'
