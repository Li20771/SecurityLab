"""
SecurityLab —— 仓库级 pytest 钩子。

按命名约定给需要 vcan0 虚拟 CAN 总线的端到端测试打 ``vcan`` 标记：

    测试模块文件名含 ``_vcan`` → @pytest.mark.vcan

这些测试依赖 Linux SocketCAN/ISO-TP 与 vcan0 接口，在没有 vcan 的
CI runner（或非 Linux）上用下面命令排除，只跑纯离线单元测试：

    pytest -m "not vcan"

需要 vcan0 的完整端到端测试在本地 WSL Ubuntu 运行：

    pytest -m vcan            # 或 bash labs/vehicle/uds-basic/tests/run_tests.sh all

标记本身在 pyproject.toml 的 [tool.pytest.ini_options] markers 中注册。
"""

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        # 约定：文件名含 _vcan 的模块都需要 vcan0（SocketCAN/ISO-TP）
        if "_vcan" in item.fspath.basename:
            item.add_marker(pytest.mark.vcan)
