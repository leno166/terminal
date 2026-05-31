"""
@文件: event_bus.py
@作者: 雷小鸥
@日期: 2026/5/30
@许可: MIT License
@描述: EventBus — 基于 Blinker 的应用层广播事件总线，替代 Textual 消息冒泡机制。
@版本: Version 0.1
"""
from logging import getLogger
from typing import Callable

from blinker import Namespace

logger = getLogger(__name__)

_namespace = Namespace()


def emit(msg) -> None:
    """广播一条消息。信号名取 msg 的类名，支持任意数量的订阅者同时接收。"""
    signal_name = type(msg).__name__
    logger.debug("EventBus.emit: 信号=%s msg=%s", signal_name, msg)
    _namespace.signal(signal_name).send(msg)


def on(msg_cls: type, receiver: Callable) -> None:
    """订阅某个消息类型。用法: bus.on(SubmitMsg, self.on_submit_msg)"""
    signal_name = msg_cls.__name__
    logger.debug("EventBus.on: 信号=%s 订阅者=%s", signal_name, receiver.__qualname__)
    _namespace.signal(signal_name).connect(receiver)


def off(msg_cls: type, receiver: Callable) -> None:
    """取消订阅。用法: bus.off(SubmitMsg, self.on_submit_msg)"""
    signal_name = msg_cls.__name__
    _namespace.signal(signal_name).disconnect(receiver)