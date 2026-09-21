#!/usr/bin/env python3
"""
Download transfer 静态配置（只保存定义，不保存运行时状态）

本模块与 ``did_database.py`` / ``dtc_database.py`` / ``routine_database.py``
同一定位：**纯静态数据 + 零依赖 + 无副作用**。

- ``REQUEST_DOWNLOAD_DFI / REQUEST_DOWNLOAD_ALFID``：0x34 本实验
  唯一支持的 dataFormatIdentifier（0x00）与
  addressAndLengthFormatIdentifier（0x23 =
  memorySizeLength=2（高半字节）+ memoryAddressLength=3（低半字节））。
- ``MAX_DOWNLOAD_SIZE``：synthetic in-memory 下载目标的大小上限，
  与 2 字节 wire size 字段的最大可编码值一致（0xFFFF）。
  超过该值的请求无法在 2 字节字段中编码，护栏仅经内部 helper 单测。
- ``MAX_TRANSFER_REQUEST_LENGTH``：0x36 完整请求（SID + BSC + data）
  的最大长度 = 128；其中数据部分 ``MAX_TRANSFER_DATA_LENGTH`` = 126。
  **128 是完整请求长度，不是纯 data 长度**（勿混淆）。

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
    "MAX_TRANSFER_REQUEST_LENGTH",
    "MAX_TRANSFER_DATA_LENGTH",
    "MAX_BLOCK_LENGTH_BYTES",
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

#: maxNumberOfBlockLength 的编码字节数（0x34 正响应 lengthFormatIdentifier
#: 高半字节 = 2）。
MAX_BLOCK_LENGTH_BYTES: int = 2
