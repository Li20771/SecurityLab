#!/usr/bin/env python3
"""
ISO-TP stateful fuzz —— testcase schema、校验与结果分类

职责：

- 定义 testcase JSON 的稳定结构（``schema_version = 1``）
- 严格校验（interface / CAN ID / DLC / 字节），复用 ``parsers.py``
- 定义 oracle 分类枚举与**固定优先级顺序**
- 定义 **canonical replay identity**：
  只依赖 ``case_id`` / ``seed`` / ``mutation`` / ``frames``（含 delay），
  **不含** timestamp / kernel logs / observed response / process state ——
  那些是 execution result，不能改变 testcase 本身。

本模块不做任何 I/O（除参数校验外），也不 import python-can / python-isotp。
"""

import re
from typing import Dict, List, Optional, Tuple

try:
    from parsers import (  # 同目录上一级：can-tools/parsers.py
        CAN_DATA_MAX_LEN,
        InputError,
        guard_interface,
        parse_can_id,
        parse_hex_bytes,
    )
except ImportError:  # pragma: no cover - 便于以包方式导入
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from parsers import (  # type: ignore[no-redef]
        CAN_DATA_MAX_LEN,
        InputError,
        guard_interface,
        parse_can_id,
        parse_hex_bytes,
    )

SCHEMA_VERSION = 1

PHASE = "4X-P1"

# ----------------------------------------------------------------------
# 结果分类（固定优先级：数组顺序即判定顺序）
# ----------------------------------------------------------------------

KERNEL_ANOMALY = "KERNEL_ANOMALY"
ECU_CRASH = "ECU_CRASH"
STATE_CORRUPTION = "STATE_CORRUPTION"
HANG = "HANG"
TRANSPORT_ERROR = "TRANSPORT_ERROR"
UDS_RESPONSE = "UDS_RESPONSE"
DISCARD = "DISCARD"
NORMAL = "NORMAL"

#: **判定顺序即优先级**（先命中者胜出）
CLASS_PRIORITY: Tuple[str, ...] = (
    KERNEL_ANOMALY,
    ECU_CRASH,
    STATE_CORRUPTION,
    HANG,
    TRANSPORT_ERROR,
    UDS_RESPONSE,
    DISCARD,
    NORMAL,
)

#: 需要在报告中优先关注的高优先级类别
HIGH_PRIORITY_CLASSES = (KERNEL_ANOMALY, ECU_CRASH, STATE_CORRUPTION)

#: 恢复探针结果
RECOVERY_PASS = "PASS"
RECOVERY_FAIL = "FAIL"
RECOVERY_NOT_RUN = "NOT_RUN"

#: 已 containment 的 errno（P3R/P3R2）。出现其它 errno → NEW_ERRNO 标记。
CONTAINED_ERRNOS = (84, 70, 90)

#: kernel oracle 的关注模式（P1 §23：刻意不用宽泛的 "can"）
KERNEL_ANOMALY_PATTERNS = (
    "isotp",
    "can-isotp",
    "net/can",
    "Oops",
    "BUG:",
    "WARNING:",
    "panic",
    "KASAN",
    "UBSAN",
    "use-after-free",
    "out of bounds",
    "general protection fault",
)

_RE_CASE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class SchemaError(ValueError):
    """testcase 结构或取值不合法。"""


# ----------------------------------------------------------------------
# case_id
# ----------------------------------------------------------------------


def make_case_id(seed_id: str, dimension: str, label: str) -> str:
    """
    生成稳定、可读、可复现的 case id。

    同一 (seed, dimension, label) 必然得到同一个 id —— 这是 replay identity 的基础。
    """
    safe = re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-").lower()
    case_id = f"{seed_id.lower()}__{dimension}__{safe}"

    if not _RE_CASE_ID.match(case_id):
        raise SchemaError(f"生成的 case_id 不合法：{case_id!r}")

    return case_id


# ----------------------------------------------------------------------
# 动作 / 帧校验
# ----------------------------------------------------------------------


def _validate_bytes(data, where: str) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        raw = bytes(data)

        if len(raw) > CAN_DATA_MAX_LEN:
            raise SchemaError(
                f"{where}: 经典 CAN 数据最多 {CAN_DATA_MAX_LEN} 字节，收到 {len(raw)}"
            )

        return raw

    if isinstance(data, str):
        try:
            return bytes(parse_hex_bytes([data]))
        except InputError as exc:
            raise SchemaError(f"{where}: 十六进制解析失败：{exc}") from None

    raise SchemaError(f"{where}: data 必须是 bytes 或十六进制字符串")


def validate_action(action: Dict, index: int) -> Dict:
    """校验并规范化一个 action。"""
    if not isinstance(action, dict):
        raise SchemaError(f"actions[{index}] 必须是对象")

    kind = action.get("type")

    if kind == "inject":
        if "id" not in action:
            raise SchemaError(f"actions[{index}]: inject 缺少 id")

        try:
            arbitration_id = parse_can_id(str(action["id"]))
        except InputError as exc:
            raise SchemaError(f"actions[{index}]: 非法 CAN ID：{exc}") from None

        data = _validate_bytes(action.get("data", b""), f"actions[{index}]")
        delay_ms = int(action.get("delay_ms", 0))

        if delay_ms < 0:
            raise SchemaError(f"actions[{index}]: delay_ms 不能为负")

        return {
            "type": "inject",
            "id": arbitration_id,
            "data": data,
            "delay_ms": delay_ms,
        }

    if kind == "wait":
        delay_ms = int(action.get("delay_ms", 0))

        if delay_ms < 0:
            raise SchemaError(f"actions[{index}]: delay_ms 不能为负")

        return {"type": "wait", "delay_ms": delay_ms}

    if kind == "await_fc":
        timeout_ms = int(action.get("timeout_ms", 0))

        if timeout_ms < 0:
            raise SchemaError(f"actions[{index}]: timeout_ms 不能为负")

        return {
            "type": "await_fc",
            "timeout_ms": timeout_ms,
            "block_size": int(action.get("block_size", 0)),
            "stmin": int(action.get("stmin", 0)),
        }

    raise SchemaError(f"actions[{index}]: 未知 action 类型 {kind!r}")


def validate_frames(actions: List[Dict]) -> List[Dict]:
    if not isinstance(actions, list) or not actions:
        raise SchemaError("frames 必须是非空列表")

    return [validate_action(a, i) for i, a in enumerate(actions)]


# ----------------------------------------------------------------------
# testcase 构造与校验
# ----------------------------------------------------------------------


def build_case(case_id: str, seed_id: str, mutation: Dict, actions: List[Dict],
               category: str, summary: str = "") -> Dict:
    """构造一个符合 schema 的 testcase（尚未执行，result 为空）。"""
    case = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "phase": PHASE,
        "seed": seed_id,
        "category": category,
        "summary": summary,
        "mutation": mutation,
        "interface": "vcan0",
        "can_ids": {"inject": "0x7E0", "observe": "0x7E8"},
        "frames": validate_frames(actions),
        "expected": {
            "oracle": ["process", "response", "recovery", "runtime", "kernel"],
            "recovery_request": "10 01",
            "recovery_expected": "50 01 00 32 00 64",
        },
        "result": {},
    }

    return case


def validate_case(case: Dict, require_result: bool = False) -> Dict:
    """
    校验一个 testcase（读回来的 JSON 也要过这一关）。

    :raises SchemaError: 任何结构/取值问题
    """
    if not isinstance(case, dict):
        raise SchemaError("testcase 必须是对象")

    if case.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError(
            f"schema_version 必须是 {SCHEMA_VERSION}，收到 {case.get('schema_version')!r}"
        )

    case_id = case.get("case_id")

    if not isinstance(case_id, str) or not _RE_CASE_ID.match(case_id):
        raise SchemaError(f"case_id 不合法：{case_id!r}")

    if not isinstance(case.get("seed"), str) or not case["seed"]:
        raise SchemaError("seed 必须是非空字符串")

    try:
        interface = guard_interface(case.get("interface", ""))
    except InputError as exc:
        raise SchemaError(f"interface 不合法：{exc}") from None

    can_ids = case.get("can_ids") or {}
    normalized_ids = {}

    for key in ("inject", "observe"):
        if key not in can_ids:
            raise SchemaError(f"can_ids 缺少 {key}")

        try:
            normalized_ids[key] = parse_can_id(str(can_ids[key]))
        except InputError as exc:
            raise SchemaError(f"can_ids.{key} 不合法：{exc}") from None

    actions = validate_frames(case.get("frames") or [])

    mutation = case.get("mutation")

    if not isinstance(mutation, dict):
        raise SchemaError("mutation 必须是对象")

    for field in ("dimension", "label"):
        if not mutation.get(field):
            raise SchemaError(f"mutation 缺少 {field}")

    if require_result:
        result = case.get("result")

        if not isinstance(result, dict) or not result:
            raise SchemaError("缺少 result")

        if result.get("class") not in CLASS_PRIORITY:
            raise SchemaError(f"result.class 不合法：{result.get('class')!r}")

    # 规范化后返回（interface 已确认是 vcan0；can_ids 统一为 int）
    normalized = dict(case)
    normalized["interface"] = interface
    normalized["frames"] = actions
    normalized["can_ids"] = normalized_ids

    return normalized


# ----------------------------------------------------------------------
# canonical replay identity
# ----------------------------------------------------------------------

#: 参与 replay identity 的字段（= 决定"发什么帧"的全部信息）
REPLAY_IDENTITY_FIELDS = ("case_id", "seed", "mutation", "interface", "can_ids",
                          "frames")


def canonical_identity(case: Dict) -> str:
    """
    生成 canonical replay identity 字符串。

    **刻意排除** ``result``（timestamp / kernel logs / observed response /
    process state）——它们是执行结果，不能影响 testcase 本身。
    因此同一个 case 无论跑多少次，identity 都相同。
    """
    validated = validate_case(case)
    parts = []

    for field in REPLAY_IDENTITY_FIELDS:
        if field == "frames":
            rendered = []

            for a in validated["frames"]:
                if a["type"] == "inject":
                    rendered.append(
                        f"inject:{a['id']:03X}:{a['data'].hex()}:{a['delay_ms']}"
                    )
                elif a["type"] == "wait":
                    rendered.append(f"wait:{a['delay_ms']}")
                else:
                    rendered.append(
                        f"await_fc:{a['timeout_ms']}:{a['block_size']}:{a['stmin']}"
                    )

            parts.append("frames=[" + ",".join(rendered) + "]")
        elif field == "mutation":
            mut = validated["mutation"]
            parts.append(
                "mutation="
                + ";".join(f"{k}={mut[k]}" for k in sorted(mut) if k != "label")
            )
        elif field == "can_ids":
            ids = validated["can_ids"]
            parts.append(
                f"can_ids=inject:0x{ids['inject']:03X},observe:0x{ids['observe']:03X}"
            )
        else:
            parts.append(f"{field}={validated[field]}")

    return "|".join(parts)


def classify_result(process_alive: bool, response_kind: str, recovery: str,
                    errno: Optional[int], kernel_anomaly: bool,
                    hung: bool) -> str:
    """
    按**固定优先级顺序**给出分类（P1 §21）。

    注意：``recovery`` 不参与 class 选择，而是独立字段 —— 这样
    ``class=ECU_CRASH`` 与 ``recovery=FAIL`` 可以同时表达，
    不会像"把 RECOVERED 当成唯一 class"那样丢失 crash/error 信息。
    """
    if kernel_anomaly:
        return KERNEL_ANOMALY

    if not process_alive:
        return ECU_CRASH

    # 进程存活，但恢复正常请求失败 → 状态污染（硬 oracle，不能被 alive 掩盖）
    if recovery == RECOVERY_FAIL:
        return STATE_CORRUPTION

    if hung:
        return HANG

    if errno is not None:
        return TRANSPORT_ERROR

    if response_kind == "uds":
        return UDS_RESPONSE

    if response_kind == "none":
        return DISCARD

    return NORMAL


def is_new_errno(errno: Optional[int]) -> bool:
    """errno 是否属于**尚未 containment** 的新错误（P1 §38：只记录，不自动加白名单）。"""
    return errno is not None and errno not in CONTAINED_ERRNOS


def to_jsonable(case: Dict) -> Dict:
    """把 bytes/元组转成可 JSON 序列化的形式（data → 十六进制字符串）。"""
    out = dict(case)
    frames = []

    for a in case.get("frames", []):
        item = dict(a)

        if item.get("type") == "inject":
            item["id"] = f"0x{item['id']:03X}"
            item["data"] = bytes(item["data"]).hex(" ").upper()

        frames.append(item)

    out["frames"] = frames

    return out


def _hex_to_bytes(text: str, where: str) -> bytes:
    """
    把 testcase JSON 里的十六进制字符串还原为 bytes。

    这里刻意**不用** ``parsers.parse_hex_bytes``：它把 ``-`` 也当作分隔符
    予以剔除，而规范输出的分组写法（``"10 16 22"``）在剔除 ``-`` 语义下
    会产生歧义。JSON 里的形式是唯一确定的
    （``bytes.hex(" ")`` 的输出），因此用 ``bytes.fromhex`` 直接还原，
    并自行保证 DLC 合法。
    """
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        raise SchemaError(
            f"{where}: 十六进制解析失败：{text!r}（应为 '10 16 22' 形式）"
        ) from None

    if len(raw) > CAN_DATA_MAX_LEN:
        raise SchemaError(
            f"{where}: 经典 CAN 数据最多 {CAN_DATA_MAX_LEN} 字节，收到 {len(raw)}"
        )

    return raw


def from_jsonable(case: Dict) -> Dict:
    """把 JSON 读回的 testcase 还原成内部形式（data → bytes, can_ids → int）。"""
    out = dict(case)

    can_ids = dict(out.get("can_ids") or {})

    for key in ("inject", "observe"):
        if key in can_ids:
            can_ids[key] = parse_can_id(str(can_ids[key]))

    out["can_ids"] = can_ids

    frames = []

    for index, a in enumerate(case.get("frames", [])):
        item = dict(a)

        if item.get("type") == "inject":
            raw = item.get("data", "")

            if isinstance(raw, str):
                item["data"] = _hex_to_bytes(raw, f"frames[{index}]")
            else:
                item["data"] = _validate_bytes(raw, f"frames[{index}]")

            item["id"] = parse_can_id(str(item["id"]))

        frames.append(item)

    out["frames"] = frames

    return out
