#!/usr/bin/env python3
"""
CAN 帧采集到 Vector BLF 文件，并支持读回校验与回放。

与只打印到 stdout 的 ``can_capture.py`` 不同，本工具把 raw CAN 帧落盘为
**Binary Log Format (.blf)**——Vector CANoe/CANalyzer、python-can、
cantools 生态通用的二进制日志格式，可长期保存并离线回放。

三个子命令::

    record   在 vcan0 上采集帧写入 .blf（--count 收满即停，--max-seconds 兜底）
    inspect  读回 .blf，逐帧打印并给出统计（用于 capture→save→load 一致性校验）
    replay   把 .blf 里的帧重新发到 vcan0（默认忽略原始间隔、顺序即时发送）

闭环用法见 ``capture/capture_lab.sh``。

安全边界：只允许 vcan0（parsers.guard_interface），不接触真实 CAN 接口。
依赖：python-can（BLFReader / BLFWriter）。
"""

import argparse
import sys
import time

import can

from parsers import (
    ArgumentParser,
    InputError,
    add_interface_argument,
    format_can_frame,
    guard_interface,
)


def _open_bus(interface: str) -> can.BusABC:
    try:
        return can.interface.Bus(interface="socketcan", channel=interface)
    except Exception as exc:  # noqa: BLE001
        raise InputError(f"无法打开 CAN 接口 {interface}: {exc}") from exc


def cmd_record(args: argparse.Namespace) -> int:
    interface = guard_interface(args.interface)
    if args.count <= 0:
        print("ERROR: record 的 --count 必须 > 0", file=sys.stderr)
        return 2
    if args.max_seconds <= 0:
        print("ERROR: --max-seconds 必须 > 0", file=sys.stderr)
        return 2

    bus = _open_bus(interface)
    writer = can.BLFWriter(args.out)
    received = 0
    started = time.monotonic()
    print(f"# recording on {interface} -> {args.out} (want {args.count} frames)")

    try:
        while received < args.count:
            if time.monotonic() - started > args.max_seconds:
                print(
                    f"# max-seconds {args.max_seconds} reached, got {received} frame(s)",
                    file=sys.stderr,
                )
                break
            msg = bus.recv(timeout=0.5)
            if msg is None:
                continue
            writer.on_message_received(msg)
            received += 1
            print(
                format_can_frame(msg.timestamp, msg.arbitration_id, bytes(msg.data))
                + ("  EXT" if msg.is_extended_id else "")
            )
    except KeyboardInterrupt:
        pass
    finally:
        writer.stop()
        bus.shutdown()

    print(f"# wrote {received} frame(s) to {args.out}")
    return 0 if received == args.count else 1


def _read_blf(path: str):
    try:
        return list(can.BLFReader(path))
    except Exception as exc:  # noqa: BLE001
        raise InputError(f"无法读取 BLF {path}: {exc}") from exc


def cmd_inspect(args: argparse.Namespace) -> int:
    messages = _read_blf(args.blf)
    if not messages:
        print("ERROR: BLF 中没有任何帧", file=sys.stderr)
        return 1

    ids: dict[int, int] = {}
    dlcs: dict[int, int] = {}
    ext = 0
    for msg in messages:
        ids[msg.arbitration_id] = ids.get(msg.arbitration_id, 0) + 1
        dlcs[msg.dlc] = dlcs.get(msg.dlc, 0) + 1
        if msg.is_extended_id:
            ext += 1
        if not args.quiet:
            print(
                format_can_frame(msg.timestamp, msg.arbitration_id, bytes(msg.data))
                + ("  EXT" if msg.is_extended_id else "")
            )

    duration = messages[-1].timestamp - messages[0].timestamp if len(messages) > 1 else 0.0
    id_summary = ",".join(f"0x{k:03X}:{v}" for k, v in sorted(ids.items()))
    dlc_summary = ",".join(f"DLC{k}:{v}" for k, v in sorted(dlcs.items()))
    print("# ---- BLF summary ----")
    print(f"# file      : {args.blf}")
    print(f"# count     : {len(messages)}")
    print(f"# ids       : {id_summary}")
    print(f"# dlc       : {dlc_summary}")
    print(f"# extended  : {ext}")
    print(f"# duration  : {duration:.6f} s")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    interface = guard_interface(args.interface)
    messages = _read_blf(args.blf)
    if not messages:
        print("ERROR: BLF 中没有任何帧", file=sys.stderr)
        return 1

    bus = _open_bus(interface)
    base = messages[0].timestamp
    sent = 0
    try:
        for msg in messages:
            if args.keep_timing and sent > 0:
                delay = msg.timestamp - base - (messages[sent - 1].timestamp - base)
                if delay > 0:
                    time.sleep(min(delay, args.max_gap))
            # 重新构造一帧，避免把 BLF 元数据原样带回
            tx = can.Message(
                arbitration_id=msg.arbitration_id,
                data=bytes(msg.data),
                is_extended_id=msg.is_extended_id,
            )
            bus.send(tx)
            sent += 1
    except KeyboardInterrupt:
        pass
    finally:
        bus.shutdown()

    print(f"# replayed {sent} frame(s) from {args.blf} onto {interface}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = ArgumentParser(
        prog="can_capture_blf.py",
        description="vcan0 raw CAN 帧的 BLF 采集 / 读回 / 回放（仅 vcan0）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_rec = sub.add_parser("record", help="采集帧写入 .blf")
    add_interface_argument(p_rec)
    p_rec.add_argument("--out", required=True, help="输出 .blf 路径")
    p_rec.add_argument("--count", type=int, required=True, help="收满多少帧后停止")
    p_rec.add_argument(
        "--max-seconds", type=float, default=15.0, help="总采集时长兜底（秒，默认 15）"
    )
    p_rec.set_defaults(func=cmd_record)

    p_insp = sub.add_parser("inspect", help="读回 .blf 并打印统计")
    p_insp.add_argument("blf", help=".blf 文件路径")
    p_insp.add_argument("--quiet", action="store_true", help="只打印汇总，不逐帧打印")
    p_insp.set_defaults(func=cmd_inspect)

    p_rep = sub.add_parser("replay", help="把 .blf 帧重新发到 vcan0")
    add_interface_argument(p_rep)
    p_rep.add_argument("blf", help=".blf 文件路径")
    p_rep.add_argument(
        "--keep-timing", action="store_true", help="保留原始帧间隔（默认即时顺序发送）"
    )
    p_rep.add_argument("--max-gap", type=float, default=1.0, help="保留间隔时的单帧上限（秒）")
    p_rep.set_defaults(func=cmd_replay)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except InputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
