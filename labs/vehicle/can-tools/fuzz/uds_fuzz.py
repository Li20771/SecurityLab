#!/usr/bin/env python3
"""
UDS fuzz v1 —— campaign front-end（**不是第二个 fuzz engine**）

职责边界（Frozen Contract v3 §12）：

    uds_fuzz.py = UDS 层 generator / campaign front-end
    isotp_fuzz.py = shared execution engine（**零修改**）

本模块**只**负责：

    seed 选择 → mutation spec 生成 → case 构造（case_schema）
      → campaign 确定性排序（--seed） → fingerprint 去重
      → 调用既有 isotp_fuzz.execute_case → campaign summary

**绝不**实现（全部来自引擎）：

    EcuProcess / RawChannel / oracle / replay / cleanup / recovery /
    dmesg 解析 / kernel oracle / 分类优先级

完整流水（Frozen Contract §11）：

    seed → mutation spec → mutated UDS request → ISO-TP SF encoding
         → frames[] → frames_fingerprint() → campaign seen set
         → duplicate → discard（不执行 ECU、不写 result）
         → unique    → execute_case()

确定性（Frozen Contract §9/§10）：

    --seed       campaign 候选排序/抽样种子（int，默认 12345）
    排序 key     ( sha256(f"{campaign_seed}:{case_id}").hexdigest(), case_id )  升序
    --max-cases  上述确定性排序的**前缀**

    保证：same corpus + same specs + same --seed + same --max-cases
          → same identities + same order

    **不使用任何 PRNG**（不用 random / shuffle / set 迭代序）。

关于 identity 与 fingerprint 的分工（Frozen Contract §11）：

    canonical_identity()   = case identity（帧 + 来源）—— **不用于去重**
    frames_fingerprint()   = campaign execution dedup —— **不用于 identity**

    本模块不修改 ``canonical_identity()``。
"""

import argparse
import hashlib
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
import isotp_fuzz as engine  # noqa: E402  （shared execution engine）
import isotp_mutators  # noqa: E402
import uds_corpus  # noqa: E402
import uds_mutators as um  # noqa: E402

__all__ = [
    "DEFAULT_CAMPAIGN_SEED",
    "build_candidates",
    "dedup_candidates",
    "order_key",
    "select_candidates",
    "run_campaign",
]

#: ``--seed`` 默认值（与既有引擎的默认 master_seed 一致）。
DEFAULT_CAMPAIGN_SEED = 12345


# ----------------------------------------------------------------------
# 生成
# ----------------------------------------------------------------------


def build_candidates(seeds: Optional[List[str]] = None,
                     dimensions: Optional[List[str]] = None) -> Tuple[List[Dict], List[Dict]]:
    """
    生成全部候选（**未去重、未排序**，顺序 = corpus 顺序 × spec 顺序）。

    返回 ``(candidates, skipped)``：

    - ``candidate``：``{case, seed_id, dimension, label, fingerprint}``
    - ``skipped``：``{seed_id, dimension, label, reason}`` —— 不适用 / no-op

    两者都**不执行 ECU**。
    """
    candidates: List[Dict] = []
    skipped: List[Dict] = []

    for seed in uds_corpus.iter_seeds():
        if seed.get("reference_only"):
            continue

        if seeds and seed["seed_id"] not in seeds:
            continue

        for spec in um.mutation_specs_for_seed(seed):
            if dimensions and spec["dimension"] not in dimensions:
                continue

            frames, reason = um.build_mutated_frames(seed, spec)

            if frames is None:
                skipped.append({
                    "seed_id": seed["seed_id"],
                    "dimension": spec["dimension"],
                    "label": spec["label"],
                    "reason": reason,
                })
                continue

            case_id = cs.make_case_id(seed["seed_id"], spec["dimension"], spec["label"])
            mutation = {
                "dimension": spec["dimension"],
                "params": dict(spec["params"]),
                "label": spec["label"],
            }

            case = cs.build_case(
                case_id=case_id,
                seed_id=seed["seed_id"],
                mutation=mutation,
                actions=frames,
                category=seed["category"],
                summary=f"uds {seed['seed_id']} / {spec['dimension']} / {spec['label']}",
            )

            candidates.append({
                "case": case,
                "seed_id": seed["seed_id"],
                "dimension": spec["dimension"],
                "label": spec["label"],
                "fingerprint": isotp_mutators.frames_fingerprint(frames),
            })

    return candidates, skipped


# ----------------------------------------------------------------------
# 去重（campaign 执行层）
# ----------------------------------------------------------------------


def dedup_candidates(candidates: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """
    按 ``frames_fingerprint`` 去重：**第一个**产生该 fingerprint 的候选保留
    provenance，后续同 fingerprint 的候选被丢弃（不执行 ECU、不写 result）。
    """
    seen = set()
    unique: List[Dict] = []
    duplicates: List[Dict] = []

    for candidate in candidates:
        key = candidate["fingerprint"]

        if key in seen:
            duplicates.append(candidate)
            continue

        seen.add(key)
        unique.append(candidate)

    return unique, duplicates


# ----------------------------------------------------------------------
# 确定性排序与预算
# ----------------------------------------------------------------------


def order_key(case_id: str, campaign_seed: int) -> Tuple[str, str]:
    """
    campaign 候选的确定性排序 key（Frozen Contract §9）。

    只依赖 ``(campaign_seed, case_id)``，**不含**任何随机源 / set 迭代序 /
    dict 哈希序，因此跨进程、跨运行完全可复现。
    """
    digest = hashlib.sha256(f"{campaign_seed}:{case_id}".encode("utf-8")).hexdigest()

    return (digest, case_id)


def order_candidates(candidates: List[Dict], campaign_seed: int) -> List[Dict]:
    """按 :func:`order_key` 升序排序（不改动入参）。"""
    return sorted(candidates, key=lambda c: order_key(c["case"]["case_id"],
                                                      campaign_seed))


def select_candidates(unique: List[Dict], campaign_seed: int,
                      max_cases: Optional[int] = None) -> List[Dict]:
    """
    去重后的候选 → 确定性排序 → 取前 ``max_cases``（``None`` = 全部）。

    ``--max-cases`` 因此是**确定性前缀**，而不是"依赖迭代顺序的截断"。
    """
    ordered = order_candidates(unique, campaign_seed)

    if max_cases is None:
        return ordered

    return ordered[:max(0, max_cases)]


# ----------------------------------------------------------------------
# campaign
# ----------------------------------------------------------------------


def run_campaign(campaign_seed: int = DEFAULT_CAMPAIGN_SEED,
                 max_cases: Optional[int] = None,
                 out_dir: Optional[str] = None,
                 seeds: Optional[List[str]] = None,
                 dimensions: Optional[List[str]] = None,
                 list_only: bool = False) -> Dict:
    """
    执行一次 UDS campaign。

    执行能力**全部**来自 ``isotp_fuzz.execute_case``（每 case fresh ECU、
    既有 timeout / 观察窗口 / recovery probe / kernel oracle / cleanup）。
    本函数不做任何生命周期管理，也不自行分类。
    """
    candidates, skipped = build_candidates(seeds=seeds, dimensions=dimensions)
    unique, duplicates = dedup_candidates(candidates)
    selected = select_candidates(unique, campaign_seed, max_cases)

    summary = {
        "layer": "uds-single-request",
        # 刻意**不叫** ``schema_version``：replay 的目录扫描把"含 schema_version
        # 但不完整"的 JSON 视为**可能损坏的 testcase**（而不是 metadata），
        # 因此 summary 用另一个名字，避免被当成 case 去重放。
        "case_schema_version": cs.SCHEMA_VERSION,
        "campaign_seed": campaign_seed,
        "max_cases": max_cases,
        "generated": len(candidates),
        "skipped": len(skipped),
        "unique": len(unique),
        "duplicates": len(duplicates),
        "selected": len(selected),
        "order": [c["case"]["case_id"] for c in selected],
        "classes": {},
    }

    if list_only:
        return summary

    classes: Dict[str, int] = {}

    for candidate in selected:
        case = candidate["case"]
        result = engine.execute_case(
            case,
            out_dir=out_dir,
            keep_artifacts=bool(out_dir),
        )
        cls = result["result"]["class"]
        classes[cls] = classes.get(cls, 0) + 1

    summary["classes"] = classes

    if out_dir:
        _write_summary(summary, out_dir)

    return summary


def _write_summary(summary: Dict, out_dir: str) -> str:
    """
    把 campaign summary 落盘（``uds-campaign-summary.json``）。

    该文件**不是** testcase（不含 ``schema_version`` / ``frames`` 等字段），
    因此既有 ``replay.py`` 的目录扫描会把它当作 metadata 跳过 —— 由
    ``test_uds_fuzz_vcan`` 固化该行为。
    """
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "uds-campaign-summary.json")

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)

    return path


def _print_summary(summary: Dict) -> None:
    print("=== UDS fuzz v1 campaign ===")
    print(f"  layer           : {summary['layer']}")
    print(f"  campaign_seed   : {summary['campaign_seed']}")
    print(f"  max_cases       : {summary['max_cases']}")
    print(f"  generated       : {summary['generated']}")
    print(f"  skipped         : {summary['skipped']}（不适用 / no-op）")
    print(f"  unique (dedup)  : {summary['unique']}")
    print(f"  duplicates      : {summary['duplicates']}")
    print(f"  selected        : {summary['selected']}")

    if summary["classes"]:
        print("  classes:")

        for name in cs.CLASS_PRIORITY:
            if summary["classes"].get(name):
                print(f"    {name:<18} {summary['classes'][name]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="UDS fuzz v1 —— 单请求变异 campaign（复用 ISO-TP fuzz 引擎）"
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_CAMPAIGN_SEED,
                        help=f"campaign 排序/抽样种子（默认 {DEFAULT_CAMPAIGN_SEED}）")
    parser.add_argument("--max-cases", type=int, default=None,
                        help="确定性排序的前 N 条（默认全部）")
    parser.add_argument("--out-dir", default=None,
                        help="落盘目录（省略则不落盘）")
    parser.add_argument("--seeds", nargs="*", default=None,
                        help="只针对这些 seed_id")
    parser.add_argument("--dimensions", nargs="*", default=None,
                        choices=list(um.ALL_DIMENSIONS),
                        help="只针对这些维度")
    parser.add_argument("--list-only", action="store_true",
                        help="只打印确定性排序结果，不执行 ECU")

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    summary = run_campaign(
        campaign_seed=args.seed,
        max_cases=args.max_cases,
        out_dir=args.out_dir,
        seeds=args.seeds,
        dimensions=args.dimensions,
        list_only=args.list_only,
    )

    _print_summary(summary)

    if args.list_only:
        print()
        print("  order:")

        for case_id in summary["order"]:
            print(f"    {case_id}")

    if args.out_dir:
        print(f"\n  summary -> "
              f"{os.path.join(args.out_dir, 'uds-campaign-summary.json')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
