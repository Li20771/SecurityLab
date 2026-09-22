#!/usr/bin/env bash
#
# verify_uds_fuzz_smoke.sh —— UDS fuzz v1 最小可复现入口
#
# 这是 ISO-TP 层 verify_fuzz_smoke.sh 的**平行**脚本（UDS 请求层），
# 不复制其 ISO-TP 语义。完整 campaign 属实验行为，不进默认 CI；
# 本脚本只做最小闭环，验证六件事：
#
#   1. corpus  ：11 个 v1 IN 服务都有 seed，且全部 ≤7 字节
#   2. mutation：spec 生成 / no-op 拒绝 / **最终请求一律 ≤7**
#   3. schema  ：所有生成的 case 通过 case_schema.validate_case
#   4. campaign：`--seed` + `--max-cases` 的确定性（同参数 → 同顺序）
#                + fingerprint 去重生效（unique < generated）
#   5. oracle  ：CLASS_PRIORITY **未被改动**（v1 不新增 class）
#   6. replay  ：落盘 case 被既有 replay.py 精确重放，issue(s)=0，
#                且 campaign summary 被识别为 metadata 而跳过
#
# 依赖：vcan0 已存在且 UP（**独占**，勿与其它 CAN 测试并行）；
#       Python venv 含 python-can / can-isotp。
# 产物全部写入 mktemp 临时目录，结束即删除；不提交生成物。
#
# 运行：bash scripts/repro/verify_uds_fuzz_smoke.sh
# 退出码：0 = 全过；1 = 验证失败；2 = 环境不可用（vcan0/依赖缺失）。

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
FUZZ_DIR="$ROOT/labs/vehicle/can-tools/fuzz"
TESTS_DIR="$ROOT/labs/vehicle/can-tools/tests"

PASS=0
FAIL=0

ok()   { echo "[ OK ] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
OUT="$WORK/cases"

echo "workdir: $WORK"

# ---- 依赖检查（缺失 = 环境不可用，退出 2，不是验证失败）----
if [ -n "${PYTHON:-}" ] && [ -x "${PYTHON}" ]; then
    PYTHON_BIN="$PYTHON"
elif [ -x "$HOME/.venvs/network/bin/python" ]; then
    PYTHON_BIN="$HOME/.venvs/network/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "[MISS] Python 解释器未找到"
    exit 2
fi

if ! ip link show vcan0 >/dev/null 2>&1; then
    echo "[MISS] vcan0 不存在（先：sudo systemctl start vcan0.service）"
    exit 2
fi
if ! ip link show vcan0 | grep -q "UP"; then
    echo "[MISS] vcan0 存在但未 UP"
    exit 2
fi
ok "vcan0 UP（本脚本独占总线，请勿并发其它 CAN 测试）"

if ! "$PYTHON_BIN" -c "import can, isotp" >/dev/null 2>&1; then
    echo "[MISS] venv 缺少 python-can / can-isotp"
    exit 2
fi
ok "Python venv：python-can / can-isotp 可导入（$PYTHON_BIN）"

# ----------------------------------------------------------------------
# 1–5. offline 校验（corpus / mutation / schema / campaign / oracle）
# ----------------------------------------------------------------------

"$PYTHON_BIN" - "$FUZZ_DIR" "$TESTS_DIR" <<'PY' > "$WORK/offline.out" 2>&1
import json
import os
import sys

fuzz_dir, tests_dir = sys.argv[1], sys.argv[2]

for path in (fuzz_dir, tests_dir, os.path.dirname(fuzz_dir)):
    if path not in sys.path:
        sys.path.insert(0, path)

import case_schema as cs
import isotp_mutators
import uds_corpus
import uds_fuzz
import uds_mutators as um

report = {}

# ---- 1. corpus ----
services = {s["service"] for s in uds_corpus.CORPUS}
missing = [f"0x{s:02X}" for s in uds_corpus.V1_IN_SIDS if s not in services]
assert not missing, f"v1 IN 服务缺 seed: {missing}"

for seed in uds_corpus.CORPUS:
    n = len(seed["uds_request"])
    assert 1 <= n <= 7, f"{seed['seed_id']}: 长度 {n} 越界"

report["corpus_seeds"] = len(uds_corpus.CORPUS)

# ---- 2. mutation：no-op 拒绝 + 最终长度 ≤7 ----
built = skipped = 0

for seed in uds_corpus.CORPUS:
    seed_fp = isotp_mutators.frames_fingerprint(
        uds_corpus.encode_uds_request(seed["uds_request"]))

    for spec in um.mutation_specs_for_seed(seed):
        mutated, _ = um.apply_mutation(seed, spec)

        if mutated is None:
            skipped += 1
            continue

        assert len(mutated) <= 7, f"{seed['seed_id']}/{spec['label']}: 超长"
        frames, reason = um.build_mutated_frames(seed, spec)

        if frames is None:
            skipped += 1
            continue

        # 候选不得与其**自身** seed 完全相同（no-op 必须已被拒）
        assert isotp_mutators.frames_fingerprint(frames) != seed_fp, \
            f"{seed['seed_id']}/{spec['label']}: no-op 未被拒绝"
        built += 1

report["mutation_built"] = built
report["mutation_skipped"] = skipped
assert built > 0, "没有任何可用变异"

# ---- 3. schema ----
candidates, _ = uds_fuzz.build_candidates()
assert candidates, "候选为空"

for candidate in candidates:
    case = candidate["case"]
    cs.validate_case(case)
    frame = case["frames"][0]["data"]
    assert uds_corpus.sf_invariant_ok(frame), \
        f"{case['case_id']}: SF 不变量违反"

report["cases_validated"] = len(candidates)

# ---- 4. campaign：确定性与去重 ----
unique, duplicates = uds_fuzz.dedup_candidates(candidates)
assert len(unique) < len(candidates), "去重未生效（本 corpus 应产生重复帧序列）"

first = uds_fuzz.select_candidates(unique, 12345, 7)
second = uds_fuzz.select_candidates(unique, 12345, 7)
assert [c["case"]["case_id"] for c in first] == \
       [c["case"]["case_id"] for c in second], "同 --seed 两次选择不一致"

# --max-cases 必须是确定性前缀
full = uds_fuzz.select_candidates(unique, 12345)
assert [c["case"]["case_id"] for c in first] == \
       [c["case"]["case_id"] for c in full[:7]], "--max-cases 不是前缀"

report["generated"] = len(candidates)
report["unique"] = len(unique)
report["duplicates"] = len(duplicates)

# ---- 5. oracle 未被改动 ----
frozen = ("KERNEL_ANOMALY", "ECU_CRASH", "STATE_CORRUPTION", "HANG",
          "TRANSPORT_ERROR", "UDS_RESPONSE", "DISCARD", "NORMAL")
assert tuple(cs.CLASS_PRIORITY) == frozen, "CLASS_PRIORITY 被改动"
assert cs.SCHEMA_VERSION == 1, "schema_version 被改动"

report["class_priority"] = "unchanged"

# ---- replay roundtrip ----
case = candidates[0]["case"]
restored = cs.from_jsonable(cs.to_jsonable(case))
assert cs.canonical_identity(restored) == cs.canonical_identity(case), \
    "replay roundtrip 改变了 identity"

report["identity_roundtrip"] = "stable"

print(json.dumps(report, sort_keys=True))
PY
ORC=$?

if [ "$ORC" -eq 0 ]; then
    ok "offline 校验通过：$(cat "$WORK/offline.out")"
else
    fail "offline 校验失败"
    cat "$WORK/offline.out"
fi

# ----------------------------------------------------------------------
# 6. vCAN smoke：3 个 case 真打 ECU
# ----------------------------------------------------------------------

"$PYTHON_BIN" "$FUZZ_DIR/uds_fuzz.py" --max-cases 3 --seed 12345 \
    --out-dir "$OUT" > "$WORK/campaign.out" 2>&1
CRC=$?

if [ "$CRC" -eq 0 ]; then
    ok "UDS campaign 执行（rc=0，3 case）"
else
    fail "UDS campaign 执行失败 rc=$CRC"
    tail -8 "$WORK/campaign.out"
fi

if grep -q "selected        : 3" "$WORK/campaign.out"; then
    ok "campaign selected = 3"
else
    fail "campaign selected 不是 3"
    grep "selected" "$WORK/campaign.out"
fi

if [ -f "$OUT/uds-campaign-summary.json" ]; then
    ok "uds-campaign-summary.json 落盘"
else
    fail "uds-campaign-summary.json 未落盘"
fi

# case 落盘 + result 完整性 + kernel oracle 三态诚实
"$PYTHON_BIN" - "$OUT" <<'PY' > "$WORK/case_check.out" 2>&1
import json
import os
import sys

out_dir = sys.argv[1]
required = ("class", "process_alive", "recovery", "kernel_oracle", "hung")

names = [n for n in sorted(os.listdir(out_dir))
         if n.endswith(".json") and n != "uds-campaign-summary.json"]

assert len(names) == 3, f"case JSON 数量 {len(names)} != 3"

for name in names:
    case = json.load(open(os.path.join(out_dir, name), encoding="utf-8"))
    res = case.get("result") or {}

    for field in required:
        assert field in res, f"{name}: result.{field} 缺失"

    oracle = res["kernel_oracle"]
    assert oracle in ("AVAILABLE", "UNAVAILABLE", "ERROR"), \
        f"{name}: kernel_oracle 非法 {oracle!r}"

    if oracle != "AVAILABLE":
        # fail-closed：oracle 不可用 ≠ 0 kernel event
        assert res.get("kernel_events") is None, \
            f"{name}: oracle={oracle} 但 kernel_events 不是 null"

print(f"case_ok={len(names)}")
PY
CRC2=$?

if [ "$CRC2" -eq 0 ]; then
    ok "case 落盘/schema/分类/oracle 三态校验通过：$(cat "$WORK/case_check.out")"
else
    fail "case 校验失败"
    cat "$WORK/case_check.out"
fi

# ----------------------------------------------------------------------
# 7. replay 精确重放（既有 replay.py，零修改）
# ----------------------------------------------------------------------

"$PYTHON_BIN" "$FUZZ_DIR/replay.py" "$OUT" --all > "$WORK/replay.out" 2>&1
RRC=$?

if [ "$RRC" -eq 0 ]; then
    ok "replay --all（rc=0）"
else
    fail "replay --all 失败 rc=$RRC"
    tail -8 "$WORK/replay.out"
fi

if grep -q "issue(s)           0" "$WORK/replay.out"; then
    ok "replay issue(s)=0（3 case 精确重放）"
else
    fail "replay issue(s) 不为 0"
    grep "issue(s)" "$WORK/replay.out"
fi

if grep -q "uds-campaign-summary.json：不是 testcase" "$WORK/replay.out"; then
    ok "campaign summary 被 replay 正确识别为 metadata 并跳过"
else
    fail "campaign summary 未被识别为 metadata"
    grep "ignore" "$WORK/replay.out" || echo "(无 ignore 行)"
fi

# ----------------------------------------------------------------------

echo
echo "========================================"
echo " UDS fuzz smoke summary"
echo "========================================"
echo "PASS: $PASS"
echo "FAIL: $FAIL"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi

exit 0
