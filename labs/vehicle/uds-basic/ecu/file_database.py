#!/usr/bin/env python3
"""
UDS Basic —— synthetic in-memory 文件存储（静态定义 + 纯函数）

本模块与 ``did_database.py`` / ``dtc_database.py`` / ``routine_database.py`` /
``transfer_database.py`` 同一定位：**纯静态数据 + 零依赖叶子模块 + 无副作用**。

服务于 0x38 RequestFileTransfer（Design Review v4 冻结 contract）：

- ``FILE_MODE_*``：0x38 ``modeOfOperation`` 常量
  （0=idle / 1=AddFile / 2=DeleteFile / 3=ReplaceFile / 4=ReadFile / 5=ReadDir）。
- ``FILE_DATABASE``：出厂文件内容。**只是 factory source**——
  ``_default_files()`` 在每个 ``EcuState`` 实例化时复制它。运行期所有读写
  只针对 ``state.files``；本 dict 本身**永不被修改**。
- ``_default_files()``：出厂文件 dict 工厂（避免 dataclass 共享可变默认值）。
- ``MAX_FILE_SIZE`` / ``MAX_DIR_INFO_LENGTH``：本实验模型的两个字节上限。
- ``_build_dir_info()``：目录信息（dirInfo）的**确定性**编码器。

这不是真实文件系统：

- 不访问磁盘、不做路径解析、不做符号链接、不做权限检查、不递归遍历；
- "目录"是**隐式**的——它只作为 ``state.files`` 的 key 前缀存在：
  ``/data/config.bin`` 的存在即意味着 ``/data`` 与 ``/`` 是目录。
  目录下文件删净后该隐式目录自动消失（无需清理，因为它从不被显式存储）。
- 文件内容刻意是**确定性合成字节**，不是任何真实 ECU 的固件/镜像，
  也不做 firmware extraction。

依赖方向：零依赖叶子模块（只 import 标准库 ``collections.abc``）。
"""

from collections.abc import Mapping

__all__ = [
    "FILE_MODE_IDLE",
    "FILE_MODE_ADD_FILE",
    "FILE_MODE_DELETE_FILE",
    "FILE_MODE_REPLACE_FILE",
    "FILE_MODE_READ_FILE",
    "FILE_MODE_READ_DIR",
    "MAX_FILE_SIZE",
    "MAX_DIR_INFO_LENGTH",
    "FILE_DATABASE",
    "_default_files",
    "_dir_exists",
    "_path_exists",
    "_ancestor_is_file",
    "_build_dir_info",
]


# ======================================================================
# A. modeOfOperation 常量（Design Review v4 冻结）
# ======================================================================
#
# 注意：modeOfOperation **不是** UDS 子功能字节。非法 mode 一律映射到
# NRC 0x31 requestOutOfRange，而不是子功能专用的 0x12。

#: 无进行中的 file transfer（``EcuState.file_mode`` 的 IDLE 值）。
FILE_MODE_IDLE: int = 0

#: AddFile：建立 download transfer（持续 session）。
FILE_MODE_ADD_FILE: int = 1

#: DeleteFile：**one-shot**，请求完成立即回到 IDLE，不建立 transfer。
FILE_MODE_DELETE_FILE: int = 2

#: ReplaceFile：建立 download transfer（持续 session）。
FILE_MODE_REPLACE_FILE: int = 3

#: ReadFile：建立 upload transfer（持续 session）。
FILE_MODE_READ_FILE: int = 4

#: ReadDir：建立 upload transfer（持续 session）。
FILE_MODE_READ_DIR: int = 5


# ======================================================================
# B. 模型上限
# ======================================================================

#: 单个文件字节上限 = 255 块 × 126 字节 = 32130。
#:
#: 与 ``transfer_database.MAX_UPLOAD_SIZE`` **同源**：读文件走 upload 方向，
#: 而 upload 的 blockSequenceCounter 是单字节（一个会话最多 255 块；超过会
#: 回绕，使"重复上一条"与"新块"不可区分）。因此超过该值的文件无法在一次
#: ReadFile 会话中读完，本模型直接拒绝其声明。
#: 两者必须相等，由单测固化（防止静默漂移）。
MAX_FILE_SIZE: int = 32130

#: dirInfo 编码后的字节上限。
#:
#: dirInfoLength 在 0x38 正响应里是 2 字节字段，但本实验把上限收紧到 255：
#: 目录项数量与文件名长度都受控时该值远达不到 65535，收紧后任何越界都只可能
#: 来自构造异常数据，便于把它当作显式拒绝条件（0x31）而不是静默截断。
MAX_DIR_INFO_LENGTH: int = 255


# ======================================================================
# C. 出厂文件（factory source，运行期只读）
# ======================================================================


def _pattern(seed: int, length: int) -> bytes:
    """生成确定性合成内容 ``(seed + i) & 0xFF``（纯函数）。"""
    return bytes((seed + i) & 0xFF for i in range(length))


#: 出厂文件内容：``路径 → 内容``。
#:
#: 目录结构（**隐式**，不在此 dict 中显式存储）::
#:
#:     /
#:     ├── boot/
#:     │   └── version.txt
#:     ├── logs/
#:     │   └── last.log        300 bytes
#:     └── data/
#:         ├── config.bin      130 bytes
#:         └── sub/
#:             └── a.txt
#:
#: 本 PR 刻意让 ``/data/sub/`` 成为**嵌套**目录、且 ``/boot/`` 与 ``/logs/``
#: 各只有一个文件，以便 ReadDir 的直接子项与递归结果可区分。
FILE_DATABASE: dict[str, bytes] = {
    "/boot/version.txt": b"uds-basic synthetic file store v1\n",
    "/logs/last.log": _pattern(0x40, 300),
    "/data/config.bin": _pattern(0x80, 130),
    "/data/sub/a.txt": b"alpha\n",
}


def _default_files() -> dict[str, bytes]:
    """
    返回一份出厂文件**副本**（``EcuState.files`` 的 default_factory）。

    必须是副本：``FILE_DATABASE`` 是模块级共享对象，若直接把原 dict 交给
    dataclass 字段，所有 ``EcuState`` 实例会共享同一份可变状态，
    AddFile/DeleteFile 会跨实例泄漏。
    """
    return dict(FILE_DATABASE)


# ======================================================================
# D. 目录视图（隐式目录的推导）
# ======================================================================


def _dir_prefix(path: str) -> str:
    """把目录路径规范化为用于 key 前缀匹配的形态（``/data`` → ``/data/``）。"""
    return path if path.endswith("/") else path + "/"


def _dir_exists(files: Mapping[str, bytes], path: str) -> bool:
    """
    判断 ``path`` 是否是目录。

    ``/`` 永远是目录（即使文件全部删净）。其它目录是**隐式**的：
    存在任意 ``path/`` 前缀的 key 才算存在——因此"目录下文件删净后
    该目录消失"是自然结果，不需要任何清理逻辑。
    """
    if path == "/":
        return True

    prefix = _dir_prefix(path)

    return any(key.startswith(prefix) for key in files)


def _path_exists(files: Mapping[str, bytes], path: str) -> bool:
    """
    判断 ``path`` 在本模型命名空间中是否**已存在**（文件或目录）。

    AddFile 的"路径已存在 → NRC 0x22"用本判定：文件名与目录名共享
    同一命名空间，因此对一个已存在的隐式目录（如 ``/data``）执行
    AddFile 必须拒绝，否则会造出同名文件与目录项冲突的目录视图。
    """
    return path in files or _dir_exists(files, path)


def _ancestor_is_file(files: Mapping[str, bytes], path: str) -> bool:
    """
    ``path`` 的某个**祖先**是否已经是文件？

    这是共享命名空间下的第二种冲突（第一种见 ``_path_exists``）。
    若 ``/a`` 已是**文件**，再创建 ``/a/b`` 会让名字 ``a`` 同时是
    "文件"与"隐式目录"；而目录视图里一个名字只能带一个 ``size``，
    两种语义无法唯一编码——``_build_dir_info`` 只能按"目录优先"
    静默丢掉文件那一面，于是 ``/a`` 变成 ReadDir 列不出、却能用
    ReadFile 读到的幽灵条目（两个视图互相矛盾）。

    因此 AddFile 必须把这类路径同样判为不可创建。**本约束是
    本项目的共享命名空间模型决定的，不声称是 ISO 14229-1 的规定。**
    """
    index = path.find("/", 1)

    while index != -1:
        if path[:index] in files:
            return True

        index = path.find("/", index + 1)

    return False


# ======================================================================
# E. dirInfo 编码（确定性的直接子项视图）
# ======================================================================


def _build_dir_info(files: Mapping[str, bytes], path: str) -> bytes:
    """
    生成 ``path`` 的目录信息（dirInfo）——纯函数、确定性、不修改 ``files``。

    编码（Design Review v4 冻结）::

        [count:2] ( [nameLen:2] <name> [size:4] )*

    - 只返回**直接**子项（不递归）：``rest`` 中第一个 ``/`` 之前的部分即 basename；
      ``rest`` 中还有 ``/`` 说明该条目是"下一层"，其 size 恒为 0；
    - ``name`` 按 **ASCII byte order 升序**（排序键显式取 ``encode("ascii")``，
      不依赖 locale / 代码点顺序）；
    - 文件 ``size`` = uint32 **big-endian**；目录条目 ``size`` = 0；
    - 空目录合法：``count = 0`` → 恰好 2 字节 ``\\x00\\x00``；
    - 目录是隐式的，因此不存在"目录记录"需要维护。

    "同名条目既是文件又是目录"在本模型中被 AddFile 的存在性检查排除，
    但本函数仍显式取确定性语义：**目录条目优先**（``size = 0``），
    不依赖 ``files`` 的迭代顺序。

    调用方（0x38）已完成路径合法性校验与"是目录"判定；本函数只负责编码，
    因此不做长度上限检查（见 ``MAX_DIR_INFO_LENGTH``，由 0x38 判定 0x31）。
    """
    prefix = _dir_prefix(path)

    directories: set[str] = set()
    file_sizes: dict[str, int] = {}

    for key, content in files.items():
        if not key.startswith(prefix):
            continue

        rest = key[len(prefix):]
        name, separator, _ = rest.partition("/")

        if not name:
            continue

        if separator:
            directories.add(name)
        else:
            file_sizes[name] = len(content)

    names = sorted(
        directories | set(file_sizes),
        key=lambda name: name.encode("ascii"),
    )

    out = bytearray(len(names).to_bytes(2, "big"))

    for name in names:
        encoded = name.encode("ascii")

        out += len(encoded).to_bytes(2, "big")
        out += encoded
        out += (0 if name in directories else file_sizes[name]).to_bytes(4, "big")

    return bytes(out)


if __name__ == "__main__":
    # 手动观察用：打印出厂文件与各目录的 dirInfo
    print(f"MAX_FILE_SIZE       = {MAX_FILE_SIZE}")
    print(f"MAX_DIR_INFO_LENGTH = {MAX_DIR_INFO_LENGTH}")
    print()

    for file_path, content in sorted(FILE_DATABASE.items()):
        print(f"  {file_path:<24} {len(content):>4} bytes")

    print()

    for directory in ("/", "/boot/", "/logs/", "/data/", "/data/sub/"):
        info = _build_dir_info(FILE_DATABASE, directory)
        print(f"  dirInfo({directory!r}) = {len(info):>3} bytes  {info.hex(' ')}")
