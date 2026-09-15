#!/usr/bin/env python3
"""
CAN 帧观察器（raw CAN observation）

在 SocketCAN 接口上持续接收并打印 **raw CAN 帧**：时间戳 / 仲裁 ID / DLC / 数据。

这是**链路层**工具：它看到的是 CAN 帧本身，不是 ISO-TP payload，
也不是 UDS 报文。要看 ISO-TP 分帧（SF / FF / CF / FC），正是用本工具观察。

用法::

    python3 can_capture.py                       # 持续打印 vcan0 上的所有帧
    python3 can_capture.py --count 10            # 收满 10 帧后退出
    python3 can_capture.py --id 0x7E8            # 只看某个仲裁 ID
    python3 can_capture.py --timeout 0.5         # 每帧等待超时

安全边界：

    只允许 vcan0（见 parsers.guard_interface）。不接触真实 CAN 接口。

依赖：python-can（当前环境 4.6.1）。不使用 python-isotp。
"""

import argparse
import sys

import can

from parsers import (
    ALLOWED_INTERFACE,
    ArgumentParser,
    InputError,
    add_interface_argument,
    format_can_frame,
    guard_interface,
    parse_can_id,
)


def build_parser() -> argparse.ArgumentParser:
    parser = ArgumentParser(
        description="在 vcan0 上观察 raw CAN 帧（时间戳 / ID / DLC / 数据）",
        epilog="示例：python3 can_capture.py --id 0x7E8 --count 5",
    )

    add_interface_argument(parser)

    parser.add_argument(
        "--id",
        dest="arbitration_id",
        default=None,
        help="只显示该 CAN ID（接受 0x7E8 或 2024）。省略则显示全部",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=1.0,
        help="每帧接收超时（秒），默认 1.0。超时后继续等待，不退出",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="收到多少帧后退出；0 表示持续运行（默认 0）",
    )

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        interface = guard_interface(args.interface)
        wanted_id = (
            None if args.arbitration_id is None else parse_can_id(args.arbitration_id)
        )
    except InputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.timeout <= 0:
        print("ERROR: --timeout 必须大于 0", file=sys.stderr)
        return 2

    if args.count < 0:
        print("ERROR: --count 不能为负数", file=sys.stderr)
        return 2

    print(f"# interface: {interface}")
    print(f"# filter:    {'all' if wanted_id is None else f'0x{wanted_id:03X}'}")
    print(f"# timeout:   {args.timeout}s")
    print(f"# count:     {'unlimited' if args.count == 0 else args.count}")
    print("# timestamp  ID   [DLC]  DATA")

    try:
        bus = can.interface.Bus(interface="socketcan", channel=interface)
    except Exception as exc:  # noqa: BLE001 - 需要把底层错误转成清晰提示
        print(f"ERROR: 无法打开 CAN 接口 {interface}: {exc}", file=sys.stderr)
        return 1

    received = 0

    try:
        while True:
            try:
                message = bus.recv(timeout=args.timeout)
            except KeyboardInterrupt:
                break

            if message is None:
                # 超时：继续等待（直到 count 满足或被 Ctrl+C）
                continue

            if wanted_id is not None and message.arbitration_id != wanted_id:
                continue

            print(
                format_can_frame(
                    message.timestamp,
                    message.arbitration_id,
                    bytes(message.data),
                ),
                flush=True,
            )

            received += 1

            if args.count and received >= args.count:
                break

    except KeyboardInterrupt:
        pass
    finally:
        bus.shutdown()

    print(f"# captured {received} frame(s)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
