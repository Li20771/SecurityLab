"""``python -m securitylab_mcp`` 入口，等价于 ``securitylab_mcp.server``。"""

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
