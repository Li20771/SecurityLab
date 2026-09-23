#!/usr/bin/env bash
#
# verify_uds_fuzz_smoke.sh —— UDS fuzz 最小可复现入口（v1 + OQ3 MF）
#
# 这是 ISO-TP 层 verify_fuzz_smoke.sh 的**平行**脚本（UDS 请求层），
# 不复制其 ISO-TP 语义。完整 campaign 属实验行为，不进默认 CI；
# 本脚本只做最小闭环。
#
# 本脚本**只调用**正式入口（uds_corpus / uds_mutators / uds_fuzz / replay），
# 不重新实现任何 FF/CF framing，也不重新实现 M1..M7 变异算法。
#
# 冻结契约（OQ3 Phase 1，size-class aware）：
#
#   L <= 7            -> 恰好一个 SF（v1 路径，逐字节未变）
#   8 <= L <= 128     -> canonical FF + CF
#   L > 128           -> reject（不生成、不截断）
#   frames <= 19      -> MAX_FRAMES_PER_CASE
#   mf_* = M1..M7     -> 仅位于 uds_len 维度，恰好七个
#
# 检查项（保持 v1 的 10 项 smoke contract 不变，内容扩展为 size-class aware：
# v1 的 SF regression **原样保留**，OQ3 的 MF smoke 叠加其上）：
#
#   1. corpus   ：v1 seed 仍 ≤7；MF seed 落在 8..128；各自不超自身 envelope
#   2. mutation ：spec 生成 / no-op 拒绝 / 结果不超**本 seed 的** envelope
#   3. bounds   ：L = 7/8/13/14/78/128 合法且分帧正确
#   3b.admission：L = 129/200/300 必须落在 **admission reject**（生成期抛出、
#                 不产生任何帧），而不是 runtime crash / transport failure /
#                 oracle failure
#   4. framing  ：canonical case 满足 Layer A；mf_* case 故意违反 Layer A
#   5. schema   ：所有 case 通过 case_schema.validate_case（Layer C 准入）
#   6. M1..M7   ：七类变异均被生成，且**逐项核对冻结传输语义**
#                 （M1-M4 改 FF 声明长度、M5 删末帧、M6/M7 改 CF 的 SN）；
#                 M6 的 CF#16 no-op 不被生成；目标确定
#   7. campaign ：`--seed` + `--max-cases` 的确定性（同参数 → 同顺序）
#                 + fingerprint 去重生效（unique < generated）
#   7b.no-op    ：canonical identity 不含 result；被判重项与保留项同指纹
#   8. oracle   ：CLASS_PRIORITY / SCHEMA_VERSION **未被改动**
#   9. golden   ：v1 的 947 case 与两个 digest 与冻结基准逐字一致
#  10. replay   ：落盘 case 被既有 replay.py 精确重放，issue(s)=0，
#                 且 campaign summary 被识别为 metadata 而跳过
#
# vcan 段（真实链路）另须证明 **MF basic**：8 字节（MF 下边界）的 canonical
# MF case 确实经 FF + CF 送达 ECU，且 ECU 正常处理 —— 进程存活、
# recovery probe 通过、无 errno、未挂死、分类不落在任何执行期失败类别。
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
# 1–9. offline 校验（corpus / mutation / bounds / framing / M1-M7 /
#                     schema / campaign / oracle / v1 golden）
# ----------------------------------------------------------------------

"$PYTHON_BIN" - "$FUZZ_DIR" "$TESTS_DIR" "$WORK" <<'PY' > "$WORK/offline.out" 2>&1
import hashlib
import json
import os
import sys

fuzz_dir, tests_dir, work_dir = sys.argv[1], sys.argv[2], sys.argv[3]

for path in (fuzz_dir, tests_dir, os.path.dirname(fuzz_dir)):
    if path not in sys.path:
        sys.path.insert(0, path)

import case_schema as cs
import isotp_corpus
import isotp_mutators
import uds_corpus
import uds_fuzz
import uds_mutators as um

#: v1 golden 冻结基准（OQ3 改动**之前**于 main = 30d2ca6 采集）。
#: 刻意写成本脚本自己的字面量：使本脚本成为**独立**见证者，
#: 而不是复述 offline 测试里的同一个常量。
V1_GOLDEN_DIGEST = \
    "33030de782f28b4fde8cab4469a65edc1e42b56f051be57250ca73dd65f9b464"
V1_SEED_IDS_DIGEST = \
    "5e1ac3419c78bd804bf413c14acb65c3644f29ec6b68ba5ce8ef51ef964cf993"
V1_CASE_COUNT = 947

report = {}

V1_LEN = uds_corpus.MAX_UDS_REQUEST_LEN
MF_LEN = uds_corpus.MAX_MF_UDS_REQUEST_LEN
MAX_FRAMES = uds_corpus.MAX_FRAMES_PER_CASE

# ---- 0. 冻结常量本身 ----
assert (V1_LEN, MF_LEN, MAX_FRAMES) == (7, 128, 19), \
    f"size-class 常量被改动: {V1_LEN}/{MF_LEN}/{MAX_FRAMES}"
assert len(um.MF_OPS) == 7 and len(set(um.MF_OPS)) == 7, \
    "mf_* op 集不是冻结的七个"
assert um.ALL_DIMENSIONS == ("uds_len", "uds_sid", "uds_sub", "uds_payload"), \
    "维度集合被改动（不得新增第五维）"

# ---- 1. corpus（size-class aware）----
services = {s["service"] for s in uds_corpus.CORPUS}
missing = [f"0x{s:02X}" for s in uds_corpus.V1_IN_SIDS if s not in services]
assert not missing, f"v1 IN 服务缺 seed: {missing}"

v1_seeds, mf_seeds = [], []

for seed in uds_corpus.CORPUS:
    n = len(seed["uds_request"])
    env = seed.get("envelope", V1_LEN)

    assert env in (V1_LEN, MF_LEN), f"{seed['seed_id']}: 未知 envelope {env}"
    assert 1 <= n <= env, f"{seed['seed_id']}: 长度 {n} 超出 envelope {env}"

    if env == V1_LEN:
        assert n <= V1_LEN, f"{seed['seed_id']}: v1 seed 长度 {n} > {V1_LEN}"
        v1_seeds.append(seed)
    else:
        # MF seed 必须真的落在 MF size class（下边界是 8）
        assert V1_LEN < n <= MF_LEN, f"{seed['seed_id']}: MF seed 长度 {n} 越界"
        mf_seeds.append(seed)

assert v1_seeds and mf_seeds, "v1 / MF 两侧都必须有 seed"

report["corpus_seeds"] = len(uds_corpus.CORPUS)
report["v1_seeds"] = len(v1_seeds)
report["mf_seeds"] = len(mf_seeds)

# ---- 2. mutation：no-op 拒绝 + 结果不超**本 seed 的** envelope ----
built = skipped = 0

for seed in uds_corpus.CORPUS:
    env = seed.get("envelope", V1_LEN)
    seed_fp = isotp_mutators.frames_fingerprint(
        uds_corpus.encode_uds_request(seed["uds_request"]))

    for spec in um.mutation_specs_for_seed(seed):
        mutated, _ = um.apply_mutation(seed, spec)

        if mutated is None:
            skipped += 1
            continue

        assert len(mutated) <= env, \
            f"{seed['seed_id']}/{spec['label']}: {len(mutated)} 超出 envelope {env}"

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

# ---- 3. bounds：size-class 边界（直接调用正式 encoder）----
#    7   SF 上边界        8   MF 下边界
#    13  恰好一个 CF      14  需要第二个 CF
#    78  corpus 最长 seed 128 envelope 上边界
for length in (7, 8, 13, 14, 78, 128):
    raw = bytes([0x22]) + bytes(length - 1)
    frames = uds_corpus.encode_uds_request(raw)
    first = bytes(frames[0]["data"])

    assert len(frames) <= MAX_FRAMES, \
        f"L={length}: 帧数 {len(frames)} > {MAX_FRAMES}"

    if length <= V1_LEN:
        assert len(frames) == 1, f"L={length}: 应为 SF 却得到 {len(frames)} 帧"
        assert (first[0] & 0xF0) == isotp_corpus.PCI_SF, f"L={length}: 首帧不是 SF"
        assert (first[0] & 0x0F) == length, \
            f"L={length}: SF PCI 长度 {first[0] & 0x0F} != payload 长度 {length}"
    else:
        assert len(frames) >= 2, f"L={length}: 应为 MF 却只有 {len(frames)} 帧"
        assert (first[0] & 0xF0) == isotp_corpus.PCI_FF, f"L={length}: 首帧不是 FF"

        declared = ((first[0] & 0x0F) << 8) | first[1]

        assert declared == length, \
            f"L={length}: FF 声明总长 {declared} != canonical payload 长度 {length}"

        # CF SN 必须符合冻结规则 SN(j) = j mod 16（j 从 1 起）
        for index, action in enumerate(frames[1:], start=1):
            sn = bytes(action["data"])[0] & 0x0F
            expect = uds_corpus.cf_sequence_number(index)

            assert sn == expect, f"L={length}: CF#{index} SN={sn} != {expect}"

    # Layer A 的权威判据来自生产校验器，不是本脚本重新推导
    assert uds_corpus.framing_invariant_ok(raw, frames), \
        f"L={length}: canonical framing 判据违反"

report["bound_accepted"] = [7, 8, 13, 14, 78, 128]

# ---- 3b. admission：<=128 accepted / >128 rejected ----
#
# 129 是本轮**最重要**的边界：它必须落在 **admission reject**，
# 而不是 runtime crash / transport failure / oracle failure。判据两条：
#
#   (a) 拒绝由**生成期**抛出（UdsEncodeError —— ValueError 族），
#       而不是执行期的运行错误；
#   (b) 拒绝是**硬拒绝**：抛异常且不返回任何帧 —— 没有帧就不可能进入
#       CAN injection，也就不可能被 classify_result() 分类成
#       ECU_CRASH / STATE_CORRUPTION / HANG / TRANSPORT_ERROR。
rejected = []

for length in (129, 200, 300):
    raw = bytes([0x22]) + bytes(length - 1)

    try:
        frames = uds_corpus.encode_uds_request(raw)
    except uds_corpus.UdsEncodeError as exc:
        assert isinstance(exc, ValueError), \
            f"L={length}: admission reject 不是 ValueError 族：{type(exc).__name__}"
        rejected.append(length)
    else:
        # 既没有异常、又给出了帧 —— 那是截断，不是 reject
        raise AssertionError(
            f"L={length} 超出 envelope 却被 canonical encoder 接受"
            f"（产生 {len(frames)} 帧）")

assert rejected == [129, 200, 300], f"拒绝集合不符：{rejected}"

# 边界相邻性：128 accepted 与 129 rejected 必须紧邻且方向相反
assert 128 in report["bound_accepted"] and 129 in rejected, \
    "128/129 未形成 accepted/reject 的相邻分界"

report["bound_rejected"] = rejected

# ---- 4/5/6. candidates：Layer A/B 分离 + Layer C 准入 + M1..M7 ----
candidates, _ = uds_fuzz.build_candidates()
assert candidates, "候选为空"

sf_cases = mf_cases = canonical_n = malformed_n = 0
op_seen = {op: 0 for op in um.MF_OPS}

for candidate in candidates:
    case = candidate["case"]
    cs.validate_case(case)

    frames = case["frames"]
    op = (case["mutation"].get("params") or {}).get("op")
    seed = uds_corpus.seed_by_id(candidate["seed_id"])

    # ---- Layer C 准入对**所有** case 成立（含 malformed）----
    assert 1 <= len(frames) <= MAX_FRAMES, \
        f"{case['case_id']}: 帧数越界 {len(frames)}"
    assert isotp_mutators.frames_fingerprint(frames), \
        f"{case['case_id']}: 不可指纹化"

    if len(frames) == 1:
        sf_cases += 1
    else:
        mf_cases += 1

    if op in um.MF_OPS:
        # ---- Layer B：**故意**破坏 transport framing ----
        # 既不得要求 Layer A 成立，也**不得**把 MF 首帧送进 sf_invariant_ok()
        malformed_n += 1
        op_seen[op] += 1

        assert not uds_corpus.framing_invariant_ok(
            bytes(seed["uds_request"]), frames), \
            f"{case['case_id']}: mf_* case 本应破坏 framing"

        # 首帧是 FF（或 M5 后仅剩的 FF），**绝不能**满足 SF 判据
        assert not uds_corpus.sf_invariant_ok(frames[0]["data"]), \
            f"{case['case_id']}: mf_* 首帧不应是合法 SF"
        continue

    # ---- Layer A：canonical ----
    canonical_n += 1

    # 用正式入口独立重建该 case，避免本脚本自己推导 framing
    mutated, _ = um.apply_mutation(seed, case["mutation"])
    assert mutated is not None, f"{case['case_id']}: 无法重建 mutated payload"

    rebuilt, _ = um.build_mutated_frames(seed, case["mutation"])
    assert rebuilt is not None, f"{case['case_id']}: 无法重建 frames"

    assert isotp_mutators.frames_fingerprint(rebuilt) == \
        isotp_mutators.frames_fingerprint(frames), \
        f"{case['case_id']}: 重建的 frames 与 case 不一致（非确定性）"

    assert uds_corpus.framing_invariant_ok(mutated, frames), \
        f"{case['case_id']}: canonical framing 判据违反"

    if len(frames) == 1:
        assert uds_corpus.sf_invariant_ok(frames[0]["data"]), \
            f"{case['case_id']}: SF 不变量违反"

assert canonical_n > 500, "canonical 覆盖过低"
assert malformed_n > 0, "未生成任何 mf_* case"
assert all(v > 0 for v in op_seen.values()), f"有 mf_* op 未被生成: {op_seen}"

report["cases_validated"] = len(candidates)
report["ops_generated"] = {k: v for k, v in sorted(op_seen.items())}

# ---- 4b. admission 的全局结论：>128 的请求从未变成 case ----
#
# 这条直接排除"reject 被当成普通 case 送进 ECU、再被分类成 ECU_CRASH /
# STATE_CORRUPTION / HANG / TRANSPORT_ERROR"的路径：超出 envelope 的请求
# 在**生成期**就不存在对应的 case，因此根本没有可分类的对象。
#
# 长度只能由 uds_len 维度改变；mf_* 是帧级变异，payload 字节不变，
# 长度即 seed 自身长度。其余情形用正式入口重建 mutated payload 取长度。
over_envelope = []

for candidate in candidates:
    seed = uds_corpus.seed_by_id(candidate["seed_id"])
    params = candidate["case"]["mutation"].get("params") or {}

    if params.get("op") in um.MF_OPS:
        size = len(seed["uds_request"])
    else:
        mutated, _ = um.apply_mutation(seed, candidate["case"]["mutation"])
        size = len(mutated) if mutated is not None else len(seed["uds_request"])

    if size > MF_LEN:
        over_envelope.append(candidate["case"]["case_id"])

assert not over_envelope, \
    f"campaign 中存在超过 {MF_LEN} 字节的 case: {over_envelope[:5]}"

report["admission_max_payload"] = MF_LEN
report["admission_over_envelope"] = len(over_envelope)

# ---- 6b. M6 的 CF#16 no-op 必须不被生成；M6/M7 目标必须确定 ----
assert uds_corpus.cf_sequence_number(16) == 0, "CF#16 的规范 SN 应为 0"

# corpus 最长 seed 是 78B -> 11 CF，CF#16 只在 envelope 上边界才出现。
# 用**真实 seed 的浅拷贝**造 128B 样本，走正式 spec 生成器验证该边界，
# 不复制任何变异算法、也不复制 seed 字典的形状知识。
probe = dict(uds_corpus.seed_by_id("uds-mf-22-four-dids"))
probe["uds_request"] = bytes([0x22]) + bytes(MF_LEN - 1)      # 128B -> 18 CF

probe_cf = uds_corpus.framing_cf_count(MF_LEN)
assert probe_cf >= 16, f"128B 应产生 >=16 个 CF，实际 {probe_cf}"

probe_specs = um.mutation_specs_for_seed(probe)
again_specs = um.mutation_specs_for_seed(dict(uds_corpus.seed_by_id(
    "uds-mf-22-four-dids")) | {"uds_request": probe["uds_request"]})

m6_targets = sorted(s["params"]["cf"] for s in probe_specs
                    if s["params"].get("op") == um.MF_OP_CF_SN_ZERO)
m7_targets = sorted(s["params"]["cf"] for s in probe_specs
                    if s["params"].get("op") == um.MF_OP_CF_SN_OFF_BY_ONE)

assert 16 not in m6_targets, "M6 不得针对 SN 已为 0 的 CF#16 生成（那是 no-op）"
assert 16 in m7_targets, "M7 必须覆盖 CF#16（SN 0 -> 1，非 no-op）"
assert len(m7_targets) == probe_cf, \
    f"M7 应对每个 CF 各一条：{len(m7_targets)} != {probe_cf}"
assert len(m6_targets) == probe_cf - 1, \
    f"M6 应排除唯一的 SN==0 CF：{len(m6_targets)} != {probe_cf - 1}"

# 目标确定性：spec 序列与 M6/M7 目标两次生成必须完全一致
assert [s["label"] for s in probe_specs] == [s["label"] for s in again_specs], \
    "spec 生成顺序不确定"
assert sorted(s["params"]["cf"] for s in again_specs
              if s["params"].get("op") == um.MF_OP_CF_SN_ZERO) == m6_targets, \
    "M6 目标不确定"
assert sorted(s["params"]["cf"] for s in again_specs
              if s["params"].get("op") == um.MF_OP_CF_SN_OFF_BY_ONE) == m7_targets, \
    "M7 目标不确定"

# frames 层确定性：同一 M7 spec 两次构建 -> 同一指纹
mf_seed = uds_corpus.seed_by_id("uds-mf-34-download")
spec7 = next(s for s in um.mutation_specs_for_seed(mf_seed)
             if s["params"].get("op") == um.MF_OP_CF_SN_OFF_BY_ONE)
f7a, _ = um.build_mutated_frames(mf_seed, spec7)
f7b, _ = um.build_mutated_frames(mf_seed, spec7)

assert f7a is not None and f7b is not None
assert isotp_mutators.frames_fingerprint(f7a) == \
    isotp_mutators.frames_fingerprint(f7b), "M7 frames 构建不确定"

report["m6_cf16_noop_excluded"] = True
report["m6_targets"] = len(m6_targets)
report["m7_targets"] = len(m7_targets)

# ---- 6c. M1..M7 的**冻结传输语义**逐项核对（mutation -> MF encoding）----
#
# 七项 op 的定义（Frozen Contract "Complete mf_* enumeration"）：
#
#   M1  FF 声明长度 := 0              M5  删除最后一个 CF（帧数 -1）
#   M2  FF 声明长度 := L-1            M6  指定 CF 的 SN := 0
#   M3  FF 声明长度 := L+1            M7  指定 CF 的 SN := (SN+1) mod 16
#   M4  FF 声明长度 := 0x0FFF
#
# 共同的 Layer B 约束：**只改 PCI / 帧数，绝不改 payload 字节**。
# 任何一项偏离都说明 mf_* 变异语义被改动 —— 这是冻结项，不得漂移。
op_seed = uds_corpus.seed_by_id("uds-mf-34-download")      # L=8 -> FF + 1 CF
op_canonical = uds_corpus.encode_uds_request(op_seed["uds_request"])
op_len = len(op_seed["uds_request"])
op_canonical_fp = isotp_mutators.frames_fingerprint(op_canonical)


def _mf_frames(op, **params):
    spec = {"dimension": um.DIM_LEN, "params": {"op": op, **params},
            "label": f"op-probe-{op}"}
    built, why = um.build_mutated_frames(op_seed, spec)

    assert built is not None, f"{op}: 无法生成（{why}）"

    return built


def _declared(frames):
    head = bytes(frames[0]["data"])

    return ((head[0] & 0x0F) << 8) | head[1]


def _sn(frames, index):
    return bytes(frames[index]["data"])[0] & 0x0F


op_semantics = {}

# M1..M4：只改 FF 的 12-bit 声明长度；帧数与 payload 逐字节不变
for op, expected in (
    (um.MF_OP_FF_DECLARED_ZERO, 0),
    (um.MF_OP_FF_DECLARED_MINUS_ONE, op_len - 1),
    (um.MF_OP_FF_DECLARED_PLUS_ONE, op_len + 1),
    (um.MF_OP_FF_DECLARED_MAX, um.FF_DECLARED_MAX),
):
    frames = _mf_frames(op)

    assert _declared(frames) == expected, \
        f"{op}: FF 声明长度 {_declared(frames)} != {expected}"
    assert len(frames) == len(op_canonical), f"{op}: 帧数被改动"
    assert frames[1:] == op_canonical[1:], f"{op}: CF 被改动（应只改 FF PCI）"
    assert bytes(frames[0]["data"])[2:] == bytes(op_canonical[0]["data"])[2:], \
        f"{op}: FF 携带的 payload 字节被改动"
    assert isotp_mutators.frames_fingerprint(frames) != op_canonical_fp, \
        f"{op}: 与 canonical 指纹相同（no-op）"

    op_semantics[op] = _declared(frames)

# M5：删除最后一个 CF —— 帧数 -1；FF 与其余 CF 逐字节不变
frames_m5 = _mf_frames(um.MF_OP_CF_DROP_LAST)

assert len(frames_m5) == len(op_canonical) - 1, "M5: 帧数未 -1"
assert frames_m5[0] == op_canonical[0], "M5: FF 被改动"
assert frames_m5[1:] == op_canonical[1:-1], "M5: 其余 CF 被改动"
assert _declared(frames_m5) == op_len, \
    "M5: FF 声明长度被改动（删帧不改声明，这正是 M5 的信号）"
assert isotp_mutators.frames_fingerprint(frames_m5) != op_canonical_fp, "M5: no-op"

op_semantics[um.MF_OP_CF_DROP_LAST] = len(frames_m5)

# M6：指定 CF 的 SN := 0（只动 PCI 低半字节）
frames_m6 = _mf_frames(um.MF_OP_CF_SN_ZERO, cf=1)

assert _sn(frames_m6, 1) == 0, "M6: CF#1 的 SN 未归零"
assert bytes(frames_m6[1]["data"])[1:] == bytes(op_canonical[1]["data"])[1:], \
    "M6: CF payload 被改动"
assert frames_m6[0] == op_canonical[0], "M6: FF 被改动"
assert isotp_mutators.frames_fingerprint(frames_m6) != op_canonical_fp, "M6: no-op"

op_semantics[um.MF_OP_CF_SN_ZERO] = _sn(frames_m6, 1)

# M7：指定 CF 的 SN := (SN+1) mod 16 —— mod-16 自增无不动点，故不存在 no-op
frames_m7 = _mf_frames(um.MF_OP_CF_SN_OFF_BY_ONE, cf=1)
op_canonical_sn = _sn(op_canonical, 1)

assert _sn(frames_m7, 1) == (op_canonical_sn + 1) % 16, \
    f"M7: CF#1 的 SN {_sn(frames_m7, 1)} != {(op_canonical_sn + 1) % 16}"
assert bytes(frames_m7[1]["data"])[1:] == bytes(op_canonical[1]["data"])[1:], \
    "M7: CF payload 被改动"
assert isotp_mutators.frames_fingerprint(frames_m7) != op_canonical_fp, "M7: no-op"

op_semantics[um.MF_OP_CF_SN_OFF_BY_ONE] = _sn(frames_m7, 1)

assert len(op_semantics) == len(um.MF_OPS) == 7, \
    f"M1..M7 未逐项核对：{sorted(op_semantics)}"
assert set(op_semantics) == set(um.MF_OPS), "逐项核对的 op 集合不是冻结的七个"

report["mf_op_semantics"] = {k: v for k, v in sorted(op_semantics.items())}

# ---- 7. campaign：确定性与去重 ----
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

# ---- 7b. no-op：不产生"新 testcase" 的两条性质 ----
#
# no-op mutation 不制造伪 testcase，由两个**独立**机制保证：
#   (i)  生成期：build_mutated_frames 拒绝与 canonical 指纹相同的候选（§2）
#   (ii) 执行期：campaign 按 frames_fingerprint 去重，重复项不执行 ECU
#
# 这里再固化两条性质：
#   (a) canonical identity **不含 result** —— 同一 case 无论执行结果如何，
#       identity 必须逐字相同；否则"重放过的 case"会凭空变成另一个 testcase
#   (b) 被判重的候选必须与**保留**的候选同指纹 —— 去重丢的确实是重复项
identity_probe = candidates[0]["case"]
identity_stamped = dict(identity_probe)
identity_stamped["result"] = {
    "class": cs.ECU_CRASH,
    "process_alive": False,
    "recovery": cs.RECOVERY_FAIL,
}

assert cs.canonical_identity(identity_stamped) == \
    cs.canonical_identity(identity_probe), \
    "canonical identity 被执行结果污染（result 参与了 identity）"

kept_fingerprints = {c["fingerprint"] for c in unique}

assert all(d["fingerprint"] in kept_fingerprints for d in duplicates), \
    "被丢弃的候选与保留候选指纹不一致（去重丢错了对象）"

report["identity_ignores_result"] = True
report["dedup_fingerprint_consistent"] = True

# ---- 8. oracle 未被改动 ----
frozen = ("KERNEL_ANOMALY", "ECU_CRASH", "STATE_CORRUPTION", "HANG",
          "TRANSPORT_ERROR", "UDS_RESPONSE", "DISCARD", "NORMAL")
assert tuple(cs.CLASS_PRIORITY) == frozen, "CLASS_PRIORITY 被改动"
assert cs.SCHEMA_VERSION == 1, "schema_version 被改动"

report["class_priority"] = "unchanged"

# ---- 9. v1 golden：947 case + 两个 digest 与冻结基准逐字一致 ----
v1_ids = [s["seed_id"] for s in uds_corpus.CORPUS if s.get("envelope") == V1_LEN]

assert hashlib.sha256("\n".join(v1_ids).encode("utf-8")).hexdigest() == \
    V1_SEED_IDS_DIGEST, "v1 seed_id 集合发生偏移（R5-b 违反）"

v1_set = set(v1_ids)
v1_cases = [c for c in candidates if c["seed_id"] in v1_set]

assert len(v1_cases) == V1_CASE_COUNT, \
    f"v1 case 数量 {len(v1_cases)} != 冻结值 {V1_CASE_COUNT}"

entries = sorted(
    (c["case"]["case_id"], isotp_mutators.frames_fingerprint(c["case"]["frames"]))
    for c in v1_cases
)
digest = hashlib.sha256(
    "\n".join(f"{cid}|{fp}" for cid, fp in entries).encode("utf-8")).hexdigest()

assert digest == V1_GOLDEN_DIGEST, \
    f"v1 frames/fingerprint 发生偏移（R5-a 违反）: {digest}"

report["v1_golden_cases"] = len(v1_cases)

# ---- replay roundtrip ----
case = candidates[0]["case"]
restored = cs.from_jsonable(cs.to_jsonable(case))
assert cs.canonical_identity(restored) == cs.canonical_identity(case), \
    "replay roundtrip 改变了 identity"

report["identity_roundtrip"] = "stable"

# ---- 输出：JSON（供 ok/fail 行）+ 人读的 OQ3 证据块 ----
report["sf_cases"] = sf_cases
report["mf_cases"] = mf_cases

with open(os.path.join(work_dir, "offline.json"), "w", encoding="utf-8") as fh:
    fh.write(json.dumps(report, sort_keys=True))

lines = [
    "OQ3 MF REPRO SMOKE",
    "",
    f"corpus_seeds = {report['corpus_seeds']}"
    f" (v1={report['v1_seeds']} / MF={report['mf_seeds']})",
    f"generated    = {report['generated']}",
    f"unique       = {report['unique']}",
    f"duplicates   = {report['duplicates']}",
    "",
    f"SF cases     = {sf_cases}",
    f"MF cases     = {mf_cases}",
    "",
    "bounds (accept)  = PASS (L=7 -> SF; L=8/13/14/78/128 -> MF)",
    "bounds (reject)  = PASS"
    " (L=129/200/300 -> admission reject：生成期抛出、0 帧、0 case)",
    "framing checks   = PASS"
    f" (Layer A canonical={canonical_n}; Layer B mf_*={malformed_n})",
    "mutation M1-M7   = PASS"
    f" (7/7 op 逐项核对传输语义; M6 excludes CF#16 no-op;"
    f" M7 targets={len(m7_targets)})",
    "no-op guard      = PASS"
    f" (payload-level no-op rejected: built={built} skipped={skipped};"
    " identity 不含 result; dedup 指纹一致)",
    "admission        = PASS"
    f" (<= {MF_LEN} accepted / > {MF_LEN} rejected;"
    f" over-envelope cases = {report['admission_over_envelope']})",
    "schema/admission = PASS"
    f" ({report['cases_validated']} cases validate_case + fingerprintable;"
    f" frames <= {MAX_FRAMES})",
    "v1 golden        = PASS"
    f" ({len(v1_cases)} cases; V1_GOLDEN_DIGEST + V1_SEED_IDS_DIGEST unchanged)",
]

with open(os.path.join(work_dir, "oq3.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")

print(json.dumps(report, sort_keys=True))
PY
ORC=$?

if [ "$ORC" -eq 0 ]; then
    ok "offline 校验通过：$(cat "$WORK/offline.json")"
    echo
    cat "$WORK/oq3.txt"
else
    fail "offline 校验失败"
    cat "$WORK/offline.out"
fi

# ----------------------------------------------------------------------
# 10. vCAN smoke：3 个 case 真打 ECU（SF regression + MF smoke + MF basic）
#
# 确定性选择（--seed 12345 --max-cases 3）恰好覆盖三种形态：
#   1× SF、1× canonical MF（FF+CF）、1× malformed MF（mf_cf_drop_last）
#
# 其中 canonical MF 那条来自 8 字节 seed（uds-mf-34-download），即 MF 的
# **下边界** —— 它同时充当 §"MF basic" 的证据：UDS request → FF → CF →
# ECU 正常处理 → oracle / recovery 正常。
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

# case 落盘 + result 完整性 + kernel oracle 三态诚实 + MF basic
"$PYTHON_BIN" - "$OUT" "$FUZZ_DIR" <<'PY' > "$WORK/case_check.out" 2>&1
import json
import os
import sys

out_dir, fuzz_dir = sys.argv[1], sys.argv[2]
tools_dir = os.path.dirname(fuzz_dir)

for path in (fuzz_dir, tools_dir):
    if path not in sys.path:
        sys.path.insert(0, path)

import case_schema as cs
import isotp_corpus
import uds_corpus
import uds_mutators as um

required = ("class", "process_alive", "recovery", "kernel_oracle", "hung")

#: 四个"执行期失败"类别 —— admission reject **不属于**它们
#: （reject 发生在生成期，不产生 case，因此没有可分类的对象，见 §3b/§4b）
FAILURE_CLASSES = (cs.KERNEL_ANOMALY, cs.ECU_CRASH, cs.STATE_CORRUPTION,
                   cs.HANG, cs.TRANSPORT_ERROR)

MAX_FRAMES = uds_corpus.MAX_FRAMES_PER_CASE
MF_MIN_LEN = uds_corpus.MAX_UDS_REQUEST_LEN + 1        # 8：MF 下边界

names = [n for n in sorted(os.listdir(out_dir))
         if n.endswith(".json") and n != "uds-campaign-summary.json"]

assert len(names) == 3, f"case JSON 数量 {len(names)} != 3"

sf = mf = malformed = 0
canonical_mf = []

for name in names:
    with open(os.path.join(out_dir, name), encoding="utf-8") as handle:
        case = cs.from_jsonable(json.load(handle))

    case_id = case["case_id"]
    res = case.get("result") or {}

    # 落盘 case 读回来仍必须过 schema 准入（含 result.class 合法性）
    cs.validate_case(case, require_result=True)

    for field in required:
        assert field in res, f"{name}: result.{field} 缺失"

    oracle = res["kernel_oracle"]
    assert oracle in cs.KERNEL_ORACLE_STATUSES, \
        f"{name}: kernel_oracle 非法 {oracle!r}"

    if oracle != cs.KERNEL_ORACLE_AVAILABLE:
        # fail-closed：oracle 不可用 ≠ 0 kernel event
        assert res.get("kernel_events") is None, \
            f"{name}: oracle={oracle} 但 kernel_events 不是 null"

    # ---- Layer C：帧数上限 ----
    frames = case["frames"]

    assert 1 <= len(frames) <= MAX_FRAMES, \
        f"{name}: 帧数 {len(frames)} 越界（1..{MAX_FRAMES}）"

    params = (case.get("mutation") or {}).get("params") or {}
    op = params.get("op")

    # ---- Layer B：malformed mf_* case（故意破坏 transport framing）----
    if str(op).startswith("mf_"):
        malformed += 1
        assert op in um.MF_OPS, f"{name}: 未知 mf_* op {op!r}"
        continue

    # ---- Layer A：canonical ----
    if len(frames) == 1:
        sf += 1
        continue

    mf += 1

    # 用正式入口重建该 case 的 payload 与规范分帧，脚本不自行推导 framing
    seed = uds_corpus.seed_by_id(case["seed"])
    mutated, why = um.apply_mutation(seed, case["mutation"])

    assert mutated is not None, f"{name}: 无法重建 payload（{why}）"
    assert uds_corpus.framing_invariant_ok(mutated, frames), \
        f"{name}: 执行体的 framing 不是该 payload 的规范 FF+CF"
    assert (bytes(frames[0]["data"])[0] & 0xF0) == isotp_corpus.PCI_FF, \
        f"{name}: 首帧不是 FF"

    canonical_mf.append({
        "case_id": case_id,
        "payload_len": len(mutated),
        "frames": len(frames),
        "class": res["class"],
        "recovery": res["recovery"],
        "process_alive": res["process_alive"],
        "errno": res.get("errno"),
        "hung": res.get("hung"),
        "oracle": oracle,
    })

assert sf >= 1, "vcan campaign 未覆盖 SF（v1 regression 丢失）"
assert mf >= 1, "vcan campaign 未覆盖 canonical MF（OQ3 smoke 丢失）"
assert malformed >= 1, "vcan campaign 未覆盖 malformed mf_* case"
assert sf + mf + malformed == len(names), "case 分类既不重叠也不遗漏"

# ---- MF basic：8 字节（MF 下边界）的 canonical MF 必须真的跑通 ----
#
# 只断言"跑完没报错"不够：这里要求 MF 请求确实经 FF + CF 送达 ECU，
# 且 ECU **正常处理** —— 进程存活、recovery probe 通过、无 errno、未挂死、
# 分类不落在任何执行期失败类别上。
on_boundary = [c for c in canonical_mf if c["payload_len"] == MF_MIN_LEN]

assert on_boundary, (
    f"未覆盖 {MF_MIN_LEN} 字节的 MF 下边界；本轮 canonical MF: "
    f"{[(c['case_id'], c['payload_len']) for c in canonical_mf]}")

for entry in on_boundary:
    tag = entry["case_id"]

    assert entry["frames"] >= 2, f"{tag}: MF basic 没有 CF"
    assert entry["process_alive"] is True, f"{tag}: ECU 进程未存活"
    assert entry["recovery"] == cs.RECOVERY_PASS, \
        f"{tag}: recovery={entry['recovery']}（MF basic 必须能恢复）"
    assert entry["errno"] is None, f"{tag}: errno={entry['errno']}"
    assert not entry["hung"], f"{tag}: case 被判定为 hang"
    assert entry["class"] not in FAILURE_CLASSES, \
        f"{tag}: MF basic 被分类为执行期失败 {entry['class']}"

print(f"case_ok={len(names)} sf={sf} mf={mf} malformed={malformed} "
      f"max_frames={MAX_FRAMES} "
      f"mf_basic={[(c['case_id'], c['payload_len'], c['frames'], c['class']) for c in on_boundary]}")
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
echo " UDS fuzz smoke summary (v1 + OQ3 MF)"
echo "========================================"
echo "PASS: $PASS"
echo "FAIL: $FAIL"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi

exit 0
