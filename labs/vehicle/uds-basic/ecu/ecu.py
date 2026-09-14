#!/usr/bin/env python3

import os

import isotp

from did_database import DID_DATABASE


def _env_int(name: str, default: int) -> int:
    """读取整数型环境变量，支持 0x 十六进制写法。"""
    raw = os.environ.get(name)

    if raw is None or raw == "":
        return default

    return int(raw, 0)


# 默认值即为标准 11-bit 诊断寻址，保持原行为不变。
# 允许用环境变量覆盖，便于多 ECU / 多通道实验。
INTERFACE = os.environ.get("UDS_INTERFACE", "vcan0")
RX_ID = _env_int("UDS_RX_ID", 0x7E0)
TX_ID = _env_int("UDS_TX_ID", 0x7E8)

DEFAULT_SESSION = 0x01
CURRENT_SESSION = DEFAULT_SESSION


def handle_uds_request(request: bytes) -> bytes:
    global CURRENT_SESSION

    if not request:
        return bytes([0x7F, 0x00, 0x13])

    service = request[0]

    # ============================================================
    # DiagnosticSessionControl (0x10)
    # ============================================================
    if service == 0x10:
        if len(request) != 2:
            return bytes([0x7F, 0x10, 0x13])

        session_type = request[1]

        supported_sessions = {
            0x01,  # defaultSession
            0x02,  # programmingSession
            0x03,  # extendedDiagnosticSession
        }

        if session_type not in supported_sessions:
            return bytes([0x7F, 0x10, 0x12])

        CURRENT_SESSION = session_type

        return bytes([
            0x50,
            session_type,
            0x00, 0x32,
            0x00, 0x64,
        ])

    # ============================================================
    # ECUReset (0x11)
    # ============================================================
    if service == 0x11:
        if len(request) != 2:
            return bytes([0x7F, 0x11, 0x13])

        reset_type = request[1]

        # hardReset
        if reset_type == 0x01:
            CURRENT_SESSION = DEFAULT_SESSION

            return bytes([
                0x51,
                reset_type,
            ])

        # unsupported reset type
        return bytes([0x7F, 0x11, 0x12])

    # ============================================================
    # ReadDataByIdentifier (0x22)
    # ============================================================
    if service == 0x22:
        # 至少需要：
        # 0x22 + 2-byte DID
        #
        # 多个 DID：
        # 0x22 + DID + DID + ...
        if len(request) < 3 or (len(request) - 1) % 2 != 0:
            return bytes([0x7F, 0x22, 0x13])

        response = bytearray([0x62])

        for offset in range(1, len(request), 2):
            did = (request[offset] << 8) | request[offset + 1]

            data = DID_DATABASE.get(did)

            if data is None:
                return bytes([0x7F, 0x22, 0x31])

            response.extend([
                request[offset],
                request[offset + 1],
            ])

            response.extend(data)

        return bytes(response)

    # ============================================================
    # Service not supported
    # ============================================================
    return bytes([0x7F, service, 0x11])


def main() -> None:
    print(f"ISO-TP version: {isotp.__version__}")
    print(f"CAN interface: {INTERFACE}")
    print(f"RX ID: 0x{RX_ID:03X}")
    print(f"TX ID: 0x{TX_ID:03X}")

    address = isotp.Address(
        isotp.AddressingMode.Normal_11bits,
        rxid=RX_ID,
        txid=TX_ID,
    )

    sock = isotp.socket()
    sock.bind(INTERFACE, address)

    print("ISO-TP socket bound successfully.")
    print(f"Current UDS session: 0x{CURRENT_SESSION:02X}")
    print("Virtual ECU is waiting for UDS requests...")

    try:
        while True:
            data = sock.recv()

            if data is None:
                continue

            print(f"RX UDS: {data.hex(' ')}")

            response = handle_uds_request(data)

            print(f"TX UDS: {response.hex(' ')}")

            sock.send(response)

    except KeyboardInterrupt:
        print("\nStopping virtual ECU...")

    finally:
        sock.close()


if __name__ == "__main__":
    main()
