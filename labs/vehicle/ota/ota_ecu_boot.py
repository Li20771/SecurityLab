#!/usr/bin/env python3
"""在 vcan0 上运行 OTA 门控 ECU（复用 uds-basic 的 transport 与 dispatcher）。

前置（WSL/Linux）：
    sudo modprobe vcan && sudo ip link add dev vcan0 type vcan && sudo ip link set up vcan0
    pip install isotp udsoncan gmssl

用法：
    python ota_ecu_boot.py --pub-hex <64B 公钥 hex> [--current-version 2.3.0]

实现：import uds-basic 的 ecu.py（transport loop），把模块级
``handle_uds_request`` 替换为 ``OtaEcu.handle`` 后调用 ``ecu.main()``——
main() 在每次请求时按**全局名**解析 handle_uds_request，因此替换生效，
且不需要改动 uds-basic 的任何代码。
"""

import argparse
import pathlib
import sys

_THIS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS))
sys.path.insert(0, str(_THIS.parent / "uds-basic" / "ecu"))

import ecu  # noqa: E402  # 依赖 isotp（WSL/Linux 安装后可用）
from ota_ecu import OtaEcu, OtaState  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="OTA 门控虚拟 ECU（vcan0）")
    parser.add_argument("--pub-hex", required=True, help="OEM SM2 公钥（64 字节 hex）")
    parser.add_argument("--current-version", default="2.3.0", help="ECU 当前固件版本")
    args = parser.parse_args()

    ota = OtaEcu(
        bytes.fromhex(args.pub_hex),
        state=ecu.STATE,
        ota_state=OtaState(current_version=args.current_version),
    )

    ecu.handle_uds_request = ota.handle  # 模块级替换（main() 按全局名解析）
    ecu.main()


if __name__ == "__main__":
    main()
