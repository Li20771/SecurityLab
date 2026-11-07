"""让 tests/ 能 import 顶层 gmcrypto 包（把 labs/crypto 加入 sys.path）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
