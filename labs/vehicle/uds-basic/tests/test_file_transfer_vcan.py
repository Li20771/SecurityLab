#!/usr/bin/env python3
"""
UDS Basic —— 0x38 RequestFileTransfer vcan0 端到端测试

在真实 SocketCAN / ISO-TP 链路上验证 file transfer：

    Test
     → ISO-TP (can-isotp)
     → SocketCAN vcan0
     → Virtual ECU (ecu/ecu.py，独立子进程)

覆盖（按已冻结的 Design Review v4）：

1. ReadDir **完整**传输：``38 05`` → ``78 05 … <dirInfoLength>`` →
   ``36 01`` → ``76 01 <dirInfo>`` → … → ``37`` → ``77``
   （不是只验证 0x38 响应：目录数据必须真的经 upload transfer 送出）
2. ReadFile 完整传输（含 300 字节块经 ISO-TP 多帧分段）
3. AddFile / ReplaceFile 完整 download 传输，**只有** 0x37 提交 files
4. DeleteFile one-shot（不建立 transfer，之后 0x34/0x35 立即可用）
5. NRC 优先级在真实链路的表现（0x13 / 0x31 / 0x22）
6. 8 向互斥矩阵（0x34/0x35 handler 零改动）
7. 生命周期：hardReset 中止传输但**保留 files**、session switch 不终止

前置：vcan0 已存在并 UP；本测试**独占总线**，勿与其它 CAN 测试并行。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_file_transfer_vcan.py

或：

    bash tests/run_tests.sh all
"""

import atexit
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")

sys.path.insert(0, _ECU_DIR)

from file_database import (  # noqa: E402
    FILE_MODE_ADD_FILE,
    FILE_MODE_DELETE_FILE,
    FILE_MODE_READ_DIR,
    FILE_MODE_READ_FILE,
    FILE_MODE_REPLACE_FILE,
    _build_dir_info,
    _default_files,
)

_ECU_SCRIPT = os.path.join(_ECU_DIR, "ecu.py")

import isotp  # noqa: E402

INTERFACE = os.environ.get("UDS_INTERFACE", "vcan0")
TX_ID = 0x7E0
RX_ID = 0x7E8

ECU_STARTUP_TIMEOUT = 10.0
RECV_TIMEOUT = 1.5

SID_REQUEST_FILE_TRANSFER = 0x38
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

_CHUNK = 126


# ----------------------------------------------------------------------
# 请求构造
# ----------------------------------------------------------------------


def _path_field(path: str) -> bytes:
    raw = path.encode("ascii")
    return bytes([(len(raw) >> 8) & 0xFF, len(raw) & 0xFF]) + raw


def read_dir(path: str) -> bytes:
    return bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_READ_DIR]) + _path_field(path)


def read_file(path: str, dfi: int = 0x00) -> bytes:
    return (bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_READ_FILE])
            + _path_field(path) + bytes([dfi]))


def delete_file(path: str) -> bytes:
    return bytes([SID_REQUEST_FILE_TRANSFER, FILE_MODE_DELETE_FILE]) + _path_field(path)


def _write_request(mode: int, path: str, size: int, **kwargs) -> bytes:
    dfi = kwargs.get("dfi", 0x00)
    fsp = kwargs.get("fsp", 0x04)
    compressed = kwargs.get("compressed", size)

    return (bytes([SID_REQUEST_FILE_TRANSFER, mode])
            + _path_field(path)
            + bytes([dfi, fsp])
            + size.to_bytes(4, "big")
            + compressed.to_bytes(4, "big"))


def add_file(path: str, size: int, **kwargs) -> bytes:
    return _write_request(FILE_MODE_ADD_FILE, path, size, **kwargs)


def replace_file(path: str, size: int, **kwargs) -> bytes:
    return _write_request(FILE_MODE_REPLACE_FILE, path, size, **kwargs)


def block_request(counter: int, data: bytes = b"") -> bytes:
    return bytes([0x36, counter]) + data


def exit_request() -> bytes:
    return bytes([0x37])


def _expected_read_dir_response(dir_info_length: int) -> bytes:
    """本实验冻结 profile：恰好 10 字节，且不含 dirInfo 本体。"""
    return bytes([
        POS_REQUEST_FILE_TRANSFER, FILE_MODE_READ_DIR, 0x02, 0x00, 0x80, 0x00,
        0x00, 0x02,
    ]) + dir_info_length.to_bytes(2, "big")


# ----------------------------------------------------------------------
# 模块级 ECU 单例（每个测试类启动一个全新 ECU）
# ----------------------------------------------------------------------

_ECU_PROCESS = None
_ECU_LOG = None


def _wait_for_ecu_ready() -> None:
    """反复 10 01 探测，直到 ECU 应答 50 01（就绪探测，非测试断言）。"""
    deadline = time.monotonic() + ECU_STARTUP_TIMEOUT
    last_error = None

    while time.monotonic() < deadline:
        try:
            response = exchange(bytes([0x10, 0x01]), timeout=0.5)

            if response is not None and response[:2] == bytes([0x50, 0x01]):
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc

        time.sleep(0.2)

    raise AssertionError(
        f"虚拟 ECU 在 {ECU_STARTUP_TIMEOUT}s 内没有就绪：{last_error!r}"
    )


def _start_fresh_ecu() -> None:
    global _ECU_PROCESS, _ECU_LOG

    _stop_ecu_process()

    env = os.environ.copy()
    env["UDS_LOCKOUT_SECONDS"] = "2"

    _ECU_LOG = tempfile.NamedTemporaryFile(
        prefix="uds-file-ecu-", suffix=".log", delete=False
    )
    _ECU_PROCESS = subprocess.Popen(
        [sys.executable, "-u", _ECU_SCRIPT],
        cwd=_ECU_DIR,
        stdout=_ECU_LOG,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )

    _wait_for_ecu_ready()


def _stop_ecu_process() -> None:
    global _ECU_PROCESS, _ECU_LOG

    if _ECU_PROCESS is not None and _ECU_PROCESS.poll() is None:
        try:
            os.killpg(os.getpgid(_ECU_PROCESS.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            _ECU_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(_ECU_PROCESS.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass

    if _ECU_LOG is not None:
        try:
            _ECU_LOG.close()
        except Exception:  # noqa: BLE001
            pass

        _ECU_LOG = None

    _ECU_PROCESS = None


atexit.register(_stop_ecu_process)


def exchange(payload: bytes, timeout: float = RECV_TIMEOUT):
    """以 (tx=0x7E0, rx=0x7E8) 新建 ISO-TP 连接收发；超时返回 None。"""
    sock = None
    try:
        sock = isotp.socket()
        sock.bind(
            INTERFACE,
            isotp.Address(
                isotp.AddressingMode.Normal_11bits,
                rxid=RX_ID,
                txid=TX_ID,
            ),
        )
        sock.settimeout(timeout)
        sock.send(payload)
        return sock.recv()
    except Exception:  # noqa: BLE001 - 超时/底层错误统一按"无响应"处理
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


def _drain_vcan0(duration: float = 0.2) -> None:
    """丢弃 vcan0 上残留报文，避免上次实验遗留帧干扰。"""
    import can

    bus = can.interface.Bus(interface="socketcan", channel=INTERFACE)
    bus.socket.settimeout(0.05)

    deadline = time.monotonic() + duration

    try:
        while time.monotonic() < deadline:
            try:
                if bus.recv(timeout=0.05) is None:
                    continue
            except Exception:  # noqa: BLE001
                break
    finally:
        bus.shutdown()


class FileTransferVcanTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        _start_fresh_ecu()

    def setUp(self) -> None:
        _drain_vcan0()
        # 模块级 ECU 单例跨测试保持状态：用 hardReset 走协议清掉进行中的
        # transfer。注意 hardReset **刻意保留 files**（0x38 语义），
        # 因此破坏性用例各自使用唯一路径，不互相依赖。
        exchange(bytes([SID_ECU_RESET, 0x01]))


# ----------------------------------------------------------------------
# 1. ReadDir：完整 upload transfer（本 feature 的核心验收）
# ----------------------------------------------------------------------


class TestReadDirE2E(FileTransferVcanTestCase):
    """只读用例：ReadDir 的真实链路完整传输。"""

    def _collect_dir_info(self, path: str) -> bytes:
        """走完 38 05 → 36 … → 37，返回收到的 dirInfo 字节。"""
        state_files = _default_files()
        info = _build_dir_info(state_files, path)

        response = exchange(read_dir(path))
        self.assertIsNotNone(response, f"ReadDir({path!r}) 无响应")
        self.assertEqual(response, _expected_read_dir_response(len(info)))

        received = b""
        counter = 1

        while len(received) < len(info):
            chunk = info[len(received):len(received) + _CHUNK]
            block = exchange(block_request(counter))

            self.assertEqual(
                block, bytes([POS_TRANSFER_DATA, counter]) + chunk,
                f"第 {counter} 块内容不符",
            )
            received += chunk
            counter += 1

        self.assertEqual(exchange(exit_request()),
                         bytes([POS_REQUEST_TRANSFER_EXIT]))
        return received

    def test_read_dir_root_full_transfer(self) -> None:
        """根目录：38 05 → 78 05 … → 36 01 → 76 01 <dirInfo> → 37 → 77。"""
        info = self._collect_dir_info("/")

        self.assertEqual(len(info), 32)
        self.assertIn(b"boot", info)
        self.assertIn(b"data", info)
        self.assertIn(b"logs", info)

    def test_read_dir_response_is_10_bytes(self) -> None:
        response = exchange(read_dir("/data/"))

        self.assertEqual(
            response,
            bytes([0x78, 0x05, 0x02, 0x00, 0x80, 0x00, 0x00, 0x02, 0x00, 0x1B]),
        )
        self.assertEqual(len(response), 10)

    def test_read_dir_response_excludes_dir_info_body(self) -> None:
        info = _build_dir_info(_default_files(), "/data/")

        response = exchange(read_dir("/data/"))

        self.assertNotIn(info, response)
        self.assertNotIn(b"config.bin", response)
        self.assertNotIn(b"sub", response)

    def test_read_dir_data_directory_full_transfer(self) -> None:
        info = self._collect_dir_info("/data/")

        self.assertIn(b"config.bin", info)
        self.assertIn(b"sub", info)
        self.assertNotIn(b"a.txt", info)

    def test_read_dir_nested_directory(self) -> None:
        info = self._collect_dir_info("/data/sub/")

        self.assertEqual(info, b"\x00\x01\x00\x05" + b"a.txt" + b"\x00\x00\x00\x06")

    def test_read_dir_duplicate_block_is_byte_identical(self) -> None:
        """真实链路上 duplicate 必须重发完全相同的字节。"""
        info = _build_dir_info(_default_files(), "/data/")
        exchange(read_dir("/data/"))

        first = exchange(block_request(0x01))
        duplicate = exchange(block_request(0x01))

        self.assertEqual(first, bytes([POS_TRANSFER_DATA, 0x01]) + info)
        self.assertEqual(duplicate, first)

    def test_read_dir_initial_36_00_is_wrong_bsc(self) -> None:
        exchange(read_dir("/data/"))

        self.assertEqual(
            exchange(block_request(0x00)),
            bytes([0x7F, 0x36, NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )

    def test_read_dir_wrong_bsc(self) -> None:
        exchange(read_dir("/data/"))

        self.assertEqual(
            exchange(block_request(0x05)),
            bytes([0x7F, 0x36, NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )

    def test_read_dir_missing_directory(self) -> None:
        self.assertEqual(
            exchange(read_dir("/nope/")),
            bytes([0x7F, 0x38, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_read_dir_without_trailing_slash(self) -> None:
        self.assertEqual(
            exchange(read_dir("/data")),
            bytes([0x7F, 0x38, NRC_REQUEST_OUT_OF_RANGE]),
        )


class TestReadDirMultiBlockE2E(FileTransferVcanTestCase):
    """多块 dirInfo（> 126 字节）：必须跨多个 0x36 块送出。

    单独一个类 + 独立 ECU：本类会写入文件，而 ReadDir 的根目录
    dirInfo 字节数会随之变化，不能与上面的逐字节只读断言共用一个 ECU。
    """

    _DIR = "/vcandir/"
    _NAMES = [f"/vcandir/vcan{index:02d}" for index in range(20)]

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        _drain_vcan0()

        for name in cls._NAMES:
            assert exchange(add_file(name, 1)) == bytes(
                [0x78, 0x01, 0x02, 0x00, 0x80, 0x00]), name
            assert exchange(block_request(0x01, b"x")) == bytes([0x76, 0x01]), name
            assert exchange(exit_request()) == bytes([0x77]), name

    def _expected(self) -> bytes:
        return _build_dir_info(
            {name: b"x" for name in self._NAMES}, self._DIR)

    def test_dir_info_spans_two_blocks(self) -> None:
        info = self._expected()

        # 2 + 20 * (6 + 6) = 242 → 126 + 116
        self.assertEqual(len(info), 242)

    def test_incomplete_exit_then_complete(self) -> None:
        info = self._expected()

        response = exchange(read_dir(self._DIR))
        self.assertEqual(response, _expected_read_dir_response(len(info)))

        self.assertEqual(
            exchange(block_request(0x01)),
            bytes([POS_TRANSFER_DATA, 0x01]) + info[:_CHUNK],
        )
        self.assertEqual(
            exchange(exit_request()),
            bytes([0x7F, 0x37, NRC_CONDITIONS_NOT_CORRECT]),
        )

        self.assertEqual(
            exchange(block_request(0x02)),
            bytes([POS_TRANSFER_DATA, 0x02]) + info[_CHUNK:],
        )
        self.assertEqual(exchange(exit_request()), bytes([0x77]))

    def test_duplicate_tail_block_is_byte_identical(self) -> None:
        info = self._expected()
        exchange(read_dir(self._DIR))
        exchange(block_request(0x01))

        tail = exchange(block_request(0x02))

        self.assertEqual(tail, bytes([POS_TRANSFER_DATA, 0x02]) + info[_CHUNK:])
        self.assertEqual(exchange(block_request(0x02)), tail)


# ----------------------------------------------------------------------
# 2. ReadFile：完整 upload transfer
# ----------------------------------------------------------------------


class TestReadFileE2E(FileTransferVcanTestCase):

    def test_read_file_config_bin_full_transfer(self) -> None:
        content = _default_files()["/data/config.bin"]

        response = exchange(read_file("/data/config.bin"))
        self.assertEqual(
            response,
            bytes([0x78, 0x04, 0x02, 0x00, 0x80, 0x00, 0x00, 0x04])
            + len(content).to_bytes(4, "big") * 2,
        )

        first = exchange(block_request(0x01))
        self.assertEqual(first, bytes([POS_TRANSFER_DATA, 0x01]) + content[:_CHUNK])

        tail = exchange(block_request(0x02))
        self.assertEqual(tail, bytes([POS_TRANSFER_DATA, 0x02]) + content[_CHUNK:])

        self.assertEqual(exchange(exit_request()), bytes([0x77]))

    def test_read_file_300_bytes_multiframe(self) -> None:
        """300 字节 → 126 + 126 + 48；大块经 ISO-TP 多帧分段。"""
        content = _default_files()["/logs/last.log"]

        self.assertEqual(exchange(read_file("/logs/last.log"))[-8:],
                         (300).to_bytes(4, "big") * 2)

        received = b""
        for counter in (1, 2, 3):
            block = exchange(block_request(counter))
            chunk = content[len(received):len(received) + _CHUNK]
            self.assertEqual(block, bytes([POS_TRANSFER_DATA, counter]) + chunk)
            received += chunk

        self.assertEqual(received, content)
        self.assertEqual(exchange(exit_request()), bytes([0x77]))

    def test_read_file_missing(self) -> None:
        self.assertEqual(
            exchange(read_file("/nope.bin")),
            bytes([0x7F, 0x38, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_read_file_bad_dfi(self) -> None:
        self.assertEqual(
            exchange(read_file("/data/config.bin", dfi=0x01)),
            bytes([0x7F, 0x38, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_read_file_extra_field(self) -> None:
        self.assertEqual(
            exchange(read_file("/data/config.bin") + b"\x00"),
            bytes([0x7F, 0x38, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_read_file_on_directory(self) -> None:
        self.assertEqual(
            exchange(read_file("/data")),
            bytes([0x7F, 0x38, NRC_REQUEST_OUT_OF_RANGE]),
        )


# ----------------------------------------------------------------------
# 3. AddFile / ReplaceFile / DeleteFile
# ----------------------------------------------------------------------


class TestWriteFileE2E(FileTransferVcanTestCase):
    """破坏性用例：各自使用唯一路径，不互相依赖。"""

    def _add(self, path: str, body: bytes) -> None:
        self.assertEqual(
            exchange(add_file(path, len(body))),
            bytes([0x78, 0x01, 0x02, 0x00, 0x80, 0x00]),
        )
        for index, offset in enumerate(range(0, len(body), _CHUNK), start=1):
            self.assertEqual(
                exchange(block_request(index, body[offset:offset + _CHUNK])),
                bytes([POS_TRANSFER_DATA, index]),
            )
        self.assertEqual(exchange(exit_request()), bytes([0x77]))

    def _read_back(self, path: str) -> bytes:
        response = exchange(read_file(path))
        self.assertIsNotNone(response, f"ReadFile({path!r}) 无响应")

        total = int.from_bytes(response[-8:-4], "big")
        received = b""
        counter = 1

        while len(received) < total:
            block = exchange(block_request(counter))
            received += block[2:]
            counter += 1

        self.assertEqual(exchange(exit_request()), bytes([0x77]))
        return received

    def test_add_file_then_read_back(self) -> None:
        body = bytes(range(200))
        self._add("/vcan-new.bin", body)

        self.assertEqual(self._read_back("/vcan-new.bin"), body)

    def test_add_file_visible_in_read_dir(self) -> None:
        self._add("/vcan-visible.bin", b"abcd")

        response = exchange(read_dir("/"))
        self.assertEqual(response[:2], bytes([0x78, 0x05]))

        info = self._collect_root(response)
        self.assertIn(b"vcan-visible.bin", info)

    def _collect_root(self, response: bytes) -> bytes:
        total = int.from_bytes(response[-2:], "big")
        received = b""
        counter = 1

        while len(received) < total:
            block = exchange(block_request(counter))
            received += block[2:]
            counter += 1

        exchange(exit_request())
        return received

    def test_add_existing_file_rejected(self) -> None:
        self.assertEqual(
            exchange(add_file("/data/config.bin", 4)),
            bytes([0x7F, 0x38, NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_add_existing_directory_rejected(self) -> None:
        self.assertEqual(
            exchange(add_file("/data", 4)),
            bytes([0x7F, 0x38, NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_add_zero_size_rejected(self) -> None:
        self.assertEqual(
            exchange(add_file("/vcan-zero.bin", 0)),
            bytes([0x7F, 0x38, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_add_incomplete_then_reset_does_not_create_file(self) -> None:
        """声明 16 字节但只送 8 → 未完成；abort 后目标路径不得存在。"""
        self.assertEqual(
            exchange(add_file("/vcan-abort.bin", 16)),
            bytes([0x78, 0x01, 0x02, 0x00, 0x80, 0x00]),
        )
        self.assertEqual(exchange(block_request(0x01, b"A" * 8)),
                         bytes([POS_TRANSFER_DATA, 0x01]))

        self.assertEqual(
            exchange(exit_request()),
            bytes([0x7F, 0x37, NRC_CONDITIONS_NOT_CORRECT]),
        )

        self.assertEqual(exchange(bytes([SID_ECU_RESET, 0x01])), bytes([0x51, 0x01]))

        self.assertEqual(
            exchange(read_file("/vcan-abort.bin")),
            bytes([0x7F, 0x38, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_replace_abort_keeps_original(self) -> None:
        """ReplaceFile 中途 abort（硬复位）不得覆盖既有文件。"""
        original = b"alpha\n"
        body = b"replaced-body"

        self.assertEqual(
            exchange(replace_file("/data/sub/a.txt", len(body))),
            bytes([0x78, 0x03, 0x02, 0x00, 0x80, 0x00]),
        )
        self.assertEqual(exchange(block_request(0x01, body)),
                         bytes([POS_TRANSFER_DATA, 0x01]))

        self.assertEqual(exchange(bytes([SID_ECU_RESET, 0x01])), bytes([0x51, 0x01]))

        self.assertEqual(self._read_back("/data/sub/a.txt"), original)

    def test_replace_file_full_transfer(self) -> None:
        """ReplaceFile 完整传输（0x37 收尾）后内容被覆盖。"""
        body = b"replaced-body"

        self.assertEqual(
            exchange(replace_file("/data/sub/a.txt", len(body))),
            bytes([0x78, 0x03, 0x02, 0x00, 0x80, 0x00]),
        )

        self.assertEqual(exchange(block_request(0x01, body)),
                         bytes([POS_TRANSFER_DATA, 0x01]))
        self.assertEqual(exchange(exit_request()), bytes([0x77]))

        self.assertEqual(self._read_back("/data/sub/a.txt"), body)

    def test_delete_file_is_one_shot(self) -> None:
        body = b"delete-me"
        self._add("/vcan-del.bin", body)

        self.assertEqual(exchange(delete_file("/vcan-del.bin")), bytes([0x78, 0x02]))

        self.assertEqual(
            exchange(block_request(0x01)),
            bytes([0x7F, 0x36, NRC_REQUEST_SEQUENCE_ERROR]),
        )
        self.assertEqual(
            exchange(exit_request()),
            bytes([0x7F, 0x37, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_delete_file_then_34_and_35_usable(self) -> None:
        self._add("/vcan-del2.bin", b"x")
        self.assertEqual(exchange(delete_file("/vcan-del2.bin")), bytes([0x78, 0x02]))

        self.assertEqual(
            exchange(bytes([0x34, 0x00, 0x23, 0x11, 0x22, 0x33, 0x00, 0x04])),
            bytes([0x74, 0x20, 0x00, 0x80]),
        )

    def test_delete_missing_file(self) -> None:
        self.assertEqual(
            exchange(delete_file("/nope.bin")),
            bytes([0x7F, 0x38, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_delete_directory_rejected(self) -> None:
        self.assertEqual(
            exchange(delete_file("/data")),
            bytes([0x7F, 0x38, NRC_REQUEST_OUT_OF_RANGE]),
        )


# ----------------------------------------------------------------------
# 4. NRC 优先级 / 互斥 / 生命周期
# ----------------------------------------------------------------------


class TestFileTransferRulesE2E(FileTransferVcanTestCase):

    def test_unsupported_modes_are_out_of_range(self) -> None:
        for mode in (0x00, 0x06, 0xFF):
            with self.subTest(mode=mode):
                request = (bytes([SID_REQUEST_FILE_TRANSFER, mode, 0x00, 0x01])
                           + b"/")
                self.assertEqual(
                    exchange(request),
                    bytes([0x7F, 0x38, NRC_REQUEST_OUT_OF_RANGE]),
                )

    def test_too_short_is_incorrect_length(self) -> None:
        self.assertEqual(
            exchange(bytes([SID_REQUEST_FILE_TRANSFER, 0x05])),
            bytes([0x7F, 0x38, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_invalid_path_is_out_of_range(self) -> None:
        self.assertEqual(
            exchange(bytes([SID_REQUEST_FILE_TRANSFER, 0x05, 0x00, 0x04]) + b"data"),
            bytes([0x7F, 0x38, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_38_while_34_active(self) -> None:
        exchange(bytes([0x34, 0x00, 0x23, 0x11, 0x22, 0x33, 0x00, 0x04]))

        self.assertEqual(
            exchange(read_dir("/")),
            bytes([0x7F, 0x38, NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_38_while_35_active(self) -> None:
        exchange(bytes([0x35, 0x00, 0x23, 0x11, 0x22, 0x33, 0x00, 0x82]))

        self.assertEqual(
            exchange(read_dir("/")),
            bytes([0x7F, 0x38, NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_38_while_38_active(self) -> None:
        exchange(read_dir("/"))

        self.assertEqual(
            exchange(read_file("/data/config.bin")),
            bytes([0x7F, 0x38, NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_34_and_35_blocked_while_38_active(self) -> None:
        exchange(read_dir("/"))

        self.assertEqual(
            exchange(bytes([0x34, 0x00, 0x23, 0x11, 0x22, 0x33, 0x00, 0x04])),
            bytes([0x7F, 0x34, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertEqual(
            exchange(bytes([0x35, 0x00, 0x23, 0x11, 0x22, 0x33, 0x00, 0x82])),
            bytes([0x7F, 0x35, NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_hard_reset_aborts_read_dir(self) -> None:
        exchange(read_dir("/data/"))
        exchange(block_request(0x01))

        self.assertEqual(exchange(bytes([SID_ECU_RESET, 0x01])), bytes([0x51, 0x01]))

        self.assertEqual(
            exchange(block_request(0x01)),
            bytes([0x7F, 0x36, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_hard_reset_preserves_files(self) -> None:
        """hardReset 保留 files：自建的持久文件在复位后仍可读回。"""
        body = b"persistent"
        self.assertEqual(
            exchange(add_file("/vcan-persist.bin", len(body))),
            bytes([0x78, 0x01, 0x02, 0x00, 0x80, 0x00]),
        )
        exchange(block_request(0x01, body))
        exchange(exit_request())

        self.assertEqual(exchange(bytes([SID_ECU_RESET, 0x01])), bytes([0x51, 0x01]))

        response = exchange(read_file("/vcan-persist.bin"))
        self.assertEqual(
            response,
            bytes([0x78, 0x04, 0x02, 0x00, 0x80, 0x00, 0x00, 0x04])
            + len(body).to_bytes(4, "big") * 2,
        )

    def test_session_switch_does_not_terminate_transfer(self) -> None:
        info = _build_dir_info(_default_files(), "/data/")
        exchange(read_dir("/data/"))

        self.assertEqual(exchange(bytes([SID_SESSION_CONTROL, 0x03]))[:2],
                         bytes([0x50, 0x03]))

        self.assertEqual(
            exchange(block_request(0x01)),
            bytes([POS_TRANSFER_DATA, 0x01]) + info,
        )
        self.assertEqual(exchange(exit_request()), bytes([0x77]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
