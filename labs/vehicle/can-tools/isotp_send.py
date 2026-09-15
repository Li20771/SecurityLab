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

ISO-TP 参数（默认即内核默认，全部不指定时行为与改造前完全一致）::

    --tx-pad       本端发送的 CAN 帧填充字节（内核 TX_PADDING，默认不填充）
    --rx-pad       本端接收时容忍的填充字节（内核 RX_PADDING，默认不启用）
    --fc-stmin     本端**流控帧里发出的**分离时间（STmin 字节，ISO-TP 语义）
    --fc-blocksize 本端**流控帧里发出的**块大小 BS
    --tx-stmin     本端**自己连续帧的**发送间隔（内核 FORCE_TXSTMIN，单位纳秒）
    --timeout      等待响应的超时（秒）

    --fc-stmin 与 --tx-stmin 是**两个方向、两种单位**的参数，不能合并：

        --fc-stmin  写进我们发出的 FC 帧，是"要求对方放慢"（ISO-TP 字节编码）
        --tx-stmin  约束我们自己发 CF 的节奏，是"我们自己放慢"（纳秒，
                    且会覆盖收到的 FC 里的 STmin）

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
    parse_duration_ns,
    parse_hex_bytes,
    parse_optional_byte,
    parse_stmin_argument,
    stmin_actual_ms,
)

#: 接收超时默认值。
#: 沿用既有客户端 labs/vehicle/uds-basic/client/isotp_client.py:44 的 RECV_TIMEOUT，
#: 不另创一套数值。
DEFAULT_TIMEOUT = 2.0


def _parser_for(parse, label: str):
    """
    把"(text, what) -> value" 的解析函数包装成 argparse 的 ``type=`` 可调用对象。

    直接用 lambda 会让 argparse 的报错变成
    ``invalid <lambda> value``（对使用者毫无信息量），
    因此这里给包装函数起一个有意义的名字。
    """
    def _parse(text: str):
        return parse(text, what=label)

    _parse.__name__ = f"{label} <value>"

    return _parse


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
        "--tx-pad",
        type=_parser_for(parse_optional_byte, "--tx-pad"),
        default=None,
        help="本端发送帧的填充字节，如 0xAA 或 170；不指定=不填充（内核默认）",
    )
    parser.add_argument(
        "--rx-pad",
        type=_parser_for(parse_optional_byte, "--rx-pad"),
        default=None,
        help="本端接收时容忍的填充字节，如 0xCC；不指定=不启用（内核默认）",
    )
    parser.add_argument(
        "--fc-stmin",
        type=_parser_for(parse_stmin_argument, "--fc-stmin"),
        default=None,
        help=(
            "本端流控帧里发出的 STmin，可写 0x05（字节）或 5ms / 500us（时长）；"
            "不指定=0x00（内核默认，无间隔要求）"
        ),
    )
    parser.add_argument(
        "--fc-blocksize",
        type=_parser_for(parse_optional_byte, "--fc-blocksize"),
        default=None,
        help="本端流控帧里发出的块大小 BS，0–255；不指定=0（内核默认，无限块）",
    )
    parser.add_argument(
        "--tx-stmin",
        type=_parser_for(parse_duration_ns, "--tx-stmin"),
        default=None,
        help=(
            "本端自己连续帧的发送间隔，单位纳秒，可写 500 / 100us / 2ms / 1s；"
            "会覆盖收到的 FC 里的 STmin；不指定=不强制（内核默认）"
        ),
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


def _describe_optional(value) -> str:
    """把"未指定"（None）与真实取值区分开显示。"""
    if value is None:
        return "(未指定 → 内核默认)"

    if isinstance(value, int):
        return f"0x{value:02X}"

    return str(value)


def socket_options(args) -> dict:
    """
    把已解析的 CLI 参数整理成"要下发给 ISO-TP socket 的选项"。

    只包含**显式指定**的选项，因此"全部未指定"时返回空 dict ——
    这正是"默认行为不变"的保证：不调用 set_opts/set_fc_opts，
    内核默认值就与改造前完全一致。
    """
    return {
        "tx_pad": args.tx_pad,
        "rx_pad": args.rx_pad,
        "fc_stmin": args.fc_stmin,
        "fc_blocksize": args.fc_blocksize,
        "tx_stmin": args.tx_stmin,
    }


def apply_socket_options(sock, options: dict) -> None:
    """
    在 ``bind()`` **之前**下发 ISO-TP 选项。

    :raises RuntimeError: 若 socket 已经 bind（python-isotp 自身也会拒绝）
    """
    general = {}

    if options["tx_pad"] is not None:
        general["txpad"] = options["tx_pad"]

    if options["rx_pad"] is not None:
        general["rxpad"] = options["rx_pad"]

    if options["tx_stmin"] is not None:
        # 单位是纳秒；python-isotp 会自动置 FORCE_TXSTMIN 标志
        general["tx_stmin"] = options["tx_stmin"]

    if general:
        sock.set_opts(**general)

    flow_control = {}

    if options["fc_blocksize"] is not None:
        flow_control["bs"] = options["fc_blocksize"]

    if options["fc_stmin"] is not None:
        flow_control["stmin"] = options["fc_stmin"]

    if flow_control:
        sock.set_fc_opts(**flow_control)


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

    options = socket_options(args)

    active = {key: value for key, value in options.items() if value is not None}

    if active:
        print(f"# tx-pad:     {_describe_optional(options['tx_pad'])}")
        print(f"# rx-pad:     {_describe_optional(options['rx_pad'])}")

        fc_stmin = options["fc_stmin"]

        if fc_stmin is None:
            print("# fc-stmin:   (未指定 → 内核默认 0x00)")
        else:
            print(
                f"# fc-stmin:   0x{fc_stmin:02X}  "
                f"（FC 中要求对方 CF 间隔 ≈ {stmin_actual_ms(fc_stmin):g} ms）"
            )

        print(f"# fc-blocksize: {_describe_optional(options['fc_blocksize'])}")

        tx_stmin = options["tx_stmin"]

        if tx_stmin is None:
            print("# tx-stmin:   (未指定 → 内核默认，不强制)")
        else:
            print(
                f"# tx-stmin:   {tx_stmin} ns "
                f"（本端 CF 间隔 ≈ {tx_stmin / 1_000_000:g} ms，覆盖收到的 FC STmin）"
            )
    else:
        print("# isotp opts: (全部未指定 → 内核默认，行为与改造前一致)")

    address = isotp.Address(
        isotp.AddressingMode.Normal_11bits,
        rxid=rxid,
        txid=txid,
    )

    try:
        sock = isotp.socket()
        apply_socket_options(sock, options)
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

        try:
            response = sock.recv()
        except TimeoutError:
            # python-isotp 的 recv() 在超时时**抛异常**（而不是返回 None），
            # 所以超时必须在这里单独接住，才能给出清晰提示。
            response = None

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
