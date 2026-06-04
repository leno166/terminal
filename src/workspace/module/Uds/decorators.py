"""
@文件: decorators.py
@描述: UDS 装饰器 — @read / @write / @workflow
      Generator-Coroutine 模式，类似 pytest fixture：
      yield 之前准备请求，yield 之后处理响应。

      支持 @decorator 和 @decorator() 两种写法。
"""
import functools
import inspect
from logging import getLogger

logger = getLogger(__name__)


def _is_gen(obj):
    """判断对象是否为生成器（generator function 的调用结果）"""
    return inspect.isgenerator(obj)


# ================== @read ==================

def read(func=None, *, did=None, session=None, level=None):
    """@read(did=0xF190) — UDS 0x22 ReadDataByIdentifier

    Generator 模式::

        @read(did=0xF190)
        def vin(self):
            payload = yield          # 框架发送 22 F1 90
            return payload.decode()  # payload = resp.body

    框架流程::

        gen = vin(self)
        next(gen)                    # 跑到 yield
        resp = self.send('22 F1 90')
        gen.send(resp.body)         # 把 body 喂回 → payload
        StopIteration → e.value     # return 的值
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(app_self, *args, **kwargs):
            service = app_self._service

            # ① 环境准备
            if session is not None and service.session != session:
                service.change_session(session)
            if level is not None and service.level < level:
                service.change_level(level)

            # ② 执行方法
            result = f(app_self, *args, **kwargs)

            # ③ 非生成器 → 立即返回（简单模式）
            if not _is_gen(result):
                return result

            gen = result

            # ④ 跑到 yield
            try:
                next(gen)
            except StopIteration as e:
                return e.value

            # ⑤ 发送 UDS 请求
            hex_str = f'22 {did:04X}'
            logger.info('@read TX: %s', hex_str)
            resp = service.send(hex_str)

            # ⑥ 把响应 body 喂回生成器
            try:
                gen.send(resp.body)
            except StopIteration as e:
                return e.value

            # 生成器未耗尽 → 继续驱动（罕见）
            try:
                next(gen)
            except StopIteration as e:
                return e.value

        return wrapper

    if func is not None:
        return decorator(func)  # @read — 不带括号
    return decorator            # @read(did=...) — 带括号


# ================== @write ==================

def write(func=None, *, did=None, session=None, level=None):
    """@write(did=0x1111) — UDS 0x2E WriteDataByIdentifier

    Generator 模式::

        @write(did=0x1111)
        def serial(self, value: str):
            payload = value.encode()     # yield 之前：准备载荷
            resp = yield payload         # 框架发送 2E 11 11 {payload}
            return resp.ok               # resp 是完整的 UdsResponse

    框架流程::

        gen = serial(self, 'ABC')
        payload = next(gen)              # 拿到 yield 的值
        resp = self.send(f'2E 11 11 {payload.hex()}')
        gen.send(resp)                   # 把 UdsResponse 喂回 → resp
        StopIteration → e.value
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(app_self, *args, **kwargs):
            service = app_self._service

            # ① 环境准备
            if session is not None and service.session != session:
                service.change_session(session)
            if level is not None and service.level < level:
                service.change_level(level)

            # ② 执行方法
            result = f(app_self, *args, **kwargs)

            # ③ 非生成器 → 立即返回
            if not _is_gen(result):
                return result

            gen = result
            try:
                payload = next(gen)
            except StopIteration as e:
                return e.value

            # ④ 编码 + 发送
            if payload is None:
                hex_str = f'2E {did:04X}'
            elif isinstance(payload, bytes):
                hex_str = f'2E {did:04X} {payload.hex()}'
            else:
                hex_str = f'2E {did:04X} {payload}'

            logger.info('@write TX: %s', hex_str)
            resp = service.send(hex_str)

            # ⑤ 把 UdsResponse 喂回生成器
            try:
                gen.send(resp)
            except StopIteration as e:
                return e.value

        return wrapper

    if func is not None:
        return decorator(func)  # @write — 不带括号
    return decorator            # @write(did=...) — 带括号


# ================== @workflow ==================

def workflow(func=None, *, session=None, level=None):
    """@workflow — 多步 UDS 流程。每次 yield 一次请求，收到响应后继续。

    支持 @workflow 和 @workflow(session=0x02) 两种写法。

    Generator 模式::

        @workflow
        def flash(self):
            resp = yield '10 02'      # 切换编程会话
            if not resp.ok:
                return False

            resp = yield '27 01'      # 请求种子
            if not resp.ok:
                return False

            resp = yield '31 01 FF00' # 启动例程
            return resp.ok

    框架流程::

        gen = flash(self)
        req = next(gen)               # → '10 02'
        while True:
            resp = send(req)
            try:
                req = gen.send(resp)  # 拿到下一个 yield
            except StopIteration as e:
                return e.value         # return 的值
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(app_self, *args, **kwargs):
            service = app_self._service

            # ① 环境准备
            if session is not None and service.session != session:
                service.change_session(session)
            if level is not None and service.level < level:
                service.change_level(level)

            # ② 执行方法
            result = f(app_self, *args, **kwargs)

            # ③ 非生成器 → 立即返回
            if not _is_gen(result):
                return result

            gen = result

            # ④ 拿第一个请求
            try:
                req = next(gen)
            except StopIteration as e:
                return e.value

            # ⑤ 循环：send → feed response → get next request
            while True:
                if req is None:
                    resp = service.send('')
                elif isinstance(req, bytes):
                    resp = service.send(req.hex(' '))
                elif isinstance(req, str):
                    resp = service.send(req)
                else:
                    resp = service.send(str(req))

                logger.info('@workflow step: %s → %s', req,
                            'OK' if resp.ok else f'NRC 0x{resp.nrc:02X}')

                try:
                    req = gen.send(resp)
                except StopIteration as e:
                    return e.value

        return wrapper

    if func is not None:
        return decorator(func)  # @workflow — 不带括号
    return decorator            # @workflow(...) — 带括号
