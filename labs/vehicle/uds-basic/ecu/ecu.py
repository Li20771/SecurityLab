#!/usr/bin/env python3

import isotp


INTERFACE = "vcan0"
RX_ID = 0x7E0
TX_ID = 0x7E8

DEFAULT_SESSION = 0x01
CURRENT_SESSION = DEFAULT_SESSION


def handle_uds_request(request: bytes) -> bytes:
    global CURRENT_SESSION

    if not request:
        return bytes([0x7F, 0x00, 0x13])

    service = request[0]

    # DiagnosticSessionControl (0x10)
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

        # Positive response:
        # 50 <session> <P2 high> <P2 low> <P2* high> <P2* low>
        return bytes([
            0x50,
            session_type,
            0x00, 0x32,  # P2ServerMax = 50 ms
            0x00, 0x64,  # P2*ServerMax = 1 s
        ])

    # Service not supported
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
