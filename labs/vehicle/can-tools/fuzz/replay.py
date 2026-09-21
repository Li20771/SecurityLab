#!/usr/bin/env python3
"""
ISO-TP stateful fuzz —— replay（精确复现已保存的 testcase）

用法::

    python3 replay.py <case.json>
    python3 replay.py <case.json> --repeat 3
    python3 replay.py fuzz-out/ --all

设计要求（P1 §30/§31）：

- replay **不重新生成 mutation**，而是读取 ``frames[]`` **原样发送**。
  因此它是 exact reproduction，而不是 "same seed regeneration"。
- replay 前后仍然执行 recovery probe（``10 01``），
  所以它复现的不只是 CAN 帧，还有**行为**。
- 使用与生成时完全相同的 ``canonical_identity()`` 做一致性校验，
  以证明 replay 的输入与原始 testcase 完全等价。
"""

import argparse
import glob
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.dirname(_HERE)

for _p in (_TOOLS_DIR, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import case_schema as cs  # noqa: E402
import isotp_fuzz as engine  # noqa: E402


def load_case(path: str) -> Dict:
    """读取 testcase JSON 并还原成内部形式。"""
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    case = cs.from_jsonable(raw)
    cs.validate_case(case)

    return case


# ----------------------------------------------------------------------
# 目录扫描：区分 testcase 与 summary / metadata
# ----------------------------------------------------------------------

#: 判定 testcase 所需的顶层结构字段（存在性即可，取值由 validate_case 负责）
_TESTCASE_REQUIRED_FIELDS = ("schema_version", "case_id", "seed", "interface",
                             "can_ids", "frames", "mutation")


def is_testcase_file(path: str) -> bool:
    """
    判断一个 JSON 文件是否是可重放的 testcase。

    ``fuzz-out/`` 里同时存在两类文件：

    * **testcase** —— 由 ``isotp_fuzz._persist_case()`` 写出，一个 case 一个文件；
    * **summary / metadata** —— 例如 ``smoke-summary.json``，是整轮的汇总，
      结构上不是 testcase。

    目录扫描必须只取前者。此处的判定是**存在性 + 严格校验**两级：

    1. 顶层必须是对象，且含 :data:`_TESTCASE_REQUIRED_FIELDS` 全部字段；
    2. 必须能通过 ``case_schema.validate_case()``（含 ``schema_version`` 取值、
       ``case_id`` 形态、interface / CAN ID / 帧校验）。

    只做第 1 步不够：一个损坏的 testcase 也应该被识别为 testcase 并在
    重放时报错，而不是被静默当成 metadata 跳过。因此第 2 步只用于**确认
    它是 testcase**，不吞掉它的校验错误——真正的错误留给 ``load_case()``
    在重放时抛出，从而不会被掩盖。

    本函数**不修改**任何 schema，也不放宽 ``canonical_identity``。
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False

    if not isinstance(raw, dict):
        return False

    if not any(field in raw for field in _TESTCASE_REQUIRED_FIELDS):
        # 连一个 testcase 特征字段都没有 → 是 summary / metadata
        return False

    if not all(field in raw for field in _TESTCASE_REQUIRED_FIELDS):
        # 部分像 testcase 但结构不全 → 交给重放阶段报明确错误
        return True

    try:
        cs.validate_case(cs.from_jsonable(raw))
    except Exception:  # noqa: BLE001 - 校验失败仍视为 testcase，错误留给重放阶段报
        return True

    return True


def collect_cases(directory: str) -> Tuple[List[str], List[str]]:
    """
    扫描目录，返回 ``(testcase_paths, ignored_paths)``。

    ``ignored_paths`` 是形状不像 testcase 的 JSON（如 ``smoke-summary.json``），
    它们**不是**错误，只是为了透明而回报给调用方。
    """
    cases: List[str] = []
    ignored: List[str] = []

    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        if is_testcase_file(path):
            cases.append(path)
        else:
            ignored.append(path)

    return cases, ignored


def replay_case(case: Dict, repeat: int = 1,
                keep_artifacts: bool = False) -> List[Dict]:
    """
    精确重放一个 case ``repeat`` 次。

    返回每次执行的 case（含新的 result）。
    """
    original_identity = cs.canonical_identity(case)
    original_class = (case.get("result") or {}).get("class")

    outcomes: List[Dict] = []

    for run_index in range(repeat):
        replayed = cs.from_jsonable(cs.to_jsonable(case))

        identity = cs.canonical_identity(replayed)

        if identity != original_identity:
            raise cs.SchemaError(
                "replay identity 与原始 testcase 不一致——"
                "说明 frames/mutation 在序列化往返中发生了变化"
            )

        # 清掉旧 result，避免它影响本次执行
        replayed["result"] = {}

        result = engine.execute_case(replayed, out_dir=None,
                                     keep_artifacts=keep_artifacts)
        result["replay_run"] = run_index + 1
        result["replay_identity_match"] = True
        result["original_class"] = original_class
        outcomes.append(result)

    return outcomes


def _print_outcome(case: Dict, outcome: Dict, show_frames: bool = False) -> None:
    res = outcome["result"]

    print(f"  case_id   : {outcome['case_id']}")
    print(f"  identity  : OK（与原始 testcase 等价）")
    print(f"  run       : {outcome.get('replay_run')}")
    print(f"  class     : {res['class']}"
          f"   (原始: {outcome.get('original_class')})")
    print(f"  process   : alive={res['process_alive']} rc={res['process_returncode']}")
    print(f"  response  : {res['response_kind']}  {res['response']}")
    print(f"  errno     : {res['errno']}  new_errno={res['new_errno']}")
    print(f"  recovery  : {res['recovery']}  {res.get('recovery_response')}")
    print(f"  kernel    : {res['kernel_events'] or '(no anomaly)'}")
    print(f"  duration  : {res['duration_ms']} ms")

    if res.get("crash_signature"):
        print(f"  crash sig : {res['crash_signature']}")

    if show_frames:
        print("  frames    :")

        for action in outcome["frames"]:
            if action["type"] == "inject":
                print(f"      inject {action['id']:03X}  "
                      f"{bytes(action['data']).hex(' ')}  "
                      f"delay={action.get('delay_ms')}ms")
            elif action["type"] == "wait":
                print(f"      wait   {action.get('delay_ms')}ms")
            else:
                print(f"      await_fc timeout={action.get('timeout_ms')}ms "
                      f"bs={action.get('block_size')} stmin={action.get('stmin')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="精确重放已保存的 fuzz testcase")
    parser.add_argument("path", help="case JSON 文件或包含 case 的目录")
    parser.add_argument("--repeat", type=int, default=1,
                        help="重复次数（用于确定性确认）")
    parser.add_argument("--all", action="store_true",
                        help="path 是目录时，重放目录下所有 case")
    parser.add_argument("--show-frames", action="store_true",
                        help="打印将被重放的帧序列")
    parser.add_argument("--no-artifacts", action="store_true",
                        help="不落盘（默认也不落盘，此开关保留语义）")

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    ignored: List[str] = []

    if os.path.isdir(args.path):
        if not args.all:
            print("目录输入需要 --all", file=sys.stderr)
            return 2

        paths, ignored = collect_cases(args.path)
    else:
        paths = [args.path]

    for path in ignored:
        print(f"[ignore] {os.path.basename(path)}：不是 testcase（summary / metadata），已跳过")

    if not paths:
        print("没有找到 case 文件", file=sys.stderr)
        return 2

    classes: Dict[str, int] = {}
    mismatches = 0

    for path in paths:
        try:
            case = load_case(path)
        except (OSError, json.JSONDecodeError, cs.SchemaError) as exc:
            print(f"[skip] {path}: {exc}")
            mismatches += 1
            continue

        print(f"=== replay {os.path.basename(path)} ===")

        outcomes = replay_case(case, repeat=max(1, args.repeat))

        for outcome in outcomes:
            _print_outcome(case, outcome, show_frames=args.show_frames)
            cls = outcome["result"]["class"]
            classes[cls] = classes.get(cls, 0) + 1

        # 多次执行的分类一致性
        distinct = {o["result"]["class"] for o in outcomes}

        if args.repeat > 1 and len(distinct) > 1:
            print(f"  !! 非确定性：{args.repeat} 次运行得到 {sorted(distinct)}")
            mismatches += 1

        print()

    print("=== summary ===")

    for name in cs.CLASS_PRIORITY:
        if classes.get(name):
            print(f"  {name:<18} {classes[name]}")

    print(f"  {'issue(s)':<18} {mismatches}")

    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
