#!/usr/bin/env python3
"""
UDS Basic —— 0x38 RequestFileTransfer 离线单元测试

直接调用协议层 ``uds_server``，不依赖 vcan0 / SocketCAN / ISO-TP。

覆盖（按已冻结的 Design Review v4）：

1. ``file_database``：常量、出厂文件、``MAX_FILE_SIZE`` 与
   ``MAX_UPLOAD_SIZE`` 同值、``FILE_DATABASE`` 不被运行期修改
2. ``_build_dir_info``：count / nameLen / size 精确编码、ASCII byte order
   排序、直接子项（不递归）、空目录、root、确定性、不修改 files
3. 0x38 五个 mode 的**逐字节**正响应（含 ReadDir 恰好 10 字节、
   ReadFile 恰好 16 字节、响应不含 dirInfo 本体）
4. NRC 优先级九步（0x13 / 0x31 / 0x22 各自的触发点与顺序）
5. ReadDir / ReadFile 经 0x36/0x37 的完整 upload 传输（含 duplicate
   逐字节一致、wrong BSC 0x73、initial 36 00 0x73、incomplete 0x37 0x22）
6. AddFile / ReplaceFile 经 0x36/0x37 的完整 download 传输
   （**只有** 0x37 完成时提交 files；abort / incomplete 不污染）
7. DeleteFile one-shot：不建立 transfer、完成后立即 IDLE、隐式目录消失
8. 8 向互斥矩阵（0x34/0x35 handler 零改动）
9. 生命周期：hardReset 清 file_mode / file_path 且**保留 files**、
   session switch 不终止 transfer、实例隔离

明确**不测**（本 PR OUT of scope）：0x06 ResumeFile / 0x39、
真实文件系统 / Flash / firmware extraction / 压缩 / 加密 / 签名校验、
Permission / session framework、Gateway、fuzz。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_file_transfer_unit.py

或：

    bash tests/run_tests.sh unit
"""

import os
import sys
import unittest
from dataclasses import fields

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

from file_database import (  # noqa: E402
    FILE_DATABASE,
    FILE_MODE_ADD_FILE,
    FILE_MODE_DELETE_FILE,
    FILE_MODE_IDLE,
    FILE_MODE_READ_DIR,
    FILE_MODE_READ_FILE,
    FILE_MODE_REPLACE_FILE,
    MAX_DIR_INFO_LENGTH,
    MAX_FILE_SIZE,
    _ancestor_is_file,
    _build_dir_info,
    _default_files,
    _dir_exists,
    _path_exists,
)
from transfer_database import (  # noqa: E402
    MAX_UPLOAD_SIZE,
    _upload_chunk,
)
from uds_server import (  # noqa: E402
    DIR_INFO_PARAMETER_LENGTH,
    FILE_DFI,
    FILE_LENGTH_FORMAT_IDENTIFIER,
    FILE_SIZE_PARAMETER_LENGTH,
    MAX_FILE_PATH_LENGTH,
    SUPPORTED_FILE_MODES,
    EcuState,
    handle_uds_request,
)

SID_REQUEST_FILE_TRANSFER = 0x38
SID_REQUEST_DOWNLOAD = 0x34
SID_REQUEST_UPLOAD = 0x35
SID_TRANSFER_DATA = 0x36
SID_REQUEST_TRANSFER_EXIT = 0x37
SID_ECU_RESET = 0x11
SID_SESSION_CONTROL = 0x10

NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_SEQUENCE_ERROR = 0x24
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_WRONG_BLOCK_SEQUENCE_COUNTER = 0x73

POS_REQUEST_FILE_TRANSFER = 0x78
POS_TRANSFER_DATA = 0x76
POS_REQUEST_TRANSFER_EXIT = 0x77

#: 单块最大 data 长度（与 0x36 upload 分支的固定块契约一致）。
_CHUNK = 126


# ----------------------------------------------------------------------
# 请求构造 helper
# ----------------------------------------------------------------------


def _path_field(path: str) -> bytes:
    raw = path.encode("ascii")
    return bytes([(len(raw) >> 8) & 0xFF, len(raw) & 0xFF]) + raw


def read_dir(path: str) -> bytes:
    return bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_READ_DIR]) + _path_field(path)


def read_file(path: str, dfi: int = 0x00) -> bytes:
    return (
        bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_READ_FILE])
        + _path_field(path)
        + bytes([dfi])
    )


def delete_file(path: str) -> bytes:
    return bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_DELETE_FILE]) + _path_field(path)


def _file_write_request(
    mode: int,
    path: str,
    size: int,
    *,
    dfi: int = 0x00,
    fsp: int = FILE_SIZE_PARAMETER_LENGTH,
    compressed: int | None = None,
) -> bytes:
    if compressed is None:
        compressed = size

    return (
        bytes([SID_REQUEST_FILE_TRANSFER, mode])
        + _path_field(path)
        + bytes([dfi, fsp])
        + size.to_bytes(4, "big")
        + compressed.to_bytes(4, "big")
    )


def add_file(path: str, size: int, **kwargs) -> bytes:
    return _file_write_request(FILE_MODE_ADD_FILE, path, size, **kwargs)


def replace_file(path: str, size: int, **kwargs) -> bytes:
    return _file_write_request(FILE_MODE_REPLACE_FILE, path, size, **kwargs)


def transfer_data(counter: int, data: bytes = b"") -> bytes:
    return bytes([SID_TRANSFER_DATA, counter]) + data


def transfer_exit() -> bytes:
    return bytes([SID_REQUEST_TRANSFER_EXIT])


def download_request(size: int = 8) -> bytes:
    return bytes([SID_REQUEST_DOWNLOAD, 0x00, 0x23, 0x11, 0x22, 0x33,
                  (size >> 8) & 0xFF, size & 0xFF])


def upload_request(size: int = 130) -> bytes:
    return bytes([SID_REQUEST_UPLOAD, 0x00, 0x23, 0x11, 0x22, 0x33,
                  (size >> 8) & 0xFF, size & 0xFF])


def parse_dir_info(info: bytes) -> list[tuple[str, int]]:
    """
    把 dirInfo 解码回 ``(name, size)`` 列表。

    与 ``_build_dir_info`` 是一对**独立**实现：用于验证编码可被唯一解码
    （长度自洽、条目数自洽、字节恰好用尽），而不是拿编码器自证。
    """
    count = int.from_bytes(info[:2], "big")
    offset = 2
    entries: list[tuple[str, int]] = []

    for _ in range(count):
        name_len = int.from_bytes(info[offset:offset + 2], "big")
        offset += 2

        name = info[offset:offset + name_len].decode("ascii")
        offset += name_len

        size = int.from_bytes(info[offset:offset + 4], "big")
        offset += 4

        entries.append((name, size))

    if offset != len(info):
        raise AssertionError(f"dirInfo 有 {len(info) - offset} 字节残余")

    return entries


class FileTransferTestCase(unittest.TestCase):
    """每个测试使用独立 EcuState 实例，互不污染。"""

    def setUp(self) -> None:
        self.state = EcuState()

    # -- 状态快照 ------------------------------------------------------

    def upload_snapshot(self) -> tuple:
        return (
            self.state.upload_active,
            self.state.upload_address,
            self.state.upload_size,
            self.state.upload_expected_block,
            self.state.upload_sent,
        )

    def download_snapshot(self) -> tuple:
        return (
            self.state.download_active,
            self.state.download_address,
            self.state.download_size,
            self.state.download_expected_block,
            bytes(self.state.download_buffer),
            self.state.download_last_block_request,
        )

    def file_snapshot(self) -> tuple:
        return (
            self.state.file_mode,
            self.state.file_path,
            self.upload_snapshot(),
            self.download_snapshot(),
        )

    # -- 请求 ----------------------------------------------------------

    def send(self, request: bytes) -> bytes:
        return handle_uds_request(request, self.state)


# ----------------------------------------------------------------------
# 1. file_database 常量与出厂文件
# ----------------------------------------------------------------------


class TestFileDatabase(FileTransferTestCase):

    def test_default_files_copies_factory(self) -> None:
        """``_default_files()`` 必须返回副本，不是 FILE_DATABASE 本身。"""
        files = _default_files()

        self.assertEqual(files, FILE_DATABASE)
        self.assertIsNot(files, FILE_DATABASE)

    def test_factory_files_are_deterministic(self) -> None:
        self.assertEqual(_default_files(), _default_files())

    def test_expected_factory_layout(self) -> None:
        """出厂文件集合与两个固定大小。"""
        self.assertEqual(
            set(FILE_DATABASE),
            {"/boot/version.txt", "/logs/last.log",
             "/data/config.bin", "/data/sub/a.txt"},
        )
        self.assertEqual(len(FILE_DATABASE["/logs/last.log"]), 300)
        self.assertEqual(len(FILE_DATABASE["/data/config.bin"]), 130)

    def test_max_file_size_equals_max_upload_size(self) -> None:
        """两个上限必须相等，否则 ReadFile 会建立读不完的会话。

        ``MAX_UPLOAD_SIZE`` 由单字节 BSC（255 块）决定；文件上限同源。
        本断言防止两者静默漂移。
        """
        self.assertEqual(MAX_FILE_SIZE, MAX_UPLOAD_SIZE)
        self.assertEqual(MAX_FILE_SIZE, 255 * 126)

    def test_all_factory_files_within_max_file_size(self) -> None:
        for path, content in FILE_DATABASE.items():
            with self.subTest(path=path):
                self.assertLessEqual(len(content), MAX_FILE_SIZE)

    def test_dir_info_limit_is_255(self) -> None:
        self.assertEqual(MAX_DIR_INFO_LENGTH, 255)

    def test_mode_constants(self) -> None:
        self.assertEqual(FILE_MODE_IDLE, 0)
        self.assertEqual(FILE_MODE_ADD_FILE, 1)
        self.assertEqual(FILE_MODE_DELETE_FILE, 2)
        self.assertEqual(FILE_MODE_REPLACE_FILE, 3)
        self.assertEqual(FILE_MODE_READ_FILE, 4)
        self.assertEqual(FILE_MODE_READ_DIR, 5)

    def test_supported_modes_excludes_idle(self) -> None:
        self.assertNotIn(FILE_MODE_IDLE, SUPPORTED_FILE_MODES)
        self.assertEqual(SUPPORTED_FILE_MODES, frozenset({1, 2, 3, 4, 5}))

    def test_profile_constants(self) -> None:
        """本实验 profile 的三个固定值（README 4.8 冻结）。"""
        self.assertEqual(FILE_LENGTH_FORMAT_IDENTIFIER, 0x02)
        self.assertEqual(FILE_SIZE_PARAMETER_LENGTH, 0x04)
        self.assertEqual(DIR_INFO_PARAMETER_LENGTH, 0x0002)
        self.assertEqual(FILE_DFI, 0x00)
        self.assertEqual(MAX_FILE_PATH_LENGTH, 64)


# ----------------------------------------------------------------------
# 2. _build_dir_info 编码
# ----------------------------------------------------------------------


class TestBuildDirInfo(FileTransferTestCase):

    def test_empty_directory_is_two_zero_bytes(self) -> None:
        self.assertEqual(_build_dir_info({}, "/"), b"\x00\x00")
        self.assertEqual(len(_build_dir_info({}, "/")), 2)

    def test_single_file_exact_encoding(self) -> None:
        """[count:2] [nameLen:2] <name> [size:4] 逐字节。"""
        info = _build_dir_info({"/a.txt": b"hello"}, "/")

        self.assertEqual(
            info,
            b"\x00\x01"                  # count = 1
            b"\x00\x05" + b"a.txt"       # nameLen = 5
            b"\x00\x00\x00\x05",         # size = 5
        )

    def test_count_is_entry_count(self) -> None:
        info = _build_dir_info({"/a": b"", "/b": b"", "/c": b""}, "/")

        self.assertEqual(int.from_bytes(info[:2], "big"), 3)

    def test_directory_entry_size_is_zero(self) -> None:
        """隐式目录条目的 size 恒为 0。"""
        info = _build_dir_info({"/dir/file.txt": b"0123456789"}, "/")

        self.assertEqual(
            info,
            b"\x00\x01" + b"\x00\x03" + b"dir" + b"\x00\x00\x00\x00",
        )

    def test_size_is_uint32_big_endian(self) -> None:
        info = _build_dir_info({"/f": bytes(300)}, "/")

        self.assertEqual(info[-4:], (300).to_bytes(4, "big"))
        self.assertEqual(info[-4:], b"\x00\x00\x01\x2c")

    def test_ascii_byte_order_sorting(self) -> None:
        """排序键是 ASCII byte order，不是大小写无关排序。"""
        info = _build_dir_info({"/alpha": b"", "/Beta": b"", "/Zeta": b""}, "/")

        # ASCII: 'B'(0x42) < 'Z'(0x5A) < 'a'(0x61)
        self.assertEqual(
            info,
            b"\x00\x03"
            b"\x00\x04Beta\x00\x00\x00\x00"
            b"\x00\x04Zeta\x00\x00\x00\x00"
            b"\x00\x05alpha\x00\x00\x00\x00",
        )

    def test_direct_children_only(self) -> None:
        """只列直接子项：/data/ 不含 sub/a.txt、也不含 config.bin 的深层路径。"""
        info = _build_dir_info(FILE_DATABASE, "/data/")

        self.assertIn(b"config.bin", info)
        self.assertIn(b"sub", info)
        self.assertNotIn(b"a.txt", info)

    def test_root_lists_only_top_level_names(self) -> None:
        info = _build_dir_info(FILE_DATABASE, "/")

        self.assertIn(b"boot", info)
        self.assertIn(b"data", info)
        self.assertIn(b"logs", info)
        # 深层文件不出现在 root
        self.assertNotIn(b"version.txt", info)
        self.assertNotIn(b"config.bin", info)

    def test_nested_directory(self) -> None:
        info = _build_dir_info(FILE_DATABASE, "/data/sub/")

        self.assertEqual(
            info,
            b"\x00\x01\x00\x05" + b"a.txt" + b"\x00\x00\x00\x06",
        )

    def test_trailing_slash_is_normalized(self) -> None:
        """目录路径带不带尾斜杠结果一致。"""
        self.assertEqual(
            _build_dir_info(FILE_DATABASE, "/data/"),
            _build_dir_info(FILE_DATABASE, "/data"),
        )

    def test_unknown_directory_is_empty(self) -> None:
        self.assertEqual(_build_dir_info(FILE_DATABASE, "/nope/"), b"\x00\x00")

    def test_deterministic_and_order_independent(self) -> None:
        """相同 files 内容、不同插入顺序 → 相同 bytes。"""
        first = {"/b": b"1", "/a": b"22", "/c/d": b"333"}
        second = {"/c/d": b"333", "/a": b"22", "/b": b"1"}

        self.assertEqual(
            _build_dir_info(first, "/"), _build_dir_info(second, "/"))

    def test_does_not_modify_files(self) -> None:
        files = _default_files()
        before = dict(files)

        _build_dir_info(files, "/")
        _build_dir_info(files, "/data/")

        self.assertEqual(files, before)

    def test_directory_wins_over_same_named_file(self) -> None:
        """同名文件 / 目录并存时目录条目优先（确定性语义，不依赖迭代序）。"""
        files = {"/x": b"0123456789", "/x/y": b"z"}

        info = _build_dir_info(files, "/")

        self.assertEqual(
            info, b"\x00\x01\x00\x01" + b"x" + b"\x00\x00\x00\x00")

    def test_multi_entry_length_matches_formula(self) -> None:
        files = {f"/f{i:02d}": b"x" for i in range(20)}

        self.assertEqual(len(_build_dir_info(files, "/")), 2 + 20 * (6 + 3))


class TestFileModelHelpers(FileTransferTestCase):
    """``_dir_exists`` / ``_path_exists``：隐式目录与共享命名空间语义。"""

    def test_dir_exists_for_root_and_nested(self) -> None:
        self.assertTrue(_dir_exists(FILE_DATABASE, "/"))
        self.assertTrue(_dir_exists(FILE_DATABASE, "/data"))
        self.assertTrue(_dir_exists(FILE_DATABASE, "/data/"))
        self.assertTrue(_dir_exists(FILE_DATABASE, "/data/sub/"))

    def test_dir_exists_is_false_for_files(self) -> None:
        self.assertFalse(_dir_exists(FILE_DATABASE, "/data/config.bin"))
        self.assertFalse(_dir_exists(FILE_DATABASE, "/data/sub/a.txt"))

    def test_dir_exists_is_false_for_missing(self) -> None:
        self.assertFalse(_dir_exists(FILE_DATABASE, "/nope/"))

    def test_dir_exists_needs_a_descendant(self) -> None:
        """目录是隐式的：没有后代就不存在——但 "/" 永远是目录。"""
        self.assertFalse(_dir_exists({}, "/data/"))
        self.assertTrue(_dir_exists({}, "/"))

    def test_path_exists_covers_files_and_directories(self) -> None:
        """AddFile 的存在性判定：文件名与目录名共享同一命名空间。"""
        self.assertTrue(_path_exists(FILE_DATABASE, "/data/config.bin"))
        self.assertTrue(_path_exists(FILE_DATABASE, "/data"))
        self.assertTrue(_path_exists(FILE_DATABASE, "/"))
        self.assertFalse(_path_exists(FILE_DATABASE, "/nope.bin"))

    def test_path_exists_after_delete(self) -> None:
        files = _default_files()
        del files["/data/config.bin"]

        self.assertFalse(_path_exists(files, "/data/config.bin"))
        # /data/ 仍有 sub/a.txt，因此目录本身依然存在
        self.assertTrue(_path_exists(files, "/data"))

    def test_ancestor_is_file_detects_direct_parent(self) -> None:
        self.assertTrue(_ancestor_is_file({"/a": b"x"}, "/a/b"))

    def test_ancestor_is_file_detects_deep_ancestor(self) -> None:
        self.assertTrue(_ancestor_is_file({"/a": b"x"}, "/a/b/c/d"))

    def test_ancestor_is_file_ignores_implicit_directories(self) -> None:
        """隐式目录是合法的祖先——只有"祖先本身是文件"才冲突。"""
        self.assertFalse(_ancestor_is_file(FILE_DATABASE, "/data/new.txt"))
        self.assertFalse(_ancestor_is_file(FILE_DATABASE, "/data/sub/deep.txt"))
        self.assertFalse(_ancestor_is_file(FILE_DATABASE, "/boot/x"))

    def test_ancestor_is_file_false_without_ancestors(self) -> None:
        self.assertFalse(_ancestor_is_file({"/a": b"x"}, "/a"))
        self.assertFalse(_ancestor_is_file({}, "/a"))


class TestDirInfoUniqueEncoding(FileTransferTestCase):
    """dirInfo 必须可**唯一**解码。

    共享命名空间下"一个名字只能有一种语义"是硬约束：目录视图里一个名字
    只带一个 ``size``，若同一名字既是文件又是目录，两种语义无法同时表示。
    本项目因此在 AddFile 处就拒绝所有会造出该局面的路径。

    本约束是本项目的模型约定，**不声称是 ISO 14229-1 的规定**。
    """

    def assert_namespace_consistent(self, files) -> None:
        """任何 key 的祖先都不能是文件。"""
        for key in files:
            index = key.find("/", 1)

            while index != -1:
                self.assertNotIn(
                    key[:index], files,
                    f"{key} 的祖先 {key[:index]} 已是文件 → 名字冲突",
                )
                index = key.find("/", index + 1)

    def assert_dir_info_round_trips(self, files, path) -> None:
        """dirInfo 可唯一解码，且与 ``files`` 内容逐项一致。"""
        entries = parse_dir_info(_build_dir_info(files, path))

        prefix = path if path.endswith("/") else path + "/"
        directories: set[str] = set()
        sizes: dict[str, int] = {}

        for key, content in files.items():
            if not key.startswith(prefix):
                continue

            name, separator, _ = key[len(prefix):].partition("/")

            if not name:
                continue

            if separator:
                directories.add(name)
            else:
                sizes[name] = len(content)

        # 核心不变量：同一名字不能既是文件又是目录
        self.assertEqual(
            directories & set(sizes), set(),
            f"{path} 下出现同名的文件与目录，dirInfo 无法唯一编码",
        )

        expected = {name: 0 for name in directories}
        expected.update(sizes)

        self.assertEqual(dict(entries), expected)
        self.assertEqual(len({name for name, _ in entries}), len(entries))

    def test_factory_store_round_trips(self) -> None:
        self.assert_namespace_consistent(FILE_DATABASE)

        for path in ("/", "/boot/", "/logs/", "/data/", "/data/sub/"):
            with self.subTest(path=path):
                self.assert_dir_info_round_trips(FILE_DATABASE, path)

    def test_after_protocol_writes_round_trips(self) -> None:
        self.assert_dir_info_round_trips(self.state.files, "/")

        for path, body in (("/new.bin", b"abc"), ("/data/extra.txt", b"z")):
            self.assertEqual(
                self.send(add_file(path, len(body))),
                bytes([0x78, 0x01, 0x02, 0x00, 0x80, 0x00]),
            )
            self.send(transfer_data(0x01, body))
            self.send(transfer_exit())

        self.assert_namespace_consistent(self.state.files)

        for path in ("/", "/data/"):
            with self.subTest(path=path):
                self.assert_dir_info_round_trips(self.state.files, path)

    def test_after_delete_round_trips(self) -> None:
        self.send(delete_file("/data/config.bin"))

        self.assert_namespace_consistent(self.state.files)
        self.assert_dir_info_round_trips(self.state.files, "/data/")

    def test_collision_attempts_keep_namespace_consistent(self) -> None:
        """所有被拒的冲突写入都不得污染命名空间。"""
        self.send(add_file("/a", 1))
        self.send(transfer_data(0x01, b"A"))
        self.send(transfer_exit())

        for path in ("/a", "/a/b", "/a/b/c", "/data", "/boot"):
            with self.subTest(add=path):
                self.assertEqual(
                    self.send(add_file(path, 4)),
                    bytes([0x7F, SID_REQUEST_FILE_TRANSFER,
                           NRC_CONDITIONS_NOT_CORRECT]),
                )

        for path in ("/a/b", "/a/b/c"):
            with self.subTest(replace=path):
                self.assertEqual(
                    self.send(replace_file(path, 4)),
                    bytes([0x7F, SID_REQUEST_FILE_TRANSFER,
                           NRC_REQUEST_OUT_OF_RANGE]),
                )

        self.assert_namespace_consistent(self.state.files)
        self.assert_dir_info_round_trips(self.state.files, "/")

    def test_deleting_parent_frees_the_namespace(self) -> None:
        """删除父文件后，其下路径重新可创建（命名空间随之更新）。"""
        self.send(add_file("/a", 1))
        self.send(transfer_data(0x01, b"A"))
        self.send(transfer_exit())

        self.assertEqual(
            self.send(add_file("/a/b", 1)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_CONDITIONS_NOT_CORRECT]),
        )

        self.send(delete_file("/a"))

        self.assertEqual(
            self.send(add_file("/a/b", 1)),
            bytes([0x78, 0x01, 0x02, 0x00, 0x80, 0x00]),
        )

    def test_parse_dir_info_detects_trailing_bytes(self) -> None:
        """解码器自身能检出长度不自洽——保证上面的对拍有效。"""
        with self.assertRaises(AssertionError):
            parse_dir_info(_build_dir_info({"/a": b"x"}, "/") + b"\x00")


# ----------------------------------------------------------------------
# 3. 0x38 ReadDir 正响应
# ----------------------------------------------------------------------


class TestRequestFileTransferReadDir(FileTransferTestCase):

    def _expected(self, dir_info_length: int) -> bytes:
        return bytes([
            POS_REQUEST_FILE_TRANSFER,
            FILE_MODE_READ_DIR,
            FILE_LENGTH_FORMAT_IDENTIFIER,
            0x00, 0x80,
            FILE_DFI,
            0x00, 0x02,
        ]) + dir_info_length.to_bytes(2, "big")

    def test_root_exact_bytes(self) -> None:
        response = self.send(read_dir("/"))

        self.assertEqual(response, self._expected(32))
        self.assertEqual(
            response, bytes([0x78, 0x05, 0x02, 0x00, 0x80, 0x00, 0x00, 0x02, 0x00, 0x20]))

    def test_data_directory_exact_bytes(self) -> None:
        info = _build_dir_info(FILE_DATABASE, "/data/")

        self.assertEqual(self.send(read_dir("/data/")), self._expected(len(info)))

    def test_response_length_is_exactly_10(self) -> None:
        for path in ("/", "/boot/", "/logs/", "/data/", "/data/sub/"):
            with self.subTest(path=path):
                response = handle_uds_request(read_dir(path), EcuState())
                self.assertEqual(len(response), 10)

    def test_response_never_carries_dir_info_body(self) -> None:
        """响应只有 8 字节头部 + 2 字节长度——绝不携带 dirInfo 本体。"""
        info = _build_dir_info(FILE_DATABASE, "/")

        response = self.send(read_dir("/"))

        self.assertEqual(len(response), 10)
        self.assertNotIn(info, response)
        self.assertNotIn(b"boot", response)
        self.assertNotIn(b"data", response)
        self.assertNotIn(b"logs", response)

    def test_dir_info_length_field_matches_builder(self) -> None:
        """dirInfoLength == len(_build_dir_info(...)) == upload_size。"""
        for path in ("/", "/boot/", "/logs/", "/data/", "/data/sub/"):
            with self.subTest(path=path):
                state = EcuState()
                response = handle_uds_request(read_dir(path), state)
                expected = len(_build_dir_info(FILE_DATABASE, path))

                self.assertEqual(response[-2:], expected.to_bytes(2, "big"))
                self.assertEqual(state.upload_size, expected)
                self.assertEqual(
                    int.from_bytes(response[-2:], "big"), state.upload_size)

    def test_37_byte_example_matches_frozen_contract(self) -> None:
        """冻结 contract 的示例：dirInfo = 37 → ... 00 02 00 25。"""
        self.state.files = {"/version.txt": b"x", "/firmware.bin": b"y"}

        info = _build_dir_info(self.state.files, "/")
        self.assertEqual(len(info), 37)

        self.assertEqual(
            self.send(read_dir("/")),
            bytes([0x78, 0x05, 0x02, 0x00, 0x80, 0x00, 0x00, 0x02, 0x00, 0x25]),
        )

    def test_establishes_real_upload_transfer(self) -> None:
        """ReadDir 必须建立真正的 upload_active，而不是只回响应。"""
        info = _build_dir_info(FILE_DATABASE, "/data/")

        self.send(read_dir("/data/"))

        self.assertTrue(self.state.upload_active)
        self.assertEqual(self.state.upload_address, 0)
        self.assertEqual(self.state.upload_size, len(info))
        self.assertEqual(self.state.upload_expected_block, 1)
        self.assertEqual(self.state.upload_sent, 0)
        self.assertEqual(self.state.file_mode, FILE_MODE_READ_DIR)
        self.assertEqual(self.state.file_path, "/data/")

    def test_does_not_cache_dir_info(self) -> None:
        """ReadDir 不保存 dirInfo 副本：只有 file_mode / file_path / upload_size。"""
        self.send(read_dir("/"))

        for forbidden in ("dir_buffer", "dir_info", "upload_last_block",
                          "upload_last_response"):
            with self.subTest(attribute=forbidden):
                self.assertFalse(hasattr(self.state, forbidden))

    def test_empty_root_directory(self) -> None:
        """空目录合法：count = 0 → dirInfoLength = 2。"""
        self.state.files = {}

        self.assertEqual(
            self.send(read_dir("/")),
            bytes([0x78, 0x05, 0x02, 0x00, 0x80, 0x00, 0x00, 0x02, 0x00, 0x02]),
        )

    def test_directories_are_not_files(self) -> None:
        """隐式目录不是文件：ReadFile 目录路径 → 0x31。"""
        self.assertEqual(
            self.send(read_file("/data")),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_missing_directory(self) -> None:
        self.assertEqual(
            self.send(read_dir("/nope/")),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_dir_info_over_255_rejected(self) -> None:
        self.state.files = {f"/f{i:02d}": b"x" for i in range(29)}

        self.assertEqual(len(_build_dir_info(self.state.files, "/")), 263)
        self.assertEqual(
            self.send(read_dir("/")),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_dir_info_255_accepted(self) -> None:
        """边界：dirInfo 恰好 255 字节必须**接受**（dirInfoLength = 0x00FF）。"""
        # 2 + n * (6 + L) = 255，取 n = 23、L = 5 → 2 + 253 = 255
        self.state.files = {f"/{index:05d}": b"x" for index in range(23)}

        self.assertEqual(len(_build_dir_info(self.state.files, "/")), 255)
        self.assertEqual(
            self.send(read_dir("/")),
            bytes([0x78, 0x05, 0x02, 0x00, 0x80, 0x00, 0x00, 0x02, 0x00, 0xFF]),
        )
        self.assertEqual(self.state.upload_size, 255)


# ----------------------------------------------------------------------
# 4. 0x38 ReadFile 正响应
# ----------------------------------------------------------------------


class TestRequestFileTransferReadFile(FileTransferTestCase):

    def test_exact_bytes_config_bin(self) -> None:
        size = len(FILE_DATABASE["/data/config.bin"])

        self.assertEqual(
            self.send(read_file("/data/config.bin")),
            bytes([0x78, 0x04, 0x02, 0x00, 0x80, 0x00, 0x00, 0x04])
            + size.to_bytes(4, "big") + size.to_bytes(4, "big"),
        )

    def test_response_length_is_exactly_16(self) -> None:
        self.assertEqual(len(self.send(read_file("/data/config.bin"))), 16)

    def test_size_fields_are_byte_length(self) -> None:
        self.assertEqual(
            self.send(read_file("/logs/last.log"))[-8:],
            (300).to_bytes(4, "big") * 2,
        )

    def test_uncompressed_equals_compressed(self) -> None:
        response = self.send(read_file("/logs/last.log"))

        self.assertEqual(response[-8:-4], response[-4:])

    def test_establishes_upload_state(self) -> None:
        self.send(read_file("/data/config.bin"))

        self.assertTrue(self.state.upload_active)
        self.assertEqual(self.state.upload_size, 130)
        self.assertEqual(self.state.upload_expected_block, 1)
        self.assertEqual(self.state.upload_sent, 0)
        self.assertEqual(self.state.upload_address, 0)
        self.assertEqual(self.state.file_mode, FILE_MODE_READ_FILE)
        self.assertEqual(self.state.file_path, "/data/config.bin")

    def test_bad_dfi(self) -> None:
        self.assertEqual(
            self.send(read_file("/data/config.bin", dfi=0x01)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_missing_file(self) -> None:
        self.assertEqual(
            self.send(read_file("/nope.bin")),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_extra_field_rejected(self) -> None:
        """ReadFile 不允许携带 size 字段：多余字段 → 0x13。"""
        self.assertEqual(
            self.send(read_file("/data/config.bin") + b"\x00"),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_missing_dfi_rejected(self) -> None:
        raw = b"/data/config.bin"
        request = (bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_READ_FILE])
                   + bytes([0x00, len(raw)]) + raw)

        self.assertEqual(
            self.send(request),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_INCORRECT_MESSAGE_LENGTH]),
        )


# ----------------------------------------------------------------------
# 5. 0x38 AddFile / ReplaceFile / DeleteFile 正响应
# ----------------------------------------------------------------------


class TestRequestFileTransferWrite(FileTransferTestCase):

    def test_add_file_exact_bytes(self) -> None:
        self.assertEqual(
            self.send(add_file("/new.bin", 4)),
            bytes([0x78, 0x01, 0x02, 0x00, 0x80, 0x00]),
        )

    def test_replace_file_exact_bytes(self) -> None:
        self.assertEqual(
            self.send(replace_file("/data/config.bin", 4)),
            bytes([0x78, 0x03, 0x02, 0x00, 0x80, 0x00]),
        )

    def test_delete_file_exact_bytes(self) -> None:
        self.assertEqual(self.send(delete_file("/data/sub/a.txt")), bytes([0x78, 0x02]))

    def test_add_file_establishes_download(self) -> None:
        self.send(add_file("/new.bin", 8))

        self.assertTrue(self.state.download_active)
        self.assertEqual(self.state.download_address, 0)
        self.assertEqual(self.state.download_size, 8)
        self.assertEqual(self.state.download_expected_block, 1)
        self.assertEqual(bytes(self.state.download_buffer), b"")
        self.assertEqual(self.state.download_last_block_request, None)
        self.assertEqual(self.state.file_mode, FILE_MODE_ADD_FILE)
        self.assertEqual(self.state.file_path, "/new.bin")

    def test_replace_file_mode_is_three(self) -> None:
        self.send(replace_file("/data/config.bin", 130))

        self.assertEqual(self.state.file_mode, FILE_MODE_REPLACE_FILE)
        self.assertEqual(self.state.file_path, "/data/config.bin")

    def test_add_existing_file_rejected(self) -> None:
        self.assertEqual(
            self.send(add_file("/data/config.bin", 4)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_add_existing_directory_rejected(self) -> None:
        """文件名与目录名共享命名空间：已存在的隐式目录也拒绝。"""
        self.assertEqual(
            self.send(add_file("/data", 4)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_add_under_existing_file_rejected(self) -> None:
        """祖先已是文件 → 0x22：否则该祖先会同时是文件与隐式目录。"""
        self.state.files = {"/a": b"x"}

        self.assertEqual(
            self.send(add_file("/a/b", 4)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertEqual(self.state.files, {"/a": b"x"})

    def test_add_under_implicit_directory_allowed(self) -> None:
        """隐式目录（不是文件）作为祖先是合法的。"""
        self.assertEqual(
            self.send(add_file("/data/new.txt", 4)),
            bytes([0x78, 0x01, 0x02, 0x00, 0x80, 0x00]),
        )

    def test_add_under_nested_implicit_directory_allowed(self) -> None:
        self.assertEqual(
            self.send(add_file("/data/sub/deep.txt", 4)),
            bytes([0x78, 0x01, 0x02, 0x00, 0x80, 0x00]),
        )

    def test_add_under_file_created_by_add_file_rejected(self) -> None:
        """协议级完整路径：先建 /a，再在其下建 /a/b 必须被拒。"""
        self.send(add_file("/a", 1))
        self.send(transfer_data(0x01, b"A"))
        self.send(transfer_exit())

        self.assertEqual(self.state.files["/a"], b"A")
        self.assertEqual(
            self.send(add_file("/a/b", 1)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertNotIn("/a/b", self.state.files)

    def test_replace_missing_file_rejected(self) -> None:
        self.assertEqual(
            self.send(replace_file("/nope.bin", 4)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_replace_directory_rejected(self) -> None:
        self.assertEqual(
            self.send(replace_file("/data", 4)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_delete_directory_rejected(self) -> None:
        """本 feature 没有 rmdir：目录路径一律 0x31。"""
        self.assertEqual(
            self.send(delete_file("/data")),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_delete_missing_file_rejected(self) -> None:
        self.assertEqual(
            self.send(delete_file("/nope.bin")),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_bad_dfi_rejected(self) -> None:
        self.assertEqual(
            self.send(add_file("/new.bin", 4, dfi=0x01)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_bad_file_size_parameter_length_rejected(self) -> None:
        self.assertEqual(
            self.send(add_file("/new.bin", 4, fsp=0x02)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_zero_size_rejected(self) -> None:
        self.assertEqual(
            self.send(add_file("/new.bin", 0)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_zero_compressed_size_rejected(self) -> None:
        self.assertEqual(
            self.send(add_file("/new.bin", 4, compressed=0)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_compressed_not_equal_uncompressed_rejected(self) -> None:
        self.assertEqual(
            self.send(add_file("/new.bin", 4, compressed=5)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_size_above_max_rejected(self) -> None:
        self.assertEqual(
            self.send(add_file("/new.bin", MAX_FILE_SIZE + 1)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_size_equal_max_accepted(self) -> None:
        response = self.send(add_file("/new.bin", MAX_FILE_SIZE))

        self.assertEqual(response, bytes([0x78, 0x01, 0x02, 0x00, 0x80, 0x00]))
        self.assertEqual(self.state.download_size, MAX_FILE_SIZE)

    def test_write_modes_reject_missing_size_fields(self) -> None:
        request = add_file("/new.bin", 4)[:-1]

        self.assertEqual(
            self.send(request),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_delete_rejects_extra_fields(self) -> None:
        self.assertEqual(
            self.send(delete_file("/data/sub/a.txt") + b"\x00"),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_INCORRECT_MESSAGE_LENGTH]),
        )


# ----------------------------------------------------------------------
# 6. NRC 优先级
# ----------------------------------------------------------------------


class TestFileTransferNrcPrecedence(FileTransferTestCase):

    def test_mode_zero_is_out_of_range_not_subfunction(self) -> None:
        """modeOfOperation 不是 subfunction：非法 mode → 0x31，不是 0x12。"""
        request = bytes([SID_REQUEST_FILE_TRANSFER, 0x00, 0x00, 0x01]) + b"/"

        self.assertEqual(
            self.send(request),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_unsupported_modes_are_out_of_range(self) -> None:
        for mode in (0x00, 0x06, 0x07, 0xFF):
            with self.subTest(mode=mode):
                request = bytes([SID_REQUEST_FILE_TRANSFER, mode, 0x00, 0x01]) + b"/"
                self.assertEqual(
                    self.send(request),
                    bytes([0x7F, SID_REQUEST_FILE_TRANSFER,
                           NRC_REQUEST_OUT_OF_RANGE]),
                )

    def test_too_short_for_path_length(self) -> None:
        for request in (bytes([SID_REQUEST_FILE_TRANSFER]),
                        bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_READ_DIR]),
                        bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_READ_DIR, 0x00])):
            with self.subTest(length=len(request)):
                self.assertEqual(
                    self.send(request),
                    bytes([0x7F, SID_REQUEST_FILE_TRANSFER,
                           NRC_INCORRECT_MESSAGE_LENGTH]),
                )

    def test_length_precedes_mode_check(self) -> None:
        """步骤 1 先于步骤 2：太短时即使是非法 mode 也回 0x13。"""
        self.assertEqual(
            self.send(bytes([SID_REQUEST_FILE_TRANSFER, 0xFF, 0x00])),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_path_length_beyond_payload(self) -> None:
        request = bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_READ_DIR, 0x00, 0x05]) + b"/"

        self.assertEqual(
            self.send(request),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_path_length_precedes_path_validity(self) -> None:
        """步骤 3 先于步骤 4：截断的 path 是 0x13，不是 0x31。"""
        request = bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_READ_DIR, 0x00, 0x40])

        self.assertEqual(
            self.send(request),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_invalid_paths_are_out_of_range(self) -> None:
        cases = (
            ("no leading slash", FILE_MODE_READ_DIR, b"data/"),
            ("empty path", FILE_MODE_DELETE_FILE, b""),
            ("empty path readdir", FILE_MODE_READ_DIR, b""),
            ("delete trailing slash", FILE_MODE_DELETE_FILE, b"/data/"),
            ("readfile trailing slash", FILE_MODE_READ_FILE, b"/data/"),
            ("replace trailing slash", FILE_MODE_REPLACE_FILE, b"/data/"),
            ("readdir without trailing slash", FILE_MODE_READ_DIR, b"/data"),
            ("slash only as file", FILE_MODE_DELETE_FILE, b"/"),
            ("control char", FILE_MODE_READ_DIR, b"/da\x01ta/"),
            ("delete char", FILE_MODE_READ_DIR, b"/da\x7fta/"),
            ("non-ascii", FILE_MODE_READ_DIR, b"/da\xffta/"),
            ("too long", FILE_MODE_READ_DIR, b"/" + b"a" * 64 + b"/"),
        )

        for label, mode, raw in cases:
            with self.subTest(case=label):
                request = (bytes([SID_REQUEST_FILE_TRANSFER, mode])
                           + bytes([(len(raw) >> 8) & 0xFF, len(raw) & 0xFF]) + raw)
                self.assertEqual(
                    self.send(request),
                    bytes([0x7F, SID_REQUEST_FILE_TRANSFER,
                           NRC_REQUEST_OUT_OF_RANGE]),
                )

    def test_space_is_a_printable_path_character(self) -> None:
        """0x20 属于"可打印 ASCII"，是合法路径字符（本模型约束）。"""
        self.state.files = {"/da ta/a b.txt": b"hi"}

        self.assertEqual(
            self.send(read_file("/da ta/a b.txt")),
            bytes([0x78, 0x04, 0x02, 0x00, 0x80, 0x00, 0x00, 0x04])
            + (2).to_bytes(4, "big") * 2,
        )

    def test_path_at_length_limit_is_checked(self) -> None:
        """64 字节路径通过长度检查（此处落到"路径不存在"0x31）。"""
        raw = b"/" + b"a" * 62 + b"/"

        self.assertEqual(len(raw), 64)
        self.assertEqual(
            self.send(bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_READ_DIR, 0x00, 0x40])
                      + raw),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_busy_slot_precedes_file_state(self) -> None:
        """步骤 7 先于步骤 8：slot 忙时即使路径不存在也回 0x22。"""
        self.send(download_request())

        response = self.send(read_dir("/nope/"))

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_value_checks_precede_slot_check(self) -> None:
        """步骤 6 先于步骤 7：坏 DFI 即使 slot 忙也回 0x31。"""
        self.send(download_request())

        self.assertEqual(
            self.send(add_file("/new.bin", 4, dfi=0x01)),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_all_nrc_paths_leave_state_untouched(self) -> None:
        before = self.file_snapshot()
        files_before = dict(self.state.files)

        self.send(bytes([SID_REQUEST_FILE_TRANSFER, 0xFF, 0x00, 0x01]) + b"/")
        self.send(bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_READ_DIR, 0x00, 0x00]))
        self.send(bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_READ_DIR, 0x00, 0x04]) + b"/bad")
        self.send(read_dir("/nope/"))
        self.send(read_file("/nope.bin"))
        self.send(delete_file("/nope.bin"))
        self.send(add_file("/data/config.bin", 4))
        self.send(replace_file("/nope.bin", 4))
        self.send(add_file("/new.bin", 4, dfi=0x01))

        self.assertEqual(self.file_snapshot(), before)
        self.assertEqual(self.state.files, files_before)


# ----------------------------------------------------------------------
# 7. ReadDir / ReadFile 的 0x36 / 0x37 传输
# ----------------------------------------------------------------------


class TestReadDirTransfer(FileTransferTestCase):

    def test_single_block_dir_info(self) -> None:
        self.send(read_dir("/data/"))
        info = _build_dir_info(FILE_DATABASE, "/data/")

        self.assertEqual(
            self.send(transfer_data(0x01)),
            bytes([POS_TRANSFER_DATA, 0x01]) + info,
        )
        self.assertEqual(self.state.upload_sent, len(info))

    def test_multiblock_dir_info(self) -> None:
        self.state.files = {f"/f{i:02d}": b"x" for i in range(20)}
        info = _build_dir_info(self.state.files, "/")

        self.assertEqual(len(info), 182)
        self.send(read_dir("/"))

        self.assertEqual(self.send(transfer_data(0x01)),
                         bytes([POS_TRANSFER_DATA, 0x01]) + info[:_CHUNK])
        self.assertEqual(self.state.upload_sent, _CHUNK)
        self.assertEqual(self.send(transfer_data(0x02)),
                         bytes([POS_TRANSFER_DATA, 0x02]) + info[_CHUNK:])
        self.assertEqual(self.state.upload_sent, len(info))

    def test_duplicate_is_byte_identical(self) -> None:
        """ReadDir 不缓存 dirInfo，duplicate 必须确定性重建同样的字节。"""
        self.send(read_dir("/"))

        first = self.send(transfer_data(0x01))
        after = self.upload_snapshot()

        for _ in range(3):
            self.assertEqual(self.send(transfer_data(0x01)), first)

        self.assertEqual(self.upload_snapshot(), after)

    def test_duplicate_matches_freshly_built_dir_info(self) -> None:
        self.state.files = {f"/f{i:02d}": b"x" for i in range(20)}
        info = _build_dir_info(self.state.files, "/")
        self.send(read_dir("/"))

        self.send(transfer_data(0x01))
        self.send(transfer_data(0x02))

        # 尾块 duplicate 与源逐字节一致
        self.assertEqual(
            self.send(transfer_data(0x02)),
            bytes([POS_TRANSFER_DATA, 0x02]) + info[_CHUNK:],
        )

    def test_initial_36_00_is_wrong_bsc(self) -> None:
        self.send(read_dir("/"))

        self.assertEqual(
            self.send(transfer_data(0x00)),
            bytes([0x7F, SID_TRANSFER_DATA, NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )

    def test_wrong_bsc_jump(self) -> None:
        self.send(read_dir("/"))

        self.assertEqual(
            self.send(transfer_data(0x05)),
            bytes([0x7F, SID_TRANSFER_DATA, NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )

    def test_wrong_bsc_backward(self) -> None:
        self.state.files = {f"/f{i:02d}": b"x" for i in range(20)}
        self.send(read_dir("/"))
        self.send(transfer_data(0x01))
        self.send(transfer_data(0x02))
        before = self.upload_snapshot()

        self.assertEqual(
            self.send(transfer_data(0x01)),
            bytes([0x7F, SID_TRANSFER_DATA, NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )
        self.assertEqual(self.upload_snapshot(), before)

    def test_data_bearing_request_rejected(self) -> None:
        self.send(read_dir("/"))

        self.assertEqual(
            self.send(transfer_data(0x01, b"\xAA")),
            bytes([0x7F, SID_TRANSFER_DATA, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_incomplete_exit_then_complete(self) -> None:
        self.state.files = {f"/f{i:02d}": b"x" for i in range(20)}
        self.send(read_dir("/"))
        self.send(transfer_data(0x01))

        self.assertEqual(
            self.send(transfer_exit()),
            bytes([0x7F, SID_REQUEST_TRANSFER_EXIT, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertTrue(self.state.upload_active)

        self.send(transfer_data(0x02))
        self.assertEqual(self.send(transfer_exit()),
                         bytes([POS_REQUEST_TRANSFER_EXIT]))

    def test_complete_exit_clears_everything(self) -> None:
        self.send(read_dir("/data/"))
        self.send(transfer_data(0x01))

        self.assertEqual(self.send(transfer_exit()),
                         bytes([POS_REQUEST_TRANSFER_EXIT]))

        self.assertFalse(self.state.upload_active)
        self.assertEqual(self.state.upload_address, 0)
        self.assertEqual(self.state.upload_size, 0)
        self.assertEqual(self.state.upload_expected_block, 0)
        self.assertEqual(self.state.upload_sent, 0)
        self.assertEqual(self.state.file_mode, FILE_MODE_IDLE)
        self.assertEqual(self.state.file_path, "")

    def test_after_complete_36_and_37_are_24(self) -> None:
        self.send(read_dir("/data/"))
        self.send(transfer_data(0x01))
        self.send(transfer_exit())

        self.assertEqual(
            self.send(transfer_data(0x01)),
            bytes([0x7F, SID_TRANSFER_DATA, NRC_REQUEST_SEQUENCE_ERROR]),
        )
        self.assertEqual(
            self.send(transfer_exit()),
            bytes([0x7F, SID_REQUEST_TRANSFER_EXIT, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_read_dir_never_modifies_files(self) -> None:
        before = dict(self.state.files)

        self.send(read_dir("/data/"))
        self.send(transfer_data(0x01))
        self.send(transfer_exit())

        self.assertEqual(self.state.files, before)


class TestReadFileTransfer(FileTransferTestCase):

    def test_single_block_file(self) -> None:
        content = FILE_DATABASE["/data/sub/a.txt"]
        self.send(read_file("/data/sub/a.txt"))

        self.assertEqual(
            self.send(transfer_data(0x01)),
            bytes([POS_TRANSFER_DATA, 0x01]) + content,
        )

    def test_multiblock_file(self) -> None:
        """300 字节 → 126 + 126 + 48 三块。"""
        content = FILE_DATABASE["/logs/last.log"]
        self.send(read_file("/logs/last.log"))

        self.assertEqual(self.send(transfer_data(0x01)),
                         bytes([POS_TRANSFER_DATA, 0x01]) + content[:_CHUNK])
        self.assertEqual(self.state.upload_sent, _CHUNK)
        self.assertEqual(self.send(transfer_data(0x02)),
                         bytes([POS_TRANSFER_DATA, 0x02]) + content[_CHUNK:2 * _CHUNK])
        self.assertEqual(self.state.upload_sent, 2 * _CHUNK)
        self.assertEqual(self.send(transfer_data(0x03)),
                         bytes([POS_TRANSFER_DATA, 0x03]) + content[2 * _CHUNK:])
        self.assertEqual(self.state.upload_sent, 300)

    def test_duplicate_byte_identical(self) -> None:
        self.send(read_file("/logs/last.log"))

        first = self.send(transfer_data(0x01))
        after = self.upload_snapshot()

        self.assertEqual(self.send(transfer_data(0x01)), first)
        self.assertEqual(self.upload_snapshot(), after)

    def test_duplicate_of_tail_block(self) -> None:
        content = FILE_DATABASE["/logs/last.log"]
        self.send(read_file("/logs/last.log"))
        self.send(transfer_data(0x01))
        self.send(transfer_data(0x02))
        tail = self.send(transfer_data(0x03))

        self.assertEqual(
            tail, bytes([POS_TRANSFER_DATA, 0x03]) + content[2 * _CHUNK:])
        self.assertEqual(self.send(transfer_data(0x03)), tail)

    def test_complete_exit(self) -> None:
        self.send(read_file("/data/config.bin"))
        self.send(transfer_data(0x01))
        self.send(transfer_data(0x02))

        self.assertEqual(self.send(transfer_exit()),
                         bytes([POS_REQUEST_TRANSFER_EXIT]))
        self.assertEqual(self.state.file_mode, FILE_MODE_IDLE)
        self.assertFalse(self.state.upload_active)

    def test_full_32130_file(self) -> None:
        """255 块边界：BSC 01..FF 走完即 sent == size。"""
        self.state.files["/big.bin"] = (bytes(range(256)) * 126)[:MAX_FILE_SIZE]
        self.send(read_file("/big.bin"))

        sent = 0
        for counter in range(1, 256):
            response = self.send(transfer_data(counter & 0xFF))
            self.assertEqual(response[:2], bytes([POS_TRANSFER_DATA, counter & 0xFF]))
            sent += len(response) - 2

        self.assertEqual(sent, MAX_FILE_SIZE)
        self.assertEqual(self.state.upload_sent, MAX_FILE_SIZE)
        self.assertEqual(self.send(transfer_exit()),
                         bytes([POS_REQUEST_TRANSFER_EXIT]))

    def test_source_is_file_not_upload_pattern(self) -> None:
        """ReadFile 的源必须是文件内容，不能退化成 _upload_chunk 循环模式。"""
        self.send(read_file("/data/sub/a.txt"))
        response = self.send(transfer_data(0x01))

        self.assertEqual(response[2:], FILE_DATABASE["/data/sub/a.txt"])
        self.assertNotEqual(response[2:], _upload_chunk(0, len(response) - 2))

    def test_large_file_does_not_use_upload_source(self) -> None:
        self.send(read_file("/logs/last.log"))
        response = self.send(transfer_data(0x01))

        self.assertEqual(response[2:], FILE_DATABASE["/logs/last.log"][:126])
        self.assertNotEqual(response[2:], _upload_chunk(0, 126))


# ----------------------------------------------------------------------
# 8. AddFile / ReplaceFile 的 0x36 / 0x37 传输
# ----------------------------------------------------------------------


class TestWriteFileTransfer(FileTransferTestCase):

    def test_commit_only_at_exit(self) -> None:
        body = b"N" * 8
        self.send(add_file("/new.bin", len(body)))

        self.assertNotIn("/new.bin", self.state.files)

        self.assertEqual(self.send(transfer_data(0x01, body)),
                         bytes([POS_TRANSFER_DATA, 0x01]))

        # 数据已在 buffer 里，但**尚未**提交
        self.assertNotIn("/new.bin", self.state.files)
        self.assertEqual(bytes(self.state.download_buffer), body)

        self.assertEqual(self.send(transfer_exit()),
                         bytes([POS_REQUEST_TRANSFER_EXIT]))
        self.assertEqual(self.state.files["/new.bin"], body)

    def test_complete_exit_clears_download_fields(self) -> None:
        body = b"M" * 4
        self.send(add_file("/new.bin", len(body)))
        self.send(transfer_data(0x01, body))
        self.send(transfer_exit())

        self.assertFalse(self.state.download_active)
        self.assertEqual(self.state.download_address, 0)
        self.assertEqual(self.state.download_size, 0)
        self.assertEqual(self.state.download_expected_block, 0)
        self.assertEqual(bytes(self.state.download_buffer), b"")
        self.assertEqual(self.state.download_last_block_request, None)
        self.assertEqual(self.state.file_mode, FILE_MODE_IDLE)
        self.assertEqual(self.state.file_path, "")

    def test_download_last_block_request_is_none_until_block(self) -> None:
        self.send(add_file("/new.bin", 4))

        self.assertEqual(self.state.download_last_block_request, None)

    def test_download_last_block_request_cleared_after_exit(self) -> None:
        self.send(add_file("/new.bin", 4))
        self.send(transfer_data(0x01, b"ABCD"))

        self.assertEqual(self.state.download_last_block_request,
                         bytes([SID_TRANSFER_DATA, 0x01]) + b"ABCD")

        self.send(transfer_exit())

        self.assertEqual(self.state.download_last_block_request, None)

    def test_multiblock_write(self) -> None:
        body = bytes(range(256))          # 256 字节 → 126 + 126 + 4 三块
        self.send(add_file("/big.bin", len(body)))

        first = self.send(transfer_data(0x01, body[:_CHUNK]))
        self.assertEqual(first, bytes([POS_TRANSFER_DATA, 0x01]))
        self.assertEqual(self.state.download_expected_block, 2)

        second = self.send(transfer_data(0x02, body[_CHUNK:2 * _CHUNK]))
        self.assertEqual(second, bytes([POS_TRANSFER_DATA, 0x02]))

        third = self.send(transfer_data(0x03, body[2 * _CHUNK:]))
        self.assertEqual(third, bytes([POS_TRANSFER_DATA, 0x03]))

        self.assertEqual(len(self.state.download_buffer), len(body))
        self.send(transfer_exit())

        self.assertEqual(self.state.files["/big.bin"], body)

    def test_duplicate_block_is_idempotent(self) -> None:
        self.send(add_file("/new.bin", 8))
        first = self.send(transfer_data(0x01, b"AAAAAAAA"))
        before = self.download_snapshot()

        self.assertEqual(self.send(transfer_data(0x01, b"AAAAAAAA")), first)
        self.assertEqual(self.download_snapshot(), before)

    def test_wrong_bsc(self) -> None:
        self.send(add_file("/new.bin", 8))

        self.assertEqual(
            self.send(transfer_data(0x05, b"A" * 8)),
            bytes([0x7F, SID_TRANSFER_DATA, NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )

    def test_incomplete_exit_is_conditions_not_correct(self) -> None:
        self.send(add_file("/new.bin", 8))

        self.assertEqual(
            self.send(transfer_exit()),
            bytes([0x7F, SID_REQUEST_TRANSFER_EXIT, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertTrue(self.state.download_active)
        self.assertNotIn("/new.bin", self.state.files)

    def test_partial_write_then_exit_does_not_create_file(self) -> None:
        self.send(add_file("/new.bin", 10))
        self.send(transfer_data(0x01, b"12345"))

        self.assertEqual(
            self.send(transfer_exit()),
            bytes([0x7F, SID_REQUEST_TRANSFER_EXIT, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertNotIn("/new.bin", self.state.files)

    def test_oversized_block_rejected(self) -> None:
        self.send(add_file("/new.bin", 4))

        self.assertEqual(
            self.send(transfer_data(0x01, b"12345")),
            bytes([0x7F, SID_TRANSFER_DATA, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_replace_commits_new_content(self) -> None:
        original = self.state.files["/boot/version.txt"]
        body = b"replacement-content"

        self.send(replace_file("/boot/version.txt", len(body)))
        self.assertEqual(self.state.files["/boot/version.txt"], original)

        self.send(transfer_data(0x01, body))
        self.assertEqual(self.state.files["/boot/version.txt"], original)

        self.send(transfer_exit())
        self.assertEqual(self.state.files["/boot/version.txt"], body)

    def test_abort_by_reset_does_not_create_file(self) -> None:
        self.send(add_file("/new.bin", 8))
        self.send(transfer_data(0x01, b"A" * 8))

        self.assertEqual(handle_uds_request(bytes([SID_ECU_RESET, 0x01]), self.state),
                         bytes([0x51, 0x01]))

        self.assertNotIn("/new.bin", self.state.files)
        self.assertEqual(self.state.file_mode, FILE_MODE_IDLE)
        self.assertEqual(self.state.file_path, "")
        self.assertFalse(self.state.download_active)
        self.assertEqual(bytes(self.state.download_buffer), b"")
        self.assertEqual(self.state.download_last_block_request, None)

    def test_abort_by_reset_does_not_overwrite_file(self) -> None:
        original = self.state.files["/data/config.bin"]
        self.send(replace_file("/data/config.bin", 8))
        self.send(transfer_data(0x01, b"X" * 8))

        handle_uds_request(bytes([SID_ECU_RESET, 0x01]), self.state)

        self.assertEqual(self.state.files["/data/config.bin"], original)

    def test_abort_then_34_is_usable(self) -> None:
        self.send(add_file("/new.bin", 8))
        self.send(transfer_data(0x01, b"A" * 8))
        handle_uds_request(bytes([SID_ECU_RESET, 0x01]), self.state)

        self.assertEqual(self.send(download_request(size=4)),
                         bytes([0x74, 0x20, 0x00, 0x80]))

    def test_plain_34_download_does_not_commit_any_file(self) -> None:
        """既有 0x34 内存下载（file_mode == 0）绝不写入 files。"""
        before = dict(self.state.files)
        self.send(download_request(size=4))
        self.send(transfer_data(0x01, b"ABCD"))
        self.send(transfer_exit())

        self.assertEqual(self.state.files, before)

    def test_full_size_write_commits_exact_bytes(self) -> None:
        body = bytes(range(200))
        self.send(add_file("/exact.bin", 200))
        self.send(transfer_data(0x01, body[:_CHUNK]))
        self.send(transfer_data(0x02, body[_CHUNK:]))
        self.send(transfer_exit())

        self.assertEqual(self.state.files["/exact.bin"], body)


# ----------------------------------------------------------------------
# 9. DeleteFile 语义
# ----------------------------------------------------------------------


class TestDeleteFile(FileTransferTestCase):

    def test_deletes_file(self) -> None:
        self.send(delete_file("/data/sub/a.txt"))

        self.assertNotIn("/data/sub/a.txt", self.state.files)

    def test_establishes_no_transfer(self) -> None:
        self.send(delete_file("/data/sub/a.txt"))

        self.assertFalse(self.state.upload_active)
        self.assertFalse(self.state.download_active)
        self.assertEqual(self.state.file_mode, FILE_MODE_IDLE)
        self.assertEqual(self.state.file_path, "")
        self.assertEqual(self.upload_snapshot(),
                         (False, 0, 0, 0, 0))
        self.assertEqual(self.state.download_last_block_request, None)

    def test_is_one_shot_and_idle_immediately(self) -> None:
        self.send(delete_file("/data/sub/a.txt"))

        self.assertEqual(
            self.send(transfer_data(0x01)),
            bytes([0x7F, SID_TRANSFER_DATA, NRC_REQUEST_SEQUENCE_ERROR]),
        )
        self.assertEqual(
            self.send(transfer_exit()),
            bytes([0x7F, SID_REQUEST_TRANSFER_EXIT, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_34_and_35_usable_right_after(self) -> None:
        self.send(delete_file("/data/sub/a.txt"))

        self.assertEqual(self.send(download_request(size=4)),
                         bytes([0x74, 0x20, 0x00, 0x80]))
        self.assertEqual(handle_uds_request(upload_request(130), EcuState()),
                         bytes([0x75, 0x20, 0x00, 0x80]))

    def test_implicit_directory_disappears(self) -> None:
        """目录下文件删净后该隐式目录消失（不需要任何清理逻辑）。"""
        self.assertTrue(_dir_exists(self.state.files, "/data/sub/"))

        self.send(delete_file("/data/sub/a.txt"))

        self.assertFalse(_dir_exists(self.state.files, "/data/sub/"))
        self.assertEqual(
            self.send(read_dir("/data/sub/")),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_root_is_always_a_directory(self) -> None:
        """全部删净后 "/" 仍是合法目录，且 dirInfo = count 0 = 2 bytes。"""
        for path in ("/boot/version.txt", "/logs/last.log",
                     "/data/config.bin", "/data/sub/a.txt"):
            self.send(delete_file(path))

        self.assertEqual(self.state.files, {})
        self.assertTrue(_dir_exists(self.state.files, "/"))
        self.assertEqual(
            self.send(read_dir("/")),
            bytes([0x78, 0x05, 0x02, 0x00, 0x80, 0x00, 0x00, 0x02, 0x00, 0x02]),
        )

    def test_deleted_file_can_be_added_again(self) -> None:
        self.send(delete_file("/data/sub/a.txt"))

        self.assertEqual(self.send(add_file("/data/sub/a.txt", 3)),
                         bytes([0x78, 0x01, 0x02, 0x00, 0x80, 0x00]))


# ----------------------------------------------------------------------
# 10. 8 向互斥矩阵
# ----------------------------------------------------------------------


class TestMutualExclusion(FileTransferTestCase):
    """0x34/0x35 handler 零改动即满足全部 8 个方向：

    0x38 复用既有的 download_active / upload_active 会话槽。
    """

    def _request_38(self, mode: str) -> bytes:
        """构造一个**能成功建立** 0x38 会话的请求。"""
        if mode == "add":
            return add_file("/mm.bin", 4)
        if mode == "replace":
            return replace_file("/data/config.bin", 4)
        if mode == "readfile":
            return read_file("/data/config.bin")
        if mode == "readdir":
            return read_dir("/")
        raise AssertionError(mode)  # pragma: no cover

    def test_38_while_34_active(self) -> None:
        self.send(download_request())
        before = self.file_snapshot()

        self.assertEqual(
            self.send(read_dir("/")),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertEqual(self.file_snapshot(), before)

    def test_38_while_35_active(self) -> None:
        self.send(upload_request())
        before = self.file_snapshot()

        self.assertEqual(
            self.send(read_dir("/")),
            bytes([0x7F, SID_REQUEST_FILE_TRANSFER, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertEqual(self.file_snapshot(), before)

    def test_38_while_38_active(self) -> None:
        """任一 0x38 mode 在进行中时新的 0x38 都拒绝（16 个组合）。"""
        for active in ("add", "replace", "readfile", "readdir"):
            for incoming in ("add", "replace", "readfile", "readdir"):
                with self.subTest(active=active, incoming=incoming):
                    self.state = EcuState()
                    self.send(self._request_38(active))
                    before = self.file_snapshot()

                    response = self.send(self._request_38(incoming))

                    self.assertEqual(
                        response,
                        bytes([0x7F, SID_REQUEST_FILE_TRANSFER,
                               NRC_CONDITIONS_NOT_CORRECT]),
                    )
                    self.assertEqual(self.file_snapshot(), before)

    def test_38_active_blocks_34(self) -> None:
        for active in ("add", "replace", "readfile", "readdir"):
            with self.subTest(active=active):
                self.state = EcuState()
                self.send(self._request_38(active))
                before = self.download_snapshot()

                self.assertEqual(
                    self.send(download_request()),
                    bytes([0x7F, SID_REQUEST_DOWNLOAD,
                           NRC_CONDITIONS_NOT_CORRECT]),
                )
                self.assertEqual(self.download_snapshot(), before)

    def test_38_active_blocks_35(self) -> None:
        for active in ("add", "replace", "readfile", "readdir"):
            with self.subTest(active=active):
                self.state = EcuState()
                self.send(self._request_38(active))
                before = self.upload_snapshot()

                self.assertEqual(
                    self.send(upload_request()),
                    bytes([0x7F, SID_REQUEST_UPLOAD,
                           NRC_CONDITIONS_NOT_CORRECT]),
                )
                self.assertEqual(self.upload_snapshot(), before)

    def test_readdir_counts_as_active_for_34(self) -> None:
        """ReadDir 必须算 active transfer（不是"只读所以不占槽"）。"""
        self.send(read_dir("/"))

        self.assertEqual(
            self.send(download_request()),
            bytes([0x7F, SID_REQUEST_DOWNLOAD, NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_readfile_counts_as_active_for_35(self) -> None:
        self.send(read_file("/data/config.bin"))

        self.assertEqual(
            self.send(upload_request()),
            bytes([0x7F, SID_REQUEST_UPLOAD, NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_delete_file_does_not_block(self) -> None:
        """DeleteFile 是 one-shot：不占槽，因此 0x34 不被阻塞。"""
        self.send(delete_file("/data/sub/a.txt"))

        self.assertEqual(self.send(download_request(size=4)),
                         bytes([0x74, 0x20, 0x00, 0x80]))


# ----------------------------------------------------------------------
# 11. 生命周期
# ----------------------------------------------------------------------


class TestFileTransferLifecycle(FileTransferTestCase):

    def test_hard_reset_preserves_files(self) -> None:
        """hardReset 必须保留 files（与保留 DTC 同侧）。"""
        before = dict(self.state.files)

        handle_uds_request(bytes([SID_ECU_RESET, 0x01]), self.state)

        self.assertEqual(self.state.files, before)

    def test_hard_reset_preserves_added_files(self) -> None:
        self.send(add_file("/kept.bin", 4))
        self.send(transfer_data(0x01, b"KEEP"))
        self.send(transfer_exit())

        handle_uds_request(bytes([SID_ECU_RESET, 0x01]), self.state)

        self.assertEqual(self.state.files["/kept.bin"], b"KEEP")

    def test_hard_reset_clears_file_mode_and_path(self) -> None:
        self.send(read_dir("/"))

        handle_uds_request(bytes([SID_ECU_RESET, 0x01]), self.state)

        self.assertEqual(self.state.file_mode, FILE_MODE_IDLE)
        self.assertEqual(self.state.file_path, "")

    def test_hard_reset_clears_all_named_fields(self) -> None:
        """hardReset 的清理清单：upload 5 + download 6 + file 2 + routine + DID。"""
        self.send(add_file("/new.bin", 4))
        self.send(transfer_data(0x01, b"AB"))
        self.state.did_overrides[0xF190] = b"x"
        self.state.active_routines.add(0x2001)
        self.state.routine_start_counts[0x2001] = 1
        self.state.dtc_records[0x100009] = 0x09
        dtc_before = dict(self.state.dtc_records)

        handle_uds_request(bytes([SID_ECU_RESET, 0x01]), self.state)

        # file transfer 会话标识
        self.assertEqual(self.state.file_mode, FILE_MODE_IDLE)
        self.assertEqual(self.state.file_path, "")
        # upload 5
        self.assertFalse(self.state.upload_active)
        self.assertEqual(self.state.upload_address, 0)
        self.assertEqual(self.state.upload_size, 0)
        self.assertEqual(self.state.upload_expected_block, 0)
        self.assertEqual(self.state.upload_sent, 0)
        # download 6
        self.assertFalse(self.state.download_active)
        self.assertEqual(self.state.download_address, 0)
        self.assertEqual(self.state.download_size, 0)
        self.assertEqual(self.state.download_expected_block, 0)
        self.assertEqual(bytes(self.state.download_buffer), b"")
        self.assertEqual(self.state.download_last_block_request, None)
        # routine / DID override：清
        self.assertEqual(self.state.active_routines, set())
        self.assertEqual(self.state.routine_start_counts, {})
        self.assertEqual(self.state.did_overrides, {})
        # DTC：保留
        self.assertEqual(self.state.dtc_records, dtc_before)

    def test_session_switch_does_not_terminate_transfer(self) -> None:
        self.send(read_dir("/data/"))
        info = _build_dir_info(FILE_DATABASE, "/data/")
        self.send(transfer_data(0x01))

        response = self.send(bytes([SID_SESSION_CONTROL, 0x03]))
        self.assertEqual(response[:2], bytes([0x50, 0x03]))

        self.assertTrue(self.state.upload_active)
        self.assertEqual(self.state.file_mode, FILE_MODE_READ_DIR)
        self.assertEqual(self.state.upload_size, len(info))

    def test_session_switch_mid_write_keeps_transfer(self) -> None:
        self.send(add_file("/new.bin", 8))
        self.send(transfer_data(0x01, b"A" * 8))

        self.send(bytes([SID_SESSION_CONTROL, 0x03]))

        self.assertTrue(self.state.download_active)
        self.assertEqual(self.state.file_mode, FILE_MODE_ADD_FILE)

        self.send(transfer_exit())
        self.assertEqual(self.state.files["/new.bin"], b"A" * 8)

    def test_instance_isolation(self) -> None:
        other = EcuState()

        self.send(add_file("/inst.bin", 4))
        self.send(transfer_data(0x01, b"INST"))
        self.send(transfer_exit())

        self.assertIn("/inst.bin", self.state.files)
        self.assertNotIn("/inst.bin", other.files)
        self.assertEqual(other.file_mode, FILE_MODE_IDLE)
        self.assertEqual(other.file_path, "")

    def test_mutable_default_isolation(self) -> None:
        first = EcuState()
        second = EcuState()

        first.files["/only-first"] = b"x"
        first.files.pop("/data/sub/a.txt")

        self.assertNotIn("/only-first", second.files)
        self.assertIn("/data/sub/a.txt", second.files)

    def test_factory_database_never_modified(self) -> None:
        before = dict(FILE_DATABASE)

        state = EcuState()
        state.files["/new.bin"] = b"x"
        del state.files["/data/sub/a.txt"]
        handle_uds_request(read_dir("/"), state)
        handle_uds_request(bytes([SID_ECU_RESET, 0x01]), state)

        self.assertEqual(FILE_DATABASE, before)

    def test_new_state_defaults(self) -> None:
        state = EcuState()

        self.assertEqual(state.file_mode, FILE_MODE_IDLE)
        self.assertEqual(state.file_path, "")
        self.assertEqual(state.files, FILE_DATABASE)
        self.assertIsNot(state.files, FILE_DATABASE)

    def test_ecu_state_field_count_is_26(self) -> None:
        """本 PR 只新增 3 个字段（23 → 26）。"""
        self.assertEqual(len(fields(EcuState)), 26)

    def test_new_fields_are_exactly_the_three_declared(self) -> None:
        names = {field.name for field in fields(EcuState)}
        transfer_fields = {
            "file_mode", "file_path", "files",
            "download_active", "download_address", "download_size",
            "download_expected_block", "download_buffer",
            "download_last_block_request",
            "upload_active", "upload_address", "upload_size",
            "upload_expected_block", "upload_sent",
        }

        self.assertTrue(transfer_fields <= names)

    def test_no_forbidden_transfer_state_attributes(self) -> None:
        """本 PR 禁止新增的 transfer 计数器 / 缓冲（Design Review v4）。"""
        import uds_server

        for forbidden in (
            "dir_buffer",
            "upload_last_block",
            "upload_last_response",
            "DirectoryTransferState",
            "TransferState",
        ):
            with self.subTest(name=forbidden):
                self.assertFalse(hasattr(self.state, forbidden))
                self.assertFalse(hasattr(uds_server, forbidden))


if __name__ == "__main__":
    unittest.main(verbosity=2)
