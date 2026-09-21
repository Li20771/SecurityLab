#!/usr/bin/env python3
"""
raw CAN 帧注入（raw CAN frame injection）

发送**一帧原始 CAN 报文**——直接构造 CAN 帧，不经过任何 ISO-TP 处理。

    python3 can_inject.py 0x7E0 02 10 03 00 00 00 00 00

发送的是::

    CAN ID = 0x7E0
    DLC    = 8
    DATA   = 02 10 03 00 00 00 00 00

注意与 ``isotp_send.py`` 的区别（这是理解 ISO-TP 的关键）：

    can_inject.py  发送 **一个 CAN 帧**，PCI 字节由你自己写
    isotp_send.py  发送 **一段完整 ISO-TP payload**，由 python-isotp 负责分帧

本工具**刻意不使用 python-isotp**：它的用途正是让你可以绕过 ISO-TP 栈，
在链路层精确构造任意帧（例如后续阶段构造非法 PCI / 异常分帧）。

安全边界：

    只允许 vcan0（见 parsers.guard_interface）。这是刻意的限制——
    避免因参数误写把原始帧发到真实 CAN 接口。

依赖：python-can（当前环境 4.6.1）。
"""

import argparse
import sys

import can

from parsers import (
    ArgumentParser,
    InputError,
    add_interface_argument,
    format_can_frame,
    guard_interface,
    parse_can_id,
    parse_hex_bytes,
)


def build_parser() -> argparse.ArgumentParser:
    parser = ArgumentParser(
        description="向 vcan0 发送一帧 raw CAN 报文（不经 ISO-TP）",
        epilog=(
            "示例：python3 can_inject.py 0x7E0 02 10 03 00 00 00 00 00\n"
            "      python3 can_inject.py 0x555 01 02 03"
        ),
    )

    add_interface_argument(parser)

    parser.add_argument(
        "can_id",
        help="CAN 仲裁 ID，11-bit（0x000–0x7FF），接受 0x7E0 或 2016",
    )
    parser.add_argument(
        "data",
        nargs="*",
        help="数据字节（十六进制，0–8 字节）。支持 '02 10 03' 或 '021003'",
    )

    parser.add_argument(
        "--data",
        dest="data_option",
        nargs="*",
        default=None,
        help="数据字节的等价写法（与位置参数二选一，不能同时使用）",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="重复发送次数，默认 1。写 0 表示持续发送直到 Ctrl+C",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="重复发送之间的间隔（秒），默认 0（不等待）",
    )

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # ---- 输入校验（全部在 parsers 中，可独立测试）----
    try:
        interface = guard_interface(args.interface)
        arbitration_id = parse_can_id(args.can_id)

        if args.data_option is not None and args.data:
            raise InputError("不要同时用位置参数和 --data 指定数据字节")

        tokens = args.data_option if args.data_option is not None else args.data
        data = parse_hex_bytes(tokens)

    except InputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.repeat < 0:
        print("ERROR: --repeat 不能为负数", file=sys.stderr)
        return 2

    if args.interval < 0:
        print("ERROR: --interval 不能为负数", file=sys.stderr)
        return 2

    # ---- 发送 ----
    try:
        bus = can.interface.Bus(interface="socketcan", channel=interface)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: 无法打开 CAN 接口 {interface}: {exc}", file=sys.stderr)
        return 1

    message = can.Message(
        arbitration_id=arbitration_id,
        is_extended_id=False,
        data=data,
    )

    print(f"# interface: {interface}")
    print(f"# frame:     {format_can_frame(None, arbitration_id, data)}")
    print(f"# repeat:    {'unlimited' if args.repeat == 0 else args.repeat}")

    sent = 0

    try:
        while True:
            bus.send(message)
            sent += 1
            print(f"TX #{sent}: {format_can_frame(None, arbitration_id, data)}", flush=True)

            if args.repeat and sent >= args.repeat:
                break

            if args.interval:
                import time

                time.sleep(args.interval)

    except KeyboardInterrupt:
        pass
    finally:
        bus.shutdown()

    print(f"# sent {sent} frame(s)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
