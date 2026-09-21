#!/usr/bin/env python3
"""
Download / Upload transfer 静态配置（只保存定义，不保存运行时状态）

本模块与 ``did_database.py`` / ``dtc_database.py`` / ``routine_database.py``
同一定位：**纯静态数据 + 零依赖 + 无副作用**。

- ``REQUEST_DOWNLOAD_DFI / REQUEST_DOWNLOAD_ALFID``：0x34 / 0x35 本实验
  唯一支持的 dataFormatIdentifier（0x00）与
  addressAndLengthFormatIdentifier（0x23 =
  memorySizeLength=2（高半字节）+ memoryAddressLength=3（低半字节））。
  两方向共享同一 8 字节请求结构与这些常量。
- ``MAX_DOWNLOAD_SIZE``：synthetic in-memory 下载目标的大小上限，
  与 2 字节 wire size 字段的最大可编码值一致（0xFFFF）。
  超过该值的请求无法在 2 字节字段中编码，护栏仅经内部 helper 单测。
- ``MAX_UPLOAD_SIZE``：上传会话的大小上限 = 255 × 126 = 32130。
  理由：上传方向 BSC 为单字节，单会话超过 255 块会回绕，使
  "重复上一条"与"新块"不可区分；这是本实验模型限制（教学用途）。
- ``MAX_TRANSFER_REQUEST_LENGTH``：0x36 完整请求（SID + BSC + data）
  的最大长度 = 128；其中数据部分 ``MAX_TRANSFER_DATA_LENGTH`` = 126。
  **128 是完整请求长度，不是纯 data 长度**（勿混淆）。
- ``UPLOAD_SOURCE`` / ``_upload_chunk()``：0x35 的 synthetic 上传数据源
  （256 字节循环模式，纯函数即时生成，不分配大缓冲、不访问文件系统）。

运行时状态（buffer / counter / active 等）一律位于
``EcuState``，本模块内容**从不被 handler 修改**；本模型是纯内存
synthetic 目标——不是真实 Flash、不接触文件系统、不执行真实刷写。

依赖方向：零依赖叶子模块。
"""

__all__ = [
    "REQUEST_DOWNLOAD_DFI",
    "REQUEST_DOWNLOAD_ALFID",
    "DOWNLOAD_ADDRESS_LENGTH",
    "DOWNLOAD_SIZE_LENGTH",
    "MAX_DOWNLOAD_SIZE",
    "MAX_UPLOAD_SIZE",
    "MAX_TRANSFER_REQUEST_LENGTH",
    "MAX_TRANSFER_DATA_LENGTH",
    "MAX_BLOCK_LENGTH_BYTES",
    "UPLOAD_SOURCE",
]


#: 0x34 dataFormatIdentifier：本实验固定 0x00（无加密/压缩格式）。
REQUEST_DOWNLOAD_DFI: int = 0x00

#: 0x34 addressAndLengthFormatIdentifier：
#: 高半字节 = memorySizeLength = 2，低半字节 = memoryAddressLength = 3。
REQUEST_DOWNLOAD_ALFID: int = 0x23

#: memoryAddress 长度（字节）。
DOWNLOAD_ADDRESS_LENGTH: int = 3

#: memorySize 长度（字节）。
DOWNLOAD_SIZE_LENGTH: int = 2

#: 下载大小上限 = 2 字节 size 字段最大可编码值（0xFFFF = 65535）。
#: 资源防线：synthetic 内存缓冲绝不会超过该值。
MAX_DOWNLOAD_SIZE: int = 0xFFFF

#: 0x36 完整 TransferData 请求的最大长度（SID + BSC + data）= 128。
#: 对应 0x34 正响应声明的 maxNumberOfBlockLength（2 字节编码）。
MAX_TRANSFER_REQUEST_LENGTH: int = 128

#: 0x36 中纯 transferData 部分的最大长度 = 128 − 1(SID) − 1(BSC) = 126。
MAX_TRANSFER_DATA_LENGTH: int = 126

#: maxNumberOfBlockLength 的编码字节数（0x34/0x35 正响应
#: lengthFormatIdentifier 高半字节 = 2）。
MAX_BLOCK_LENGTH_BYTES: int = 2

#: 0x35 RequestUpload 的会话大小上限 = 255 块 × 126 字节 = 32130。
#: BSC 是单字节，一个会话最多 255 块；超过会回绕，使"重复上一条"
#: 与"新块"不可区分。本实验模型限制，非真实 ECU 的通用上限。
MAX_UPLOAD_SIZE: int = 32130

#: 0x35 的 synthetic 上传数据源：256 字节循环模式（教学确定性数据，
#: 不是真实 ECU Flash、不是 firmware 文件、不做 extraction）。
UPLOAD_SOURCE: bytes = bytes(range(256))


def _upload_chunk(position: int, length: int) -> bytes:
    """
    生成 upload 源 ``position`` 起 ``length`` 字节的数据（纯函数）。

    - 不修改 ``UPLOAD_SOURCE``、不分配大缓冲、不访问文件系统；
    - 超出 256 时按循环模式取模（``position=254, length=4`` →
      ``FE FF 00 01``）；
    - 相同 ``(position, length)`` 必得相同结果——duplicate 重发
      上一块的基础（配合固定块契约确定性重建）。
    """
    if length < 0:
        raise ValueError(f"length 不能为负：{length}")

    source_len = len(UPLOAD_SOURCE)
    out = bytearray()

    for offset in range(length):
        out.append(UPLOAD_SOURCE[(position + offset) % source_len])

    return bytes(out)
