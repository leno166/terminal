"""
@文件: StreamPromptTui_Diag.py
@作者: e-xingyu.guo
@日期: 2026/6/2
@许可: MIT License
@描述: StreamPromptTui <-> diag.Service 适配桥接层
@版本: Version 0.1
"""
import queue
import threading
from logging import getLogger
from typing import Literal
from src.module.StreamPromptTui import App
from src.module.diag import Service, UdsResponse

logger = getLogger(__name__)


# ================== 响应格式化 ==================

def fmt_resp(resp: UdsResponse) -> str:
    if resp.is_negative:
        return f"ERR | 7F {resp.request_sid:02X}:{resp.nrc:02X} | {resp.nrc_desc}"

    head_hex = resp.head.hex(' ') if resp.head else ''
    body_hex = resp.body.hex(' ') if resp.body else ''
    parts = [f"OK | {resp.hex_str}"]
    if head_hex:
        parts.append(f"| head: {head_hex}")
    if body_hex:
        parts.append(f"| body: {body_hex}")
    return " ".join(parts)


# ================== DiagBridge ==================

class DiagBridge(App):
    """StreamPromptTui <-> diag.Service 桥接器。

    用户输入 → on_input() → 命令队列 → 工作线程 → Service 调用 → recv() → TUI 显示
    """

    def __init__(self, service: Service) -> None:
        super().__init__()
        self._service = service
        self._cmd_queue: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._loop, daemon=True, name='DiagBridge')
        self._worker.start()

        self._dispatch = {
            # 会话 / 等级
            's'        : self._cmd_session,
            'session'  : self._cmd_session,
            'l'        : self._cmd_level,
            'level'    : self._cmd_level,
            # 数据读写
            'r'        : self._cmd_read_did,
            'read'     : self._cmd_read_did,
            'read_did' : self._cmd_read_did,
            'w'        : self._cmd_write_did,
            'write'    : self._cmd_write_did,
            'write_did': self._cmd_write_did,
            # 复位
            'rst'      : self._cmd_reset,
            'reset'    : self._cmd_reset,
            # 例程
            'start'    : self._cmd_start_routine,
            'stop'     : self._cmd_stop_routine,
            'result'   : self._cmd_routine_result,
            # 特殊功能
            'ssh'      : self._cmd_unlock_ssh,
            # ECU 切换
            'ecu'      : self._cmd_on,
            'on'       : self._cmd_on,
            # 帮助
            'help'     : self._cmd_help,
            'h'        : self._cmd_help,
            '?'        : self._cmd_help,
        }

        self._help_text = [
            "s / session   <id>          — 切换诊断会话 (1/2/3)",
            "l / level     <L1|L5|L19>   — 切换安全等级",
            "r / read      <DID>         — 读取 DID (hex)",
            "w / write     <DID> <hex>   — 写入 DID",
            "rst / reset   [type]        — ECU 复位 (默认 01)",
            "start         <id> [data]   — 启动例程",
            "stop          <id>          — 停止例程",
            "result        [id]          — 查询例程结果",
            "ssh                          — 解锁 SSH",
            "ecu / on      <name>        — 切换 ECU",
            "<raw hex>                    — 透传 UDS 命令",
        ]

    # ── IBridge 接口实现 ──────────────────────────────

    def on_input(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        cmd, _, args = line.partition(' ')
        handler = self._dispatch.get(cmd.lower())
        self._cmd_queue.put((line, args, handler))

    # ── 工作线程 ──────────────────────────────────────

    def _loop(self) -> None:
        while True:
            line, args, handler = self._cmd_queue.get()
            try:
                if handler:
                    handler(args)
                else:
                    resp = self._service.send(line)
                    self.recv(fmt_resp(resp))
            except Exception:
                logger.exception("命令执行失败: %s", line)
                self.recv(f"[ERROR] 命令执行异常，见日志")

    # ── 命令 handlers ────────────────────────────────

    def _cmd_session(self, args: str) -> None:
        ss_id = int(args.strip())
        ok, resp = self._service.change_session(ss_id)
        tag = "OK" if ok else "FAIL"
        self.recv(f"{tag} | session -> 0x{ss_id:02X} | {fmt_resp(resp)}")

    def _cmd_level(self, args: Literal['L1', 'L5', 'L19']) -> None:
        level = args
        ok, resp = self._service.change_level(level)
        tag = "OK" if ok else "FAIL"
        self.recv(f"{tag} | level -> {level} | {fmt_resp(resp)}")

    def _cmd_read_did(self, args: str) -> None:
        did = int(args.strip(), 16)
        resp = self._service.read_did(did)
        self.recv(f"read_did(0x{did:04X}) | {fmt_resp(resp)}")

    def _cmd_write_did(self, args: str) -> None:
        parts = args.strip().split()
        did = int(parts[0], 16)
        data = bytes.fromhex(parts[1])
        resp = self._service.write_did(did, data)
        self.recv(f"write_did(0x{did:04X}, {data.hex(' ')}) | {fmt_resp(resp)}")

    def _cmd_reset(self, args: str) -> None:
        reset_type = int(args.strip(), 16) if args.strip() else 0x01
        ok = self._service.reset(reset_type)
        tag = "OK" if ok else "FAIL"
        self.recv(f"{tag} | reset(0x{reset_type:02X})")

    def _cmd_start_routine(self, args: str) -> None:
        parts = args.strip().split()
        rid = int(parts[0], 16)
        data = bytes.fromhex(parts[1]) if len(parts) > 1 else None
        resp = self._service.start_routine(rid, data)
        self.recv(f"start_routine(0x{rid:04X}) | {fmt_resp(resp)}")

    def _cmd_stop_routine(self, args: str) -> None:
        rid = int(args.strip(), 16)
        ok = self._service.stop_routine(rid)
        tag = "OK" if ok else "FAIL"
        self.recv(f"{tag} | stop_routine(0x{rid:04X})")

    def _cmd_routine_result(self, args: str) -> None:
        rid = int(args.strip(), 16) if args.strip() else None
        resp = self._service.get_routine_result(rid)
        self.recv(f"routine_result | {fmt_resp(resp)}")

    def _cmd_unlock_ssh(self, args: str) -> None:
        ok = self._service.unlock_ssh()
        tag = "OK" if ok else "FAIL"
        self.recv(f"{tag} | unlock_ssh")

    def _cmd_on(self, args: str) -> None:
        name = args.strip()
        self._service.on(name)
        self.recv(f"OK | on -> {name}")

    def _cmd_help(self, args: str) -> None:
        for line in self._help_text:
            self.recv(line)


# ================== main ==================

def main():
    import argparse
    from pathlib import Path
    import logging

    # ── 日志配置 ──────────────────────────────────────
    log_file = Path(__file__).parent / "stream_prompt_tui_diag.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)-7s] [%(threadName)s] %(name)s %(filename)s:%(lineno)d %(funcName)s() - %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.FileHandler(str(log_file), mode='w', encoding='utf-8')],
    )
    logging.getLogger("textual").setLevel(logging.WARNING)

    # ── 参数解析 ──────────────────────────────────────
    parser = argparse.ArgumentParser(description="StreamPromptTui + diag 适配终端")
    parser.add_argument("--ip", required=True, help="DoIP 服务端 IP")
    parser.add_argument("--ecu", nargs='+', required=True,
                        help="ECU 定义: name=ip=addr (如: mcu=198.18.44.49=1301)")
    parser.add_argument("--platform", default="P_30TU", help="车型平台 (默认 P_30TU)")
    parser.add_argument("--serial", type=float, default=2.0, help="序列版本 (默认 2.0)")
    parser.add_argument("--soc", type=int, default=1, help="SOC 编号，1=40H, 2=50H (默认 1)")
    parser.add_argument("--port", type=int, default=13400, help="DoIP 端口 (默认 13400)")
    parser.add_argument("--tester", type=lambda x: int(x, 16), default=0x0E80,
                        help="Tester 逻辑地址 (默认 0E80)")

    args = parser.parse_args()

    # ── 解析 ECU ──────────────────────────────────────
    ecus = {}
    for item in args.ecu:
        name, ip, addr = item.split('=')
        ecus[name] = (ip, int(addr, 16))

    service = Service(
        ip=args.ip, ecus=ecus, platform=args.platform,
        serial_version=args.serial, soc_num=args.soc,
        port=args.port, tester=args.tester,
    )

    with service:
        bridge = DiagBridge(service)
        bridge.run(completion_dict={
            'session 1': '默认会话',
            'session 3': '扩展会话',
            'level L1' : '安全等级 L1',
            'level L5' : '安全等级 L5',
            'level L19': '安全等级 L19',
            'read '    : '读取 DID',
            'write '   : '写入 DID',
            'reset'    : 'ECU 硬复位',
            'reset 03' : 'ECU 软复位',
            'ssh'      : '解锁 SSH',
            'start '   : '启动例程',
            'stop '    : '停止例程',
            'result'   : '查询例程结果',
            'on '      : '切换 ECU',
            'help'     : '显示帮助',
        })


if __name__ == '__main__':
    main()
