#!/usr/bin/env python3
"""
ISO-TP stateful fuzz —— 执行引擎（engine）

架构（P1 §9/§27/§28/§29）::

    Fuzzer (本模块)
        │  python-can 原始帧           ← 不使用 ECU socket，不 import ecu/uds_server
        ▼
      vcan0
        │
        ▼
    Linux CAN_ISOTP
        │
        ▼
    ECU 子进程（每 case 一个全新进程）

Oracle（P1 §10/§21/§22/§23）::

    process   : 子进程 poll() → ALIVE / DEAD
    response  : UDS response / No response / Partial ISO-TP / Transport error
    recovery  : 硬 oracle —— 10 01 → 50 01 00 32 00 64
    runtime   : normal / timeout / hang / crash
    kernel    : dmesg before/after 快照 + 过滤差分（**绝不** dmesg -c）
                三态：AVAILABLE / UNAVAILABLE / ERROR（见 case_schema）。
                oracle 非 AVAILABLE 时 kernel_events 记为 None，
                **绝不**伪装成 "0 kernel event"。

分类顺序固定（见 ``case_schema.CLASS_PRIORITY``）::

    KERNEL_ANOMALY > ECU_CRASH > STATE_CORRUPTION > HANG
        > TRANSPORT_ERROR > UDS_RESPONSE > DISCARD > NORMAL

用法::

    python3 isotp_fuzz.py --list
    python3 isotp_fuzz.py --smoke --seed 12345 --out ../fuzz-out
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

import can
import isotp

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.dirname(_HERE)
_LAB_DIR = os.path.dirname(_TOOLS_DIR)
_ECU_DIR = os.path.join(_LAB_DIR, "uds-basic", "ecu")
_ECU_SCRIPT = os.path.join(_ECU_DIR, "ecu.py")

for _p in (_TOOLS_DIR, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from parsers import (  # noqa: E402
    InputError,
    guard_interface,
    parse_can_id,
)

import case_schema as cs  # noqa: E402
import isotp_corpus as corpus  # noqa: E402
import isotp_mutators as mut  # noqa: E402

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------

ECU_STARTUP_TIMEOUT = 10.0
#: 就绪探测的轮询间隔（不做硬编码 sleep）
READY_POLL_INTERVAL = 0.15
#: 每 case 允许的最长 wall-clock（超过即判 HANG）
CASE_TIMEOUT = 20.0
#: 观察窗口：等待 ECU 回帧
OBSERVE_QUIET = 0.35

_RE_ECU_RX = re.compile(r"^RX UDS: ([0-9a-f ]+)$")
_RE_ECU_TX = re.compile(r"^TX UDS: ([0-9a-f ]+)$")
_RE_ERRNO = re.compile(r"errno=(\d+)")
_RE_ISO_ERR = re.compile(r"ISO-TP (RX|TX) error")

#: 已实测会出现的 transport error（P3R/P3R2 containment 覆盖）
KNOWN_TRANSPORT_ERRNOS = (84, 70, 90)


# ----------------------------------------------------------------------
# 内核 oracle（只读快照，绝不 dmesg -c）
# ----------------------------------------------------------------------


def dmesg_snapshot() -> Tuple[str, List[str]]:
    """
    读取 dmesg 全量快照（只读）。返回 ``(oracle_status, lines)``。

    三态语义（工程收口 2026-09-21，见 ``case_schema``）：

    * ``AVAILABLE`` —— dmesg 成功执行（exit 0），lines 为全量行；
    * ``UNAVAILABLE`` —— dmesg 因权限/环境不可用（二进制缺失、
      非零退出码），lines 为空但**不代表** 0 kernel event；
    * ``ERROR`` —— dmesg 执行异常（超时、意外 OSError）。

    刻意不再静默吞掉异常：任何非 AVAILABLE 状态都必须显式进入
    ``result.kernel_oracle``，由汇总层如实呈现。
    """
    try:
        proc = subprocess.run(
            ["dmesg"], capture_output=True, text=True, timeout=10
        )
    except FileNotFoundError:
        # dmesg 二进制不存在（如精简容器/无 util-linux）
        return cs.KERNEL_ORACLE_UNAVAILABLE, []
    except subprocess.TimeoutExpired:
        return cs.KERNEL_ORACLE_ERROR, []
    except OSError:
        # 启动失败等意外执行异常——不是"可用但无事件"
        return cs.KERNEL_ORACLE_ERROR, []

    if proc.returncode != 0:
        # dmesg 返回非零（典型：无 root 时的 "Operation not permitted"）
        return cs.KERNEL_ORACLE_UNAVAILABLE, []

    return cs.KERNEL_ORACLE_AVAILABLE, proc.stdout.splitlines()


def kernel_oracle_result(status: str, delta: List[str]) -> Tuple[Optional[List[str]], bool]:
    """
    由 oracle 状态与内核差分行得到 ``(kernel_events, kernel_anomaly)``。

    oracle 非 AVAILABLE 时返回 ``(None, False)``：**None 明确表示
    "不可判定"**，与空列表（= 检查成功且无事件）严格区分。
    """
    if status != cs.KERNEL_ORACLE_AVAILABLE:
        return None, False

    hits = filter_kernel_anomalies(delta)

    return hits, bool(hits)


def dmesg_delta(before: List[str], after: List[str]) -> List[str]:
    """
    计算新增行。

    用"最长公共前缀 + 计数"之外的简单策略：dmesg 是环形追加的，
    因此取 after 中不属于 before 的行（按多重集合差）。
    """
    from collections import Counter

    seen = Counter(before)
    delta = []

    for line in after:
        if seen.get(line, 0) > 0:
            seen[line] -= 1
        else:
            delta.append(line)

    return delta


def filter_kernel_anomalies(delta: List[str]) -> List[str]:
    """按 P1 §23 的模式过滤。刻意**不**使用宽泛的 "can"。"""
    hits = []

    for line in delta:
        lowered = line.lower()

        for pattern in cs.KERNEL_ANOMALY_PATTERNS:
            if pattern.lower() in lowered:
                hits.append(line)
                break

    return hits


# ----------------------------------------------------------------------
# ECU 子进程（每 case 一个全新进程）
# ----------------------------------------------------------------------


class EcuProcess:
    """管理一个 ECU 子进程，并收集其 stdout。"""

    def __init__(self, env_overrides: Optional[Dict] = None):
        self.lines: List[str] = []
        self._proc: Optional[subprocess.Popen] = None
        self._env_overrides = env_overrides or {}

    def start(self) -> None:
        env = os.environ.copy()
        env["UDS_LOCKOUT_SECONDS"] = "2"
        env.update(self._env_overrides)

        self.lines = []
        self._proc = subprocess.Popen(
            [sys.executable, "-u", _ECU_SCRIPT],
            cwd=_ECU_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )

        import threading

        def _reader(stream) -> None:
            for line in iter(stream.readline, b""):
                self.lines.append(line.decode("utf-8", errors="replace").rstrip("\n"))

        threading.Thread(target=_reader, args=(self._proc.stdout,), daemon=True).start()

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def returncode(self) -> Optional[int]:
        return None if self._proc is None else self._proc.poll()

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass

            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

        if self._proc is not None and self._proc.stdout is not None:
            try:
                self._proc.stdout.close()
            except OSError:
                pass

        self._proc = None

    # ---- 日志解析 ----

    def log_tail(self, limit: int = 20) -> str:
        return "\n".join(self.lines[-limit:])

    def errnos(self) -> List[int]:
        found = []

        for line in self.lines:
            if _RE_ISO_ERR.search(line):
                m = _RE_ERRNO.search(line)

                if m:
                    found.append(int(m.group(1)))

        return found

    def crash_signature(self) -> str:
        for line in reversed(self.lines):
            stripped = line.strip()

            if stripped.startswith(("OSError", "TimeoutError", "RuntimeError",
                                    "ValueError", "Traceback")):
                return stripped

        return ""

    def uds_responses(self) -> List[bytes]:
        out = []

        for line in self.lines:
            m = _RE_ECU_TX.match(line.strip())

            if m:
                out.append(bytes.fromhex(m.group(1).replace(" ", "")))

        return out


# ----------------------------------------------------------------------
# raw CAN 通道（注入 + 观察）
# ----------------------------------------------------------------------


def _extract_payload(frame: bytes) -> Optional[bytes]:
    """从单帧 CAN 帧中提取 ISO-TP payload（只处理 SF）。"""
    if not frame:
        return None

    if (frame[0] & 0xF0) != corpus.PCI_SF:
        return None

    length = frame[0] & 0x0F

    return frame[1 : 1 + length]


class RawChannel:
    """在 vcan0 上注入 raw CAN 并观察 0x7E8。"""

    def __init__(self, interface: str = corpus.INTERFACE):
        self.interface = guard_interface(interface)
        self.bus = can.interface.Bus(interface="socketcan", channel=self.interface)
        self.bus.socket.settimeout(0.05)
        self._captured: List[Tuple[float, int, bytes]] = []

    def send(self, arbitration_id: int, data: bytes) -> None:
        try:
            validated_id = parse_can_id(str(arbitration_id))
        except InputError as exc:
            raise ValueError(f"非法 CAN ID：{exc}") from None

        if len(data) > corpus.CAN_DATA_MAX_LEN:
            raise ValueError(f"数据超过 {corpus.CAN_DATA_MAX_LEN} 字节")

        self.bus.send(can.Message(
            arbitration_id=validated_id,
            is_extended_id=False,
            data=bytes(data),
        ))

    def collect(self, duration: float, observe_id: int = corpus.OBSERVE_ID) -> List[bytes]:
        """在 duration 内收集 observe_id 上的帧（简单轮询，无 notifier 线程）。"""
        frames: List[bytes] = []
        deadline = time.monotonic() + duration

        while time.monotonic() < deadline:
            try:
                msg = self.bus.recv(timeout=0.05)
            except OSError:
                break

            if msg is None:
                continue

            if msg.arbitration_id == observe_id:
                frames.append(bytes(msg.data))

        return frames

    def wait_for_first_frame(self, timeout: float,
                             observe_id: int = corpus.OBSERVE_ID) -> bool:
        """等待 ECU 发出 FF（PCI 高半字节 0x1）。"""
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                msg = self.bus.recv(timeout=0.05)
            except OSError:
                return False

            if msg is None:
                continue

            if msg.arbitration_id == observe_id and msg.data:
                if (msg.data[0] & 0xF0) == corpus.PCI_FF:
                    return True

        return False

    def close(self) -> None:
        try:
            self.bus.shutdown()
        except Exception:  # noqa: BLE001
            pass


# ----------------------------------------------------------------------
# recovery client（独立 tester socket，P1 §29）
# ----------------------------------------------------------------------


def recovery_probe(interface: str = corpus.INTERFACE,
                   request: bytes = corpus.RECOVERY_REQUEST,
                   timeout: float = 1.5) -> Tuple[str, Optional[bytes]]:
    """
    用 python-isotp 建立**独立 tester socket** 发送恢复请求。

    返回 ``(status, payload)``，status ∈ {PASS, FAIL, NOT_RUN}。
    """
    sock = None

    try:
        sock = isotp.socket()
        sock.bind(
            guard_interface(interface),
            isotp.Address(
                isotp.AddressingMode.Normal_11bits,
                txid=corpus.INJECT_ID,
                rxid=corpus.OBSERVE_ID,
            ),
        )
        sock.settimeout(timeout)
        sock.send(bytes(request))
        response = sock.recv()

        if response is None:
            return cs.RECOVERY_FAIL, None

        if bytes(response[: len(corpus.RECOVERY_EXPECTED)]) == corpus.RECOVERY_EXPECTED:
            return cs.RECOVERY_PASS, bytes(response)

        return cs.RECOVERY_FAIL, bytes(response)
    except (TimeoutError, OSError):
        return cs.RECOVERY_FAIL, None
    except Exception:  # noqa: BLE001
        return cs.RECOVERY_NOT_RUN, None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


# ----------------------------------------------------------------------
# 单 case 执行
# ----------------------------------------------------------------------


def execute_case(case: Dict, out_dir: Optional[str] = None,
                 keep_artifacts: bool = True) -> Dict:
    """
    执行一个 testcase：全新 ECU → 注入 → 观察 → 恢复 → 分类。

    返回填好 ``result`` 的 case（同时可按需落盘）。
    """
    case = cs.validate_case(case)
    started = time.time()

    ecu = EcuProcess()
    channel = None
    kernel_before_status, kernel_before = dmesg_snapshot()

    observed_frames: List[bytes] = []
    injected: List[Tuple[int, bytes]] = []
    transport_errnos: List[int] = []
    hung = False

    try:
        ecu.start()
        channel = RawChannel(case["interface"])

        # ---- readiness：轮询直到有响应（不做硬编码 sleep）----
        #
        # 探针刻意用 ``10 01``（defaultSession）而不是 ``22 F1 80``：
        # 后者的响应是 15 字节**多帧**，raw 观察通道只看单帧会永远等不到；
        # 而 ``10 01`` 的响应是 6 字节单帧。两者都不会改变 fuzz testcase
        # 的初始状态（都停留在 defaultSession）。
        ready_deadline = time.monotonic() + ECU_STARTUP_TIMEOUT
        ready = False

        while time.monotonic() < ready_deadline:
            channel.send(corpus.INJECT_ID, corpus.sf(corpus.RECOVERY_REQUEST))
            frames = channel.collect(0.25)

            if any(
                (_extract_payload(f) or b"").startswith(corpus.RECOVERY_EXPECTED[:2])
                for f in frames
            ):
                ready = True
                break

            if not ecu.alive():
                break

            time.sleep(READY_POLL_INTERVAL)

        if not ready:
            return _finish_case(
                case, ecu, started, kernel_before_status, kernel_before,
                observed_frames, injected, transport_errnos,
                hung=False, ready=False, out_dir=out_dir,
                keep_artifacts=keep_artifacts,
            )

        # ---- 执行 frame sequence ----
        case_deadline = time.monotonic() + CASE_TIMEOUT

        for action in case["frames"]:
            if time.monotonic() > case_deadline:
                hung = True
                break

            kind = action["type"]

            if kind == "inject":
                channel.send(action["id"], action["data"])
                injected.append((action["id"], action["data"]))

                if action.get("delay_ms"):
                    observed_frames += channel.collect(action["delay_ms"] / 1000.0)

            elif kind == "wait":
                observed_frames += channel.collect(action["delay_ms"] / 1000.0)

            elif kind == "await_fc":
                got_ff = channel.wait_for_first_frame(action["timeout_ms"] / 1000.0)

                if got_ff:
                    channel.send(
                        corpus.INJECT_ID,
                        corpus.fc(
                            0,
                            action.get("block_size", 0),
                            action.get("stmin", 0),
                        ),
                    )
                    injected.append(
                        (corpus.INJECT_ID,
                         corpus.fc(0, action.get("block_size", 0), action.get("stmin", 0)))
                    )

                observed_frames += channel.collect(OBSERVE_QUIET)

        # ---- 观察窗口 ----
        observed_frames += channel.collect(OBSERVE_QUIET)

        transport_errnos = ecu.errnos()

    finally:
        alive = ecu.alive()
        crash_sig = ecu.crash_signature()
        rc = ecu.returncode()
        ecu_log = ecu.log_tail(30)

        if channel is not None:
            channel.close()

        # ---- recovery probe（独立 tester socket）----
        # 即使进程已死也跑一次：能区分"进程死了"与"进程活着但服务坏了"
        recovery, recovery_payload = recovery_probe(case["interface"])

        kernel_after_status, kernel_after = dmesg_snapshot()
        kernel_oracle = cs.combine_oracle_status(
            kernel_before_status, kernel_after_status
        )
        kernel_delta = dmesg_delta(kernel_before, kernel_after)
        kernel_events, kernel_anomaly = kernel_oracle_result(kernel_oracle, kernel_delta)

        ecu.stop()

    # ---- 观测归类 ----
    response_kind, response_text = _classify_response(observed_frames)
    errno = transport_errnos[-1] if transport_errnos else None

    result_class = cs.classify_result(
        process_alive=alive,
        response_kind=response_kind,
        recovery=recovery,
        errno=errno,
        kernel_anomaly=kernel_anomaly,
        hung=hung,
    )

    case["result"] = {
        "class": result_class,
        "process_alive": alive,
        "process_returncode": rc,
        "crash_signature": crash_sig,
        "response": response_text,
        "response_kind": response_kind,
        "errno": errno,
        "errnos_all": transport_errnos,
        "new_errno": cs.is_new_errno(errno),
        "recovery": recovery,
        "recovery_response": (
            recovery_payload.hex(" ") if recovery_payload else None
        ),
        "kernel_oracle": kernel_oracle,
        "kernel_events": kernel_events,
        "kernel_delta_lines": (
            len(kernel_delta)
            if kernel_oracle == cs.KERNEL_ORACLE_AVAILABLE
            else None
        ),
        "hung": hung,
        "duration_ms": int((time.time() - started) * 1000),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ecu_log_tail": ecu_log,
    }

    if out_dir and keep_artifacts:
        _persist_case(case, out_dir, kernel_before, kernel_after, kernel_delta)

    return case


def _classify_response(frames: List[bytes]) -> Tuple[str, str]:
    """把观察到的帧归类为 UDS response / Partial ISO-TP / none。"""
    if not frames:
        return "none", "(no response)"

    for frame in frames:
        payload = _extract_payload(frame)

        if payload:
            return "uds", payload.hex(" ")

    if any((f[0] & 0xF0) == corpus.PCI_FF for f in frames):
        # 只看到首帧——多帧响应被截断（例如传输被丢弃）
        return "partial", frames[0].hex(" ")

    return "other", frames[0].hex(" ")


def _persist_case(case: Dict, out_dir: str, kernel_before: List[str],
                  kernel_after: List[str], kernel_delta: List[str]) -> None:
    """落盘 testcase JSON 与内核快照（原始内容保留，过滤只用于分类）。"""
    os.makedirs(out_dir, exist_ok=True)
    case_path = os.path.join(out_dir, f"{case['case_id']}.json")

    with open(case_path, "w", encoding="utf-8") as fh:
        json.dump(cs.to_jsonable(case), fh, indent=2, ensure_ascii=False)

    state = case["result"]["class"]

    if state in cs.HIGH_PRIORITY_CLASSES or case["result"].get("new_errno"):
        kdir = os.path.join(out_dir, "kernel")
        os.makedirs(kdir, exist_ok=True)

        for name, lines in (("before", kernel_before), ("after", kernel_after),
                            ("delta", kernel_delta)):
            with open(os.path.join(kdir, f"{case['case_id']}.{name}.log"),
                      "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")


def _finish_case(case: Dict, ecu: EcuProcess, started: float,
                 kernel_before_status: str, kernel_before: List[str],
                 observed: List[bytes], injected: List[Tuple[int, bytes]],
                 errnos: List[int], hung: bool, ready: bool,
                 out_dir: Optional[str], keep_artifacts: bool) -> Dict:
    """ECU 未就绪时的收尾（不把"起不来"误判成 case 结果）。"""
    alive = ecu.alive()
    crash_sig = ecu.crash_signature()
    ecu_log = ecu.log_tail(30)
    ecu.stop()

    case["result"] = {
        "class": cs.ECU_CRASH if not alive else cs.DISCARD,
        "process_alive": alive,
        "process_returncode": ecu.returncode(),
        "crash_signature": crash_sig,
        "response": "(not ready)",
        "response_kind": "none",
        "errno": None,
        "errnos_all": errnos,
        "new_errno": False,
        "recovery": cs.RECOVERY_NOT_RUN,
        "recovery_response": None,
        "kernel_oracle": kernel_before_status,
        "kernel_events": (
            [] if kernel_before_status == cs.KERNEL_ORACLE_AVAILABLE else None
        ),
        "kernel_delta_lines": (
            0 if kernel_before_status == cs.KERNEL_ORACLE_AVAILABLE else None
        ),
        "hung": hung,
        "ready": ready,
        "duration_ms": int((time.time() - started) * 1000),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ecu_log_tail": ecu_log,
    }

    if out_dir and keep_artifacts:
        _persist_case(case, out_dir, kernel_before, [], [])

    return case


# ----------------------------------------------------------------------
# case 生成
# ----------------------------------------------------------------------


def generate_cases(categories: Optional[List[str]] = None,
                   seeds: Optional[List[str]] = None,
                   dimensions: Optional[List[str]] = None,
                   limit_per_seed: Optional[int] = None) -> List[Dict]:
    """
    从 corpus 生成全部（seed × 适用 mutation）case。

    纯生成，不执行。
    """
    cases: List[Dict] = []

    for seed_def in corpus.CORPUS:
        if seed_def.get("reference_only"):
            continue

        if categories and seed_def["category"] not in categories:
            continue

        if seeds and seed_def["seed_id"] not in seeds:
            continue

        specs = mut.mutation_specs_for_seed(seed_def)

        if dimensions:
            specs = [s for s in specs if s["dimension"] in dimensions]

        if limit_per_seed is not None:
            specs = _spread(specs, limit_per_seed)

        for spec in specs:
            actions, skip = mut.apply_mutation(seed_def, spec)

            if skip is not None:
                continue

            case_id = cs.make_case_id(
                seed_def["seed_id"], spec["dimension"], spec["label"]
            )
            mutation = {
                "dimension": spec["dimension"],
                "label": spec["label"],
                "params": spec["params"],
                "seed_fingerprint": mut.frames_fingerprint(seed_def["actions"]),
                "result_fingerprint": mut.frames_fingerprint(actions),
            }

            # 把 spec 上的分类/边界标记透传进 mutation，便于结果中识别
            for meta_key in ("class_hint", "stmin_class", "boundary"):
                if meta_key in spec:
                    mutation[meta_key] = spec[meta_key]

            cases.append(cs.build_case(
                case_id=case_id,
                seed_id=seed_def["seed_id"],
                mutation=mutation,
                actions=actions,
                category=seed_def["category"],
                summary=f"{seed_def['summary']} ｜ mutation: {spec['label']}",
            ))

    return cases


def _spread(items: List[Dict], count: int) -> List[Dict]:
    """在保持维度多样性的前提下取前 count 条（等距抽样）。"""
    if count >= len(items):
        return items

    if count <= 0:
        return []

    step = len(items) / float(count)

    return [items[int(i * step)] for i in range(count)]


def build_smoke_cases(per_category: int = 3) -> List[Dict]:
    """
    构造 P1 的在线 smoke 集合（≤30 case，每类至少一个）。

    每类取少量 mutation，覆盖 baseline / pci / sf / ff / sn / fc / padding。
    """
    priority_categories = [
        "sequence_number", "pci", "single_frame", "first_frame",
        "flow_control", "padding", "baseline", "consecutive_frame",
    ]

    cases: List[Dict] = []
    seen_ids = set()

    # 先保证每个类别至少 per_category 条
    for category in priority_categories:
        cat_cases = generate_cases(categories=[category])
        picked = _spread(cat_cases, per_category)

        for c in picked:
            if c["case_id"] not in seen_ids:
                cases.append(c)
                seen_ids.add(c["case_id"])

    # 再补充少量未覆盖维度（BS / STmin），确保 mutation 维度都露面
    for dim in (mut.DIM_BS, mut.DIM_STMIN):
        for c in generate_cases(dimensions=[dim], limit_per_seed=1):
            if c["case_id"] not in seen_ids and len(cases) < 30:
                cases.append(c)
                seen_ids.add(c["case_id"])

    return cases[:30]


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stateful ISO-TP fuzzer（vcan0，固定 CAN ID）"
    )
    parser.add_argument("--list", action="store_true",
                        help="只打印 corpus 与生成的 case 数量，不执行")
    parser.add_argument("--smoke", action="store_true",
                        help="运行小规模 smoke（<=30 case）")
    parser.add_argument("--categories", nargs="*",
                        help="只生成/执行这些类别")
    parser.add_argument("--dimensions", nargs="*", choices=list(mut.ALL_DIMENSIONS),
                        help="只生成/执行这些 mutation 维度")
    parser.add_argument("--seeds", nargs="*", help="只针对这些 seed_id")
    parser.add_argument("--limit-per-seed", type=int, default=None,
                        help="每个 seed 最多取多少条 mutation")
    parser.add_argument("--seed", type=int, default=12345,
                        help="master seed（P1 全部 mutation 为确定性，仅记录）")
    parser.add_argument("--out", default=os.path.join(_LAB_DIR, "fuzz-out"),
                        help="testcase JSON 输出目录")
    parser.add_argument("--max-cases", type=int, default=None,
                        help="硬上限（安全阀）")
    parser.add_argument("--no-artifacts", action="store_true",
                        help="不落盘 testcase")

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.list:
        stats = corpus.corpus_stats()
        print("=== seed corpus ===")

        for category in corpus.CATEGORIES:
            print(f"  {category:<20} {stats.get(category, 0)}")

        print(f"  {'TOTAL':<20} {len(corpus.CORPUS)}")

        all_cases = generate_cases()
        print(f"\n=== 可生成 case 总数（全部类别×全部维度）: {len(all_cases)} ===")

        by_dim: Dict[str, int] = {}

        for c in all_cases:
            by_dim[c["mutation"]["dimension"]] = (
                by_dim.get(c["mutation"]["dimension"], 0) + 1
            )

        for dim in mut.ALL_DIMENSIONS:
            print(f"  {dim:<14} {by_dim.get(dim, 0)}")

        print(f"\nsmoke 集合大小: {len(build_smoke_cases())}")
        return 0

    if not args.smoke:
        print("请指定 --list 或 --smoke（本阶段不做大规模 campaign）", file=sys.stderr)
        return 2

    cases = build_smoke_cases() if not (
        args.categories or args.dimensions or args.seeds
    ) else generate_cases(
        categories=args.categories,
        seeds=args.seeds,
        dimensions=args.dimensions,
        limit_per_seed=args.limit_per_seed or 1,
    )

    if args.max_cases is not None:
        cases = cases[: args.max_cases]

    out_dir = None if args.no_artifacts else args.out

    print(f"master_seed = {args.seed}")
    print(f"cases       = {len(cases)}")
    print(f"out_dir     = {out_dir}")
    print()

    summary: Dict[str, int] = {}
    oracle_summary: Dict[str, int] = {s: 0 for s in cs.KERNEL_ORACLE_STATUSES}
    high_priority: List[Dict] = []

    for index, case in enumerate(cases):
        case["master_seed"] = args.seed
        case["case_index"] = index

        result = execute_case(case, out_dir=out_dir, keep_artifacts=not args.no_artifacts)
        cls = result["result"]["class"]
        summary[cls] = summary.get(cls, 0) + 1
        oracle = result["result"].get("kernel_oracle", cs.KERNEL_ORACLE_UNAVAILABLE)
        oracle_summary[oracle] = oracle_summary.get(oracle, 0) + 1

        print(
            f"[{index + 1:>2}/{len(cases)}] {result['case_id'][:58]:<58} "
            f"{cls:<17} errno={result['result']['errno']} "
            f"alive={result['result']['process_alive']} "
            f"recovery={result['result']['recovery']} "
            f"oracle={oracle}"
        )

        if cls in cs.HIGH_PRIORITY_CLASSES or result["result"].get("new_errno"):
            high_priority.append(result)

            print(f"           !! {cls} — 保存最小复现并停止该 mutation family")
            print(f"           crash: {result['result']['crash_signature']}")

            if oracle != cs.KERNEL_ORACLE_AVAILABLE:
                print(f"           kernel oracle: {oracle} —— kernel_events 不可判定")
            elif result["result"].get("kernel_events"):
                print(f"           kernel: {result['result']['kernel_events'][:2]}")

            break  # P1 §37/§40：不继续大量重复，先停在结果上

    print()
    print("=== summary ===")

    for name in cs.CLASS_PRIORITY:
        print(f"  {name:<18} {summary.get(name, 0)}")

    print(f"  {'NEW_ERRNO cases':<18} "
          f"{sum(1 for c in cases if c.get('result', {}).get('new_errno'))}")

    print("  kernel oracle     "
          + " / ".join(f"{s}={oracle_summary.get(s, 0)}"
                       for s in cs.KERNEL_ORACLE_STATUSES))

    if oracle_summary.get(cs.KERNEL_ORACLE_AVAILABLE, 0) < len(cases):
        print("  !! kernel oracle 未全程 AVAILABLE：受影响 case 的 "
              "kernel_events=None（≠0），KERNEL_ANOMALY 分类降级为不可判定")

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        summary_path = os.path.join(out_dir, "smoke-summary.json")

        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "master_seed": args.seed,
                    "total": len(cases),
                    "summary": summary,
                    "kernel_oracle": oracle_summary,
                    "high_priority": [c["case_id"] for c in high_priority],
                },
                fh,
                indent=2,
                ensure_ascii=False,
            )

        print(f"\nsummary written: {summary_path}")

    return 1 if high_priority else 0


if __name__ == "__main__":
    sys.exit(main())
