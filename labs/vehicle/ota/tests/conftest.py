"""tests/ 的 sys.path 设置：labs/vehicle/ota、uds-basic/ecu、labs/crypto 都要可 import。"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent          # labs/vehicle/ota/tests
_OTA = _HERE.parent                              # labs/vehicle/ota
_VEHICLE = _OTA.parent                           # labs/vehicle
_LABS = _VEHICLE.parent                          # labs

sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_OTA))
sys.path.insert(0, str(_VEHICLE / "uds-basic" / "ecu"))
sys.path.insert(0, str(_LABS / "crypto"))
