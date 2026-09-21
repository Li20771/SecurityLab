#!/usr/bin/env python3
"""
UDS Basic —— 原始 ISO-TP 客户端

直接通过 Linux CAN_ISOTP 发送 UDS 原始报文字节，
不经过 udsoncan 的请求构造与响应解释。

用途：

- 观察最原始的 UDS 请求 / 响应字节
- 快速验证 ECU 行为
- 手动实验任意 SID / NRC

用法：

    python isotp_client.py                 # 默认发送 10 01
    python isotp_client.py 11 01           # 发送 ECUReset hardReset
    python isotp_client.py 22 F1 90        # 发送 ReadDataByIdentifier
    python isotp_client.py 22 F1 90 F1 80  # 多 DID 读取
    python isotp_client.py 27 01           # 未实现服务

安全边界：

只使用 vcan0（虚拟 CAN），不接触真实车辆 / 真实 ECU / 真实 CAN 适配器。
"""

import sys

import isotp

INTERFACE = "vcan0"

# Tester -> ECU
TX_ID = 0x7E0

# ECU -> Tester
RX_ID = 0x7E8

# 默认请求：DiagnosticSessionControl / defaultSession
DEFAULT_REQUEST = "10 01"

# 接收超时（秒）
RECV_TIMEOUT = 2.0


def parse_request(argv: list) -> bytes:
    """
    从命令行参数解析 UDS 请求。

    支持两种写法：

        isotp_client.py 11 01
        isotp_client.py "11 01"
    """
    if not argv:
        return bytes.fromhex(DEFAULT_REQUEST)

    raw = " ".join(argv).replace(",", " ").strip()

    try:
        return bytes.fromhex(raw)
    except ValueError as exc:
        raise SystemExit(
            f"无法解析请求字节：{raw!r}\n"
            f"示例：python isotp_client.py 22 F1 90\n"
            f"错误：{exc}"
        ) from exc


def describe_response(response: bytes) -> None:
    """打印响应的 UDS 语义解析。"""
    if not response:
        return

    service = response[0]

    if service == 0x7F and len(response) >= 3:
        print(f"  → 否定响应：请求 SID 0x{response[1]:02X}，NRC 0x{response[2]:02X}")
        return

    if service == 0x50:
        print(f"  → DiagnosticSessionControl 肯定响应，会话 0x{response[1]:02X}")
        return

    if service == 0x51:
        print(f"  → ECUReset 肯定响应，resetType 0x{response[1]:02X}")
        return

    if service == 0x62:
        print("  → ReadDataByIdentifier 肯定响应：")

        # 本实验的 DID 数据长度已知，用于切分多 DID 响应
        known_lengths = {
            0xF190: 17,
            0xF180: 13,
        }

        offset = 1

        while offset + 1 < len(response):
            did = (response[offset] << 8) | response[offset + 1]
            offset += 2

            remaining = len(response) - offset
            length = known_lengths.get(did, remaining)

            if length > remaining:
                length = remaining

            data = response[offset:offset + length]
            offset += length

            print(f"      DID 0x{did:04X}: {data.hex(' ')}")
            print(f"                  {data.decode('ascii', errors='replace')!r}")


def main() -> None:
    request = parse_request(sys.argv[1:])

    address = isotp.Address(
        isotp.AddressingMode.Normal_11bits,
        rxid=RX_ID,
        txid=TX_ID,
    )

    sock = isotp.socket()
    sock.bind(INTERFACE, address)
    sock.settimeout(RECV_TIMEOUT)

    print(f"Interface: {INTERFACE}")
    print(f"TX ID:     0x{TX_ID:03X}")
    print(f"RX ID:     0x{RX_ID:03X}")
    print()

    try:
        print(f"TX UDS: {request.hex(' ')}")

        sock.send(request)

        response = sock.recv()

        if response is None:
            print("RX UDS: timeout")
            return

        print(f"RX UDS: {response.hex(' ')}")
        describe_response(response)

    finally:
        sock.close()


if __name__ == "__main__":
    main()
