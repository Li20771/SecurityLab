"""SecurityLab MCP v1：只读工具面（offline / fake-only 实现）。

契约来源：``brief/contracts/SECURITYLAB_MCP_V1.md`` v1.0.0（FROZEN）。
本地 schema 镜像：``securitylab_mcp/contracts/``。

当前状态：

- ``securitylab_ro``：已实现只读 catalog/status/read_file/report/run_result；
- ``securitylab_vcan``：仅保留 disabled schema；任何调用返回 ``tool-disabled``，
  不启动、不连接、不执行任何 vcan/CAN/ECU/车辆/设备操作。
"""

from .errors import CONTRACT_VERSION

__all__ = ["CONTRACT_VERSION"]
