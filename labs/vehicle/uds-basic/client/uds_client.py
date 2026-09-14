#!/usr/bin/env python3
"""
UDS Basic —— udsoncan 客户端示例

使用 udsoncan.Client 通过 Linux CAN_ISOTP 与虚拟 ECU 通信。

覆盖三个核心服务：

- change_session()           0x10 DiagnosticSessionControl
- ecu_reset()                0x11 ECUReset
- read_data_by_identifier()  0x22 ReadDataByIdentifier

用法：

    python uds_client.py               # 默认：读取 DID（F190 + F180）
    python uds_client.py read          # 读取 DID（单 DID + 多 DID）
    python uds_client.py session       # 切换诊断会话
    python uds_client.py reset         # ECU 复位
    python uds_client.py all           # 依次执行以上全部

安全边界：

只使用 vcan0（虚拟 CAN），不接触真实车辆 / 真实 ECU / 真实 CAN 适配器。
"""

import sys

import isotp
import udsoncan

from udsoncan.client import Client
from udsoncan.connections import IsoTPSocketConnection


INTERFACE = "vcan0"

# Tester -> ECU
TX_ID = 0x7E0

# ECU -> Tester
RX_ID = 0x7E8

DID_F180 = 0xF180
DID_F190 = 0xF190

# DID 数据长度必须与虚拟 ECU 的 did_database.py 一致，
# 否则 udsoncan 会因长度不符而报 InvalidResponseException。
DID_LENGTHS = {
    DID_F180: 13,
    DID_F190: 17,
}


def build_connection() -> IsoTPSocketConnection:
    """创建 Linux CAN_ISOTP 连接。"""
    address = isotp.Address(
        isotp.AddressingMode.Normal_11bits,
        rxid=RX_ID,
        txid=TX_ID,
    )

    return IsoTPSocketConnection(interface=INTERFACE, address=address)


def build_config() -> dict:
    """构造 udsoncan 客户端配置。"""
    return {
        "data_identifiers": {
            did: udsoncan.AsciiCodec(length)
            for did, length in DID_LENGTHS.items()
        }
    }


def demo_read_data_by_identifier(client: Client) -> None:
    """0x22 ReadDataByIdentifier：单 DID 与多 DID。"""
    print("--- 0x22 ReadDataByIdentifier ---")

    print(f"[1] 读取单个 DID 0x{DID_F190:04X}")
    response = client.read_data_by_identifier(DID_F190)

    if response is None:
        print("    无响应")
    else:
        print(f"    肯定响应: {response.positive}")
        for did, value in response.service_data.values.items():
            print(f"    DID 0x{did:04X} = {value!r}")

    print(f"[2] 一次读取多个 DID [0x{DID_F190:04X}, 0x{DID_F180:04X}]")
    response = client.read_data_by_identifier([DID_F190, DID_F180])

    if response is None:
        print("    无响应")
    else:
        print(f"    肯定响应: {response.positive}")
        for did, value in response.service_data.values.items():
            print(f"    DID 0x{did:04X} = {value!r}")

    print()


def demo_change_session(client: Client) -> None:
    """0x10 DiagnosticSessionControl。"""
    print("--- 0x10 DiagnosticSessionControl ---")

    session = udsoncan.services.DiagnosticSessionControl.Session

    for name, value in (
        ("extendedDiagnosticSession", session.extendedDiagnosticSession),
        ("programmingSession", session.programmingSession),
        ("defaultSession", session.defaultSession),
    ):
        print(f"[*] change_session({name})")

        response = client.change_session(value)

        if response is None:
            print("    无响应")
            continue

        print(f"    肯定响应: {response.positive}")
        print(f"    会话回显: 0x{response.service_data.session_echo:02X}")
        print(
            "    P2ServerMax:  "
            f"{response.service_data.p2_server_max} s "
            "(原始值 0x0032 = 50 ms)"
        )
        print(
            "    P2*ServerMax: "
            f"{response.service_data.p2_star_server_max} s "
            "(原始值 0x0064 = 100 ms)"
        )

    print()


def demo_ecu_reset(client: Client) -> None:
    """0x11 ECUReset。"""
    print("--- 0x11 ECUReset ---")

    response = client.ecu_reset(udsoncan.services.ECUReset.ResetType.hardReset)

    if response is None:
        print("无响应")
    else:
        print(f"    肯定响应: {response.positive}")
        print(f"    resetType 回显: 0x{response.service_data.reset_type_echo:02X}")

    print()


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "read"

    if mode not in ("read", "session", "reset", "all"):
        raise SystemExit(
            f"未知模式：{mode}\n可用：read | session | reset | all"
        )

    print("udsoncan:", udsoncan.__version__)
    print("can-isotp:", isotp.__version__)
    print(f"CAN interface: {INTERFACE}")
    print(f"TX ID: 0x{TX_ID:03X}")
    print(f"RX ID: 0x{RX_ID:03X}")
    print()

    with Client(build_connection(), config=build_config()) as client:
        print("UDS client connected.")
        print()

        if mode in ("read", "all"):
            demo_read_data_by_identifier(client)

        if mode in ("session", "all"):
            demo_change_session(client)

        if mode in ("reset", "all"):
            demo_ecu_reset(client)

    print("UDS client closed.")


if __name__ == "__main__":
    main()
