#!/usr/bin/env python3

import isotp
import udsoncan

from udsoncan.client import Client
from udsoncan.connections import IsoTPSocketConnection
from udsoncan.services import DiagnosticSessionControl


INTERFACE = "vcan0"

# Client -> ECU
TX_ID = 0x7E0

# ECU -> Client
RX_ID = 0x7E8


def main() -> None:
    print("udsoncan:", udsoncan.__version__)
    print("can-isotp:", isotp.__version__)
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
            DiagnosticSessionControl.Session.extendedDiagnosticSession
        )

        print("UDS response:")
        print(response)


if __name__ == "__main__":
    main()

