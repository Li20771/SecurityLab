#!/usr/bin/env python3

import isotp
import udsoncan

from udsoncan.client import Client
from udsoncan.connections import IsoTPSocketConnection
from udsoncan.services import ReadDataByIdentifier


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

    client_config = {
        "data_identifiers": {
            0xF190: udsoncan.AsciiCodec(17),
            0xF180: udsoncan.AsciiCodec(13),
        }
    }

    with Client(connection, config=client_config) as client:
        print("UDS client connected.")

        response = client.read_data_by_identifier([0xF190, 0xF180])

        if response is None:
            print("No UDS response.")
            return

        print("UDS response:")
        print(response)

        print("Parsed values:")

        for did, value in response.service_data.values.items():
            print(f"  0x{did:04X}: {value!r}")


if __name__ == "__main__":
    main()
