#!/usr/bin/env python3
"""
完整 ISO-TP payload 发送（ISO-TP payload transmission）

发送**一段完整的 ISO-TP payload**：由 python-isotp / 内核 CAN_ISOTP
负责把它切分成必要的 CAN 帧（单帧，或首帧 + 连续帧，并处理流控）。

    python3 isotp_send.py 10 03
    python3 isotp_send.py 22 F1 90
    python3 isotp_send.py --txid 0x7E0 --rxid 0x7E8 27 01

与 ``can_inject.py`` 的区别（关键概念）::

    can_inject.py  发送 1 个 CAN 帧（你自己写 PCI 字节）
    isotp_send.py  发送 1 段 ISO-TP payload（由协议栈分帧）

ID 方向语义（与 ECU / client 保持一致）::

    --txid  = Tester 的发送 ID = ECU 的接收 ID   （默认 0x7E0）
    --rxid  = Tester 的接收 ID = ECU 的发送 ID   （默认 0x7E8）

    即 --txid 不是"ECU 的发送 ID"。Tester 发 0x7E0，收 0x7E8。

本工具**不做 UDS 语义解释**：不解析 SID、NRC、DID、SecurityAccess。
它只负责 payload → ISO-TP → CAN，以及 CAN → ISO-TP → 响应字节的原样打印，
因此不与 ``uds_server.py`` 形成业务耦合。

安全边界：

    只允许 vcan0（见 parsers.guard_interface）。

依赖：python-isotp（当前环境 2.0.7）。
"""

import argparse
import sys

import isotp

from parsers import (
    ALLOWED_INTERFACE,
    ArgumentParser,
    CAN_DATA_MAX_LEN,
    InputError,
    add_interface_argument,
    guard_interface,
    parse_can_id,
    parse_hex_bytes,
)

#: 接收超时默认值。
#: 沿用既有客户端 labs/vehicle/uds-basic/client/isotp_client.py:44 的 RECV_TIMEOUT，
#: 不另创一套数值。
DEFAULT_TIMEOUT = 2.0


def build_parser() -> argparse.ArgumentParser:
    parser = ArgumentParser(
        description=(
            "通过 ISO-TP（内核 CAN_ISOTP，Normal 11-bit 寻址）发送一段完整 payload，"
            "并打印原始响应字节（不做 UDS 解释）"
        ),
        epilog=(
            "示例：python3 isotp_send.py 10 03\n"
            "      python3 isotp_send.py 22 F1 90\n"
            "      python3 isotp_send.py --txid 0x7E0 --rxid 0x7E8 27 01"
        ),
    )

    add_interface_argument(parser)

    parser.add_argument(
        "--txid",
        default="0x7E0",
        help="Tester 发送 ID（= ECU 接收 ID），接受 0x7E0 或 2016。默认 0x7E0",
    )
    parser.add_argument(
        "--rxid",
        default="0x7E8",
        help="Tester 接收 ID（= ECU 发送 ID），接受 0x7E8 或 2024。默认 0x7E8",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"等待响应超时（秒），默认 {DEFAULT_TIMEOUT}（沿用既有 client 的取值）",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="只发送，不等待响应",
    )
    parser.add_argument(
        "payload",
        nargs="+",
        help="ISO-TP payload 字节（十六进制），支持 '10 03' 或 '1003'",
    )

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        interface = guard_interface(args.interface)
        txid = parse_can_id(args.txid)
        rxid = parse_can_id(args.rxid)
        payload = parse_hex_bytes(args.payload)
    except InputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not payload:
        print("ERROR: payload 不能为空", file=sys.stderr)
        return 2

    if args.timeout <= 0:
        print("ERROR: --timeout 必须大于 0", file=sys.stderr)
        return 2

    single_frame = len(payload) <= CAN_DATA_MAX_LEN - 1

    print(f"# interface: {interface}")
    print(f"# txid:      0x{txid:03X}  (Tester 发送 = ECU 接收)")
    print(f"# rxid:      0x{rxid:03X}  (Tester 接收 = ECU 发送)")
    print(f"# addressing: Normal_11bits")
    print(f"# payload:    {payload.hex(' ')}  ({len(payload)} bytes)")
    print(
        "# framing:   "
        + ("single frame 预计" if single_frame else "multi-frame 预计（FF + CF）")
    )

    address = isotp.Address(
        isotp.AddressingMode.Normal_11bits,
        rxid=rxid,
        txid=txid,
    )

    try:
        sock = isotp.socket()
        sock.bind(interface, address)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: 无法在 {interface} 上绑定 ISO-TP: {exc}", file=sys.stderr)
        return 1

    if not args.no_wait:
        sock.settimeout(args.timeout)

    try:
        print()
        print(f"TX: {payload.hex(' ')}")

        sock.send(payload)

        if args.no_wait:
            print("# 已发送（--no-wait，未等待响应）")
            return 0

        response = sock.recv()

        if response is None:
            print("RX: timeout（未收到响应）")
            return 1

        print(f"RX: {response.hex(' ')}  ({len(response)} bytes)")

    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: ISO-TP 通信失败: {exc}", file=sys.stderr)
        return 1
    finally:
        sock.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
