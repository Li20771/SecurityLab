#!/usr/bin/env python3

import isotp
import udsoncan

from udsoncan.client import Client
from udsoncan.connections import IsoTPSocketConnection


INTERFACE = "vcan0"
TX_ID = 0x7E0
RX_ID = 0x7E8


def main() -> None:
    print("udsoncan:", udsoncan.__version__)
    print(f"CAN interface: {INTERFACE}")
    print(f"TX ID: 0x{TX_ID:03X}")
    print(f"RX ID: 0x{RX_ID:03X}")

    address = isotp.Address(
        isotp.AddressingMode.Normal_11bits,
        rxid=RX_ID,
        txid=TX_ID,
    )

    connection = IsoTPSocketConnection(
        interface=INTERFACE,
        address=address,
    )

    with Client(connection) as client:
        print("UDS client connected.")

        response = client.change_session(
            udsoncan.DiagnosticSessionControl.Session.extendedDiagnosticSession
        )

        print("UDS response:", response)


if __name__ == "__main__":
    main()
