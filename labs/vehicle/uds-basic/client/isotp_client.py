#!/usr/bin/env python3

import isotp


INTERFACE = "vcan0"
RX_ID = 0x7E8
TX_ID = 0x7E0


def read_did(sock, did: int) -> None:
    request = bytes([
        0x22,
        (did >> 8) & 0xFF,
        did & 0xFF,
    ])

    print(f"\nTX UDS: {request.hex(' ')}")
    sock.send(request)

    response = sock.recv()

    if response is None:
        print("RX UDS: timeout")
        return

    print(f"RX UDS: {response.hex(' ')}")

    if len(response) >= 3 and response[0] == 0x62:
        response_did = (response[1] << 8) | response[2]
        data = response[3:]

        print(f"DID: 0x{response_did:04X}")
        print(f"DATA: {data.hex(' ')}")
        print(f"ASCII: {data.decode('ascii', errors='replace')}")
    elif len(response) >= 3 and response[0] == 0x7F:
        print(f"Negative Response: NRC=0x{response[2]:02X}")


def main() -> None:
    address = isotp.Address(
        isotp.AddressingMode.Normal_11bits,
        rxid=RX_ID,
        txid=TX_ID,
    )

    sock = isotp.socket()
    sock.bind(INTERFACE, address)

    try:
        read_did(sock, 0xF190)
        read_did(sock, 0xF180)

    finally:
        sock.close()


if __name__ == "__main__":
    main()

