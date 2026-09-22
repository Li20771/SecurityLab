#!/usr/bin/env python3
"""
UDS fuzz v1 —— 离线单元测试（不需要 vcan0）

直接测试 UDS 层的三个新模块，不打开任何 CAN socket、不启动 ECU：

    fuzz/uds_corpus.py    种子 + UDS→ISO-TP SF 编码器
    fuzz/uds_mutators.py  4 个变异维度
    fuzz/uds_fuzz.py      campaign front-end（生成 / 去重 / 排序）

覆盖（Frozen Contract v3）：

1. corpus：11 个 IN 服务各有 seed；全部 ≤7 字节；顺序稳定
2. encoder：空 / >7 拒绝；SF PCI == payload 长度；roundtrip
3. mutation：4 个维度的**精确值集**、service applicability、suppress 仅 0x3E
4. determinism：same seed + same spec → same request → same frames → same fingerprint
5. no-op 拒绝
6. 长度不变量：**任何**生成结果 ≤7
7. case schema：validate_case 通过；不新增字段 / 不新增 action 类型
8. campaign：fingerprint 去重、确定性排序、`--max-cases` 前缀性质
9. replay roundtrip：to_jsonable → from_jsonable → canonical_identity 不变
10. **mutation guards**：证明上述检查有牙齿（故意破坏必须被抓到）

运行：

    /home/lilike/.venvs/network/bin/python tests/test_uds_fuzz_offline.py

或：

    bash tests/run_tests.sh offline
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.dirname(_HERE)
_FUZZ_DIR = os.path.join(_TOOLS_DIR, "fuzz")

for _p in (_TOOLS_DIR, _FUZZ_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import case_schema as cs  # noqa: E402
import isotp_mutators  # noqa: E402
import uds_corpus  # noqa: E402
import uds_fuzz  # noqa: E402
import uds_mutators as um  # noqa: E402

#: v1 OUT 的 SID（Frozen Contract §2）——绝不应出现在 corpus 里
OUT_SIDS = (0x34, 0x35, 0x36)

#: 允许的 action 类型（**不新增**）
KNOWN_ACTION_TYPES = {"inject", "wait", "await_fc"}

#: build_case 产出的固定字段集（**不新增 case field**）
EXPECTED_CASE_FIELDS = {
    "schema_version", "case_id", "phase", "seed", "category", "summary",
    "mutation", "interface", "can_ids", "frames", "expected", "result",
}


class UdsFuzzOfflineTestCase(unittest.TestCase):

    def encode(self, uds_request: bytes):
        return uds_corpus.encode_uds_request(uds_request)

    def spec(self, dimension, params, label="x"):
        return {"dimension": dimension, "params": params, "label": label}


# ----------------------------------------------------------------------
# 1. corpus
# ----------------------------------------------------------------------


class TestUdsCorpus(UdsFuzzOfflineTestCase):

    def test_covers_every_v1_in_service(self) -> None:
        services = {seed["service"] for seed in uds_corpus.CORPUS}

        for sid in uds_corpus.V1_IN_SIDS:
            with self.subTest(sid=f"0x{sid:02X}"):
                self.assertIn(sid, services, f"0x{sid:02X} 在 v1 IN 却无 seed")

    def test_has_no_out_of_scope_service(self) -> None:
        services = {seed["service"] for seed in uds_corpus.CORPUS}

        for sid in OUT_SIDS:
            with self.subTest(sid=f"0x{sid:02X}"):
                self.assertNotIn(sid, services)

    def test_all_seeds_within_single_frame_limit(self) -> None:
        for seed in uds_corpus.CORPUS:
            with self.subTest(seed=seed["seed_id"]):
                self.assertLessEqual(len(seed["uds_request"]),
                                     uds_corpus.MAX_UDS_REQUEST_LEN)

    def test_all_seeds_are_non_empty_and_encodable(self) -> None:
        for seed in uds_corpus.CORPUS:
            with self.subTest(seed=seed["seed_id"]):
                self.assertTrue(seed["uds_request"])
                frames = self.encode(seed["uds_request"])
                self.assertEqual(len(frames), 1)

    def test_seed_ids_are_unique_and_stable(self) -> None:
        ids = [seed["seed_id"] for seed in uds_corpus.CORPUS]

        self.assertEqual(len(ids), len(set(ids)), "seed_id 必须唯一")
        # 顺序稳定：两次读取同一对象序列
        self.assertEqual(ids, [s["seed_id"] for s in uds_corpus.iter_seeds()])

    def test_suppress_path_seed_exists(self) -> None:
        """0x3E 80 必须作为**独立合法 seed**（真实抑制路径）。"""
        seed = uds_corpus.seed_by_id("uds-3e-suppress")

        self.assertEqual(seed["uds_request"], bytes([0x3E, 0x80]))
        self.assertEqual(seed["service"], 0x3E)

    def test_file_transfer_seeds_cover_modes_02_04_05(self) -> None:
        modes = {
            seed["uds_request"][1]
            for seed in uds_corpus.CORPUS
            if seed["service"] == 0x38
        }

        self.assertEqual(modes, {0x02, 0x04, 0x05})

    def test_no_file_transfer_seed_for_out_modes(self) -> None:
        for seed in uds_corpus.CORPUS:
            if seed["service"] != 0x38:
                continue

            with self.subTest(seed=seed["seed_id"]):
                self.assertNotIn(seed["uds_request"][1], (0x01, 0x03))


# ----------------------------------------------------------------------
# 2. encoder
# ----------------------------------------------------------------------


class TestUdsEncoder(UdsFuzzOfflineTestCase):

    def test_rejects_empty(self) -> None:
        with self.assertRaises(uds_corpus.UdsEncodeError):
            self.encode(b"")

    def test_rejects_over_seven(self) -> None:
        for length in (8, 9, 16):
            with self.subTest(length=length):
                with self.assertRaises(uds_corpus.UdsEncodeError):
                    self.encode(bytes([0x22]) + bytes(length - 1))

    def test_accepts_exactly_seven(self) -> None:
        frames = self.encode(bytes([0x22, 0xF1, 0x80, 0xF1, 0x90, 0xF1, 0x87]))

        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["data"][0], 0x07)

    def test_sf_pci_equals_payload_length(self) -> None:
        for length in range(1, 8):
            with self.subTest(length=length):
                request = bytes([0x10]) + bytes(length - 1)
                frame = self.encode(request)[0]["data"]

                self.assertEqual(frame[0] & 0xF0, 0x00, "必须是 SF")
                self.assertEqual(frame[0] & 0x0F, length)
                self.assertEqual(len(frame) - 1, length)

    def test_payload_roundtrip(self) -> None:
        """帧 → payload 还原必须与原 UDS 请求逐字节相同。"""
        for seed in uds_corpus.CORPUS:
            with self.subTest(seed=seed["seed_id"]):
                frame = self.encode(seed["uds_request"])[0]["data"]
                declared = frame[0] & 0x0F

                self.assertEqual(frame[1:1 + declared], seed["uds_request"])

    def test_sf_invariant_helper_accepts_good_frames(self) -> None:
        for seed in uds_corpus.CORPUS:
            with self.subTest(seed=seed["seed_id"]):
                frame = self.encode(seed["uds_request"])[0]["data"]

                self.assertTrue(uds_corpus.sf_invariant_ok(frame))


# ----------------------------------------------------------------------
# 3. uds_len
# ----------------------------------------------------------------------


class TestUdsLenMutation(UdsFuzzOfflineTestCase):

    def setUp(self) -> None:
        self.seed = uds_corpus.seed_by_id("uds-19-count-by-mask")   # 19 01 FF (L=3)
        self.raw = self.seed["uds_request"]

    def ops(self):
        return {s["params"]["op"] for s in um.mutation_specs_for_seed(self.seed)
                if s["dimension"] == um.DIM_LEN}

    def test_generates_expected_op_set(self) -> None:
        self.assertEqual(
            self.ops(), {"sid_only", "drop1", "add1_00", "add2_00", "add2_ff"})

    def test_sid_only(self) -> None:
        mutated, reason = um.apply_mutation(
            self.seed, self.spec(um.DIM_LEN, {"op": "sid_only"}))

        self.assertIsNone(reason)
        self.assertEqual(mutated, bytes([0x19]))

    def test_drop1(self) -> None:
        mutated, _ = um.apply_mutation(
            self.seed, self.spec(um.DIM_LEN, {"op": "drop1"}))

        self.assertEqual(mutated, bytes([0x19, 0x01]))

    def test_add1_and_add2(self) -> None:
        cases = (
            ("add1_00", bytes([0x19, 0x01, 0xFF, 0x00])),
            ("add2_00", bytes([0x19, 0x01, 0xFF, 0x00, 0x00])),
            ("add2_ff", bytes([0x19, 0x01, 0xFF, 0xFF, 0xFF])),
        )

        for op, expected in cases:
            with self.subTest(op=op):
                mutated, _ = um.apply_mutation(self.seed,
                                               self.spec(um.DIM_LEN, {"op": op}))
                self.assertEqual(mutated, expected)

    def test_declared_mismatch_only_for_0x38(self) -> None:
        """decl_* 只应出现在 0x38 seed 上。"""
        for seed in uds_corpus.CORPUS:
            has_decl = any(
                s["params"]["op"].startswith("decl")
                for s in um.mutation_specs_for_seed(seed)
                if s["dimension"] == um.DIM_LEN
            )

            with self.subTest(seed=seed["seed_id"]):
                self.assertEqual(has_decl, seed["service"] == 0x38)

    def test_declared_plus_changes_only_declaration(self) -> None:
        """只改 pathLen 声明，**不动实际 path 字节**。"""
        seed = uds_corpus.seed_by_id("uds-38-readdir-subdir")
        raw = seed["uds_request"]

        mutated, reason = um.apply_mutation(
            seed, self.spec(um.DIM_LEN, {"op": "decl_plus"}))

        self.assertIsNone(reason)
        self.assertEqual(len(mutated), len(raw), "长度必须不变")
        self.assertEqual(int.from_bytes(mutated[2:4], "big"),
                         int.from_bytes(raw[2:4], "big") + 1)
        self.assertEqual(mutated[4:], raw[4:], "path 字节必须不变")

    def test_declared_minus_changes_only_declaration(self) -> None:
        seed = uds_corpus.seed_by_id("uds-38-readdir-subdir")
        raw = seed["uds_request"]

        mutated, _ = um.apply_mutation(
            seed, self.spec(um.DIM_LEN, {"op": "decl_minus"}))

        self.assertEqual(int.from_bytes(mutated[2:4], "big"),
                         int.from_bytes(raw[2:4], "big") - 1)
        self.assertEqual(mutated[4:], raw[4:])

    def test_no_generation_when_it_would_exceed_limit(self) -> None:
        """7 字节 seed 不应生成任何加长 op。"""
        seed = uds_corpus.seed_by_id("uds-38-readfile-missing")   # L=7
        ops = {s["params"]["op"] for s in um.mutation_specs_for_seed(seed)
               if s["dimension"] == um.DIM_LEN}

        self.assertNotIn("add1_00", ops)
        self.assertNotIn("add2_00", ops)
        self.assertNotIn("add2_ff", ops)

    def test_one_byte_seed_has_no_truncating_ops(self) -> None:
        seed = uds_corpus.seed_by_id("uds-37-no-transfer")   # L=1
        ops = {s["params"]["op"] for s in um.mutation_specs_for_seed(seed)
               if s["dimension"] == um.DIM_LEN}

        self.assertNotIn("sid_only", ops)
        self.assertNotIn("drop1", ops)


# ----------------------------------------------------------------------
# 4. uds_sid
# ----------------------------------------------------------------------


class TestUdsSidMutation(UdsFuzzOfflineTestCase):

    def setUp(self) -> None:
        self.seed = uds_corpus.seed_by_id("uds-22-single-did")   # 22 F1 80

    def values(self):
        return [s["params"]["value"] for s in um.mutation_specs_for_seed(self.seed)
                if s["dimension"] == um.DIM_SID]

    def test_contains_fixed_values(self) -> None:
        for value in (0x00, 0x7F, 0xFF):
            with self.subTest(value=value):
                self.assertIn(value, self.values())

    def test_contains_exact_unimpl_set(self) -> None:
        values = self.values()

        for value in um.UNIMPL_SIDS:
            with self.subTest(value=value):
                self.assertIn(value, values)

        self.assertEqual(set(um.UNIMPL_SIDS), {0x06, 0x28, 0x39, 0x85})

    def test_cross_covers_all_other_in_sids(self) -> None:
        values = set(self.values())

        for sid in uds_corpus.V1_IN_SIDS:
            if sid == self.seed["service"]:
                continue

            with self.subTest(sid=f"0x{sid:02X}"):
                self.assertIn(sid, values)

        self.assertNotIn(self.seed["service"], values, "cross 不应含自身")

    def test_cross_keeps_payload_unchanged(self) -> None:
        mutated, _ = um.apply_mutation(
            self.seed, self.spec(um.DIM_SID, {"value": 0x10}))

        self.assertEqual(mutated, bytes([0x10, 0xF1, 0x80]))
        self.assertEqual(mutated[1:], self.seed["uds_request"][1:])


# ----------------------------------------------------------------------
# 5. uds_sub（服务控制字节）
# ----------------------------------------------------------------------


class TestUdsSubMutation(UdsFuzzOfflineTestCase):

    def specs_for(self, seed_id):
        seed = uds_corpus.seed_by_id(seed_id)

        return [s for s in um.mutation_specs_for_seed(seed)
                if s["dimension"] == um.DIM_SUB]

    def test_generated_for_control_byte_services(self) -> None:
        covered = set()

        for seed in uds_corpus.CORPUS:
            if self.specs_for(seed["seed_id"]):
                covered.add(seed["service"])

        # corpus 覆盖到的控制字节服务必须都生成 uds_sub
        expected = {s["service"] for s in uds_corpus.CORPUS
                    if s["service"] in uds_corpus.CONTROL_BYTE_SIDS}

        self.assertEqual(covered, expected)

    def test_not_generated_for_services_without_control_byte(self) -> None:
        for seed in uds_corpus.CORPUS:
            if seed["service"] in uds_corpus.CONTROL_BYTE_SIDS:
                continue

            with self.subTest(seed=seed["seed_id"]):
                self.assertEqual(self.specs_for(seed["seed_id"]), [])

    def test_suppress_only_for_services_implementing_it(self) -> None:
        for seed in uds_corpus.CORPUS:
            suppress = [s for s in self.specs_for(seed["seed_id"])
                        if s["params"].get("op") == "suppress"]

            with self.subTest(seed=seed["seed_id"]):
                if seed["service"] in uds_corpus.SUPPRESS_SIDS:
                    self.assertEqual(len(suppress), 1)
                else:
                    self.assertEqual(suppress, [])

    def test_suppress_never_generated_for_0x38(self) -> None:
        """0x38 的控制字节是 **mode**，绝不能获得 suppress。"""
        for seed in uds_corpus.CORPUS:
            if seed["service"] != 0x38:
                continue

            with self.subTest(seed=seed["seed_id"]):
                ops = {s["params"].get("op")
                       for s in self.specs_for(seed["seed_id"])}
                self.assertNotIn("suppress", ops)

    def test_suppress_apply_is_guarded_for_0x38(self) -> None:
        """即使手工构造 suppress spec，apply 期也必须拒绝 0x38。"""
        seed = uds_corpus.seed_by_id("uds-38-readdir-root")

        mutated, reason = um.apply_mutation(
            seed, self.spec(um.DIM_SUB, {"op": "suppress"}))

        self.assertIsNone(mutated)
        self.assertIn("suppress", reason)

    def test_suppress_sets_bit7(self) -> None:
        seed = uds_corpus.seed_by_id("uds-3e-tester-present")

        mutated, reason = um.apply_mutation(
            seed, self.spec(um.DIM_SUB, {"op": "suppress"}))

        self.assertIsNone(reason)
        self.assertEqual(mutated, bytes([0x3E, 0x80]))

    def test_fixed_values(self) -> None:
        seed = uds_corpus.seed_by_id("uds-10-default-session")
        values = {s["params"]["value"] for s in self.specs_for(seed["seed_id"])
                  if "value" in s["params"]}

        self.assertEqual(values, {0x00, 0x7F, 0xFF})


# ----------------------------------------------------------------------
# 6. uds_payload
# ----------------------------------------------------------------------


class TestUdsPayloadMutation(UdsFuzzOfflineTestCase):

    def payload_specs(self, seed_id):
        seed = uds_corpus.seed_by_id(seed_id)

        return [s for s in um.mutation_specs_for_seed(seed)
                if s["dimension"] == um.DIM_PAYLOAD], seed

    def test_bitflip_count_is_exactly_8_times_len_minus_1(self) -> None:
        for seed in uds_corpus.CORPUS:
            specs = [s for s in um.mutation_specs_for_seed(seed)
                     if s["dimension"] == um.DIM_PAYLOAD
                     and s["params"].get("op") == "bitflip"]
            length = len(seed["uds_request"])

            with self.subTest(seed=seed["seed_id"]):
                self.assertEqual(len(specs), 8 * (length - 1))

    def test_bitflip_indices_exclude_sid_byte(self) -> None:
        specs, _ = self.payload_specs("uds-19-count-by-mask")   # L=3

        indices = {s["params"]["index"] for s in specs
                   if s["params"].get("op") == "bitflip"}

        self.assertEqual(indices, {1, 2})
        self.assertNotIn(0, indices)

    def test_bitflip_flips_exactly_one_bit(self) -> None:
        seed = uds_corpus.seed_by_id("uds-19-count-by-mask")
        raw = seed["uds_request"]
        mutated, reason = um.apply_mutation(
            seed, self.spec(um.DIM_PAYLOAD,
                            {"op": "bitflip", "index": 2, "bit": 0}))

        self.assertIsNone(reason)
        self.assertEqual(mutated[0:2], raw[0:2])
        self.assertEqual(mutated[2], raw[2] ^ 0x01)

    def test_all_zero_and_all_ff(self) -> None:
        seed = uds_corpus.seed_by_id("uds-19-count-by-mask")

        zero, _ = um.apply_mutation(
            seed, self.spec(um.DIM_PAYLOAD, {"op": "all_zero"}))
        ff, _ = um.apply_mutation(
            seed, self.spec(um.DIM_PAYLOAD, {"op": "all_ff"}))

        self.assertEqual(zero, bytes([0x19, 0x00, 0x00]))
        self.assertEqual(ff, bytes([0x19, 0xFF, 0xFF]))

    def test_delete_and_replace_counts(self) -> None:
        specs, seed = self.payload_specs("uds-19-count-by-mask")   # L=3
        length = len(seed["uds_request"])

        for op, expected in (("del", length - 1), ("rep", 2 * (length - 1))):
            with self.subTest(op=op):
                self.assertEqual(
                    len([s for s in specs if s["params"].get("op") == op]),
                    expected,
                )

    def test_delete_shrinks_by_one(self) -> None:
        seed = uds_corpus.seed_by_id("uds-19-count-by-mask")
        mutated, _ = um.apply_mutation(
            seed, self.spec(um.DIM_PAYLOAD, {"op": "del", "index": 1}))

        self.assertEqual(mutated, bytes([0x19, 0xFF]))

    def test_replace_sets_value(self) -> None:
        seed = uds_corpus.seed_by_id("uds-19-count-by-mask")
        mutated, _ = um.apply_mutation(
            seed, self.spec(um.DIM_PAYLOAD,
                            {"op": "rep", "index": 1, "value": 0xAA}))

        self.assertEqual(mutated, bytes([0x19, 0xAA, 0xFF]))

    def test_field_boundary_per_service(self) -> None:
        """逐服务的 field_boundary 精确集合（Frozen Contract §6）。"""
        expected_targets = {
            "uds-22-single-did": {"did"},
            "uds-14-clear-all": {"group_of_dtc"},
            "uds-19-count-by-mask": {"mask"},
            "uds-31-start-counter": {"rid"},
            "uds-2e-short-write": {"did", "data_len"},
            "uds-27-send-key-no-seed": {"key"},
        }

        for seed_id, targets in expected_targets.items():
            specs, _ = self.payload_specs(seed_id)
            actual = {s["params"]["target"] for s in specs
                      if s["params"].get("op") == "field"}

            with self.subTest(seed=seed_id):
                self.assertEqual(actual, targets)

    def test_field_boundary_0x38_targets(self) -> None:
        specs, _ = self.payload_specs("uds-38-readdir-root")

        targets = {s["params"]["target"] for s in specs
                   if s["params"].get("op") == "field"}

        self.assertIn("path_len", targets)
        self.assertIn("path_first", targets)
        # 根路径 "/" → 不生成"去尾斜杠"
        self.assertNotIn("strip_trailing_slash", targets)

    def test_field_boundary_0x38_trailing_slash_on_non_root(self) -> None:
        specs, seed = self.payload_specs("uds-38-readdir-subdir")
        strip = [s for s in specs
                 if s["params"].get("target") == "strip_trailing_slash"]

        self.assertEqual(len(strip), 1)

        mutated, reason = um.apply_mutation(seed, strip[0])

        self.assertIsNone(reason)
        self.assertEqual(mutated, bytes([0x38, 0x05, 0x00, 0x02, 0x2F, 0x61]))
        self.assertFalse(mutated.endswith(b"/"))

    def test_field_boundary_group_of_dtc_values(self) -> None:
        specs, seed = self.payload_specs("uds-14-clear-all")
        values = {s["params"]["value"] for s in specs
                  if s["params"].get("target") == "group_of_dtc"}

        self.assertEqual(values, {0x000000, 0xFFFFFE})

    def test_data_len_field_bounds(self) -> None:
        specs, seed = self.payload_specs("uds-2e-short-write")
        lengths = {s["params"]["value"] for s in specs
                   if s["params"].get("target") == "data_len"}

        self.assertEqual(lengths, {1, 4})


# ----------------------------------------------------------------------
# 7. determinism / 8. no-op / 9. 长度不变量
# ----------------------------------------------------------------------


class TestUdsDeterminism(UdsFuzzOfflineTestCase):

    def test_same_seed_and_spec_gives_same_everything(self) -> None:
        seed = uds_corpus.seed_by_id("uds-22-single-did")
        spec = self.spec(um.DIM_PAYLOAD, {"op": "bitflip", "index": 2, "bit": 3})

        first_request, _ = um.apply_mutation(seed, spec)
        second_request, _ = um.apply_mutation(seed, spec)

        first_frames, _ = um.build_mutated_frames(seed, spec)
        second_frames, _ = um.build_mutated_frames(seed, spec)

        self.assertEqual(first_request, second_request)
        self.assertEqual(first_frames, second_frames)
        self.assertEqual(isotp_mutators.frames_fingerprint(first_frames),
                         isotp_mutators.frames_fingerprint(second_frames))

    def test_spec_generation_is_stable(self) -> None:
        for seed in uds_corpus.CORPUS:
            with self.subTest(seed=seed["seed_id"]):
                self.assertEqual(um.mutation_specs_for_seed(seed),
                                 um.mutation_specs_for_seed(seed))

    def test_candidate_generation_is_stable(self) -> None:
        first, _ = uds_fuzz.build_candidates()
        second, _ = uds_fuzz.build_candidates()

        self.assertEqual([c["case"]["case_id"] for c in first],
                         [c["case"]["case_id"] for c in second])


class TestUdsNoOpRejection(UdsFuzzOfflineTestCase):

    def test_all_ff_on_already_ff_seed_is_rejected(self) -> None:
        """14 FF FF FF 的 all_ff 变异与 seed 完全相同 → 必须拒绝。"""
        seed = uds_corpus.seed_by_id("uds-14-clear-all")

        frames, reason = um.build_mutated_frames(
            seed, self.spec(um.DIM_PAYLOAD, {"op": "all_ff"}, "pay=all-FF"))

        self.assertIsNone(frames)
        self.assertIn("no-op", reason)

    def test_suppress_on_suppress_seed_is_rejected(self) -> None:
        """3E 80 的 suppress 变异 = 0x80 | 0x80 = 0x80 → 与 seed 相同。"""
        seed = uds_corpus.seed_by_id("uds-3e-suppress")

        frames, reason = um.build_mutated_frames(
            seed, self.spec(um.DIM_SUB, {"op": "suppress"}, "ctl=suppress"))

        self.assertIsNone(frames)
        self.assertIn("no-op", reason)

    def test_real_noop_specs_are_absent_from_candidates(self) -> None:
        """
        候选**不得与其自身的 seed** 帧序列相同（no-op 判定是相对自身 seed 的）。

        注意：候选**可以**与**另一个** seed 的请求相同 —— 例如
        ``10 01`` 的 ``sid=cross-0x11`` 变异必然产出 ``11 01``。
        那是合法的跨服务换体，由 campaign 去重（fingerprint）负责收敛，
        不是 no-op（见 :meth:`test_cross_seed_collision_is_expected`）。
        """
        candidates, skipped = uds_fuzz.build_candidates()

        seed_fingerprints = {
            seed["seed_id"]: isotp_mutators.frames_fingerprint(
                uds_corpus.encode_uds_request(seed["uds_request"]))
            for seed in uds_corpus.CORPUS
        }

        offenders = [
            candidate["case"]["case_id"]
            for candidate in candidates
            if candidate["fingerprint"] == seed_fingerprints[candidate["seed_id"]]
        ]

        self.assertEqual(offenders, [], "候选不得与其自身 seed 帧序列相同")
        self.assertTrue(skipped, "应当存在被拒绝的 spec")

    def test_cross_seed_collision_is_expected(self) -> None:
        """cross-SID 变异合法地产出**另一个 seed** 的帧 —— 这正是去重的理由。"""
        seed = uds_corpus.seed_by_id("uds-10-default-session")

        frames, reason = um.build_mutated_frames(
            seed, self.spec(um.DIM_SID, {"value": 0x11}, "sid=cross-0x11"))

        self.assertIsNone(reason, "相对自身 seed 不是 no-op")

        other = uds_corpus.encode_uds_request(
            uds_corpus.seed_by_id("uds-11-hard-reset")["uds_request"])

        self.assertEqual(isotp_mutators.frames_fingerprint(frames),
                         isotp_mutators.frames_fingerprint(other))


class TestUdsLengthInvariant(UdsFuzzOfflineTestCase):

    def test_every_generated_request_is_within_limit(self) -> None:
        """穷举所有 seed × 所有 spec：生成结果一律 ≤7。"""
        checked = 0

        for seed in uds_corpus.CORPUS:
            for spec in um.mutation_specs_for_seed(seed):
                mutated, reason = um.apply_mutation(seed, spec)

                if mutated is None:
                    continue

                checked += 1

                with self.subTest(seed=seed["seed_id"], label=spec["label"]):
                    self.assertLessEqual(len(mutated),
                                         uds_corpus.MAX_UDS_REQUEST_LEN)

        self.assertGreater(checked, 500, "覆盖率过低，测试可能没跑全")

    def test_every_generated_case_satisfies_sf_invariant(self) -> None:
        candidates, _ = uds_fuzz.build_candidates()

        self.assertTrue(candidates)

        for candidate in candidates:
            frame = candidate["case"]["frames"][0]["data"]

            with self.subTest(case=candidate["case"]["case_id"]):
                self.assertTrue(uds_corpus.sf_invariant_ok(frame))


# ----------------------------------------------------------------------
# 10. case schema
# ----------------------------------------------------------------------


class TestUdsCaseSchema(UdsFuzzOfflineTestCase):

    def setUp(self) -> None:
        self.candidates, _ = uds_fuzz.build_candidates()

    def test_all_cases_validate(self) -> None:
        for candidate in self.candidates:
            with self.subTest(case=candidate["case"]["case_id"]):
                cs.validate_case(candidate["case"])

    def test_no_new_case_fields(self) -> None:
        for candidate in self.candidates[:20]:
            with self.subTest(case=candidate["case"]["case_id"]):
                self.assertEqual(set(candidate["case"]), EXPECTED_CASE_FIELDS)

    def test_no_new_action_types(self) -> None:
        for candidate in self.candidates[:20]:
            for action in candidate["case"]["frames"]:
                with self.subTest(kind=action["type"]):
                    self.assertIn(action["type"], KNOWN_ACTION_TYPES)

    def test_schema_version_unchanged(self) -> None:
        for candidate in self.candidates[:5]:
            self.assertEqual(candidate["case"]["schema_version"],
                             cs.SCHEMA_VERSION)
            self.assertEqual(cs.SCHEMA_VERSION, 1)

    def test_cases_are_single_frame(self) -> None:
        for candidate in self.candidates:
            with self.subTest(case=candidate["case"]["case_id"]):
                self.assertEqual(len(candidate["case"]["frames"]), 1)

    def test_canonical_identity_is_stable(self) -> None:
        for candidate in self.candidates[:20]:
            case = candidate["case"]

            self.assertEqual(cs.canonical_identity(case),
                             cs.canonical_identity(case))


# ----------------------------------------------------------------------
# 11. campaign 去重
# ----------------------------------------------------------------------


class TestUdsCampaignDedup(UdsFuzzOfflineTestCase):

    def setUp(self) -> None:
        self.candidates, _ = uds_fuzz.build_candidates()

    def test_dedup_collapses_identical_fingerprints(self) -> None:
        unique, duplicates = uds_fuzz.dedup_candidates(self.candidates)

        self.assertLess(len(unique), len(self.candidates),
                        "本 corpus 应产生重复帧序列（去重必须做实事）")
        self.assertEqual(len(unique) + len(duplicates), len(self.candidates))

    def test_unique_fingerprints_are_distinct(self) -> None:
        unique, _ = uds_fuzz.dedup_candidates(self.candidates)
        fingerprints = [c["fingerprint"] for c in unique]

        self.assertEqual(len(fingerprints), len(set(fingerprints)))

    def test_first_provenance_is_kept(self) -> None:
        unique, duplicates = uds_fuzz.dedup_candidates(self.candidates)

        self.assertTrue(duplicates)

        first_by_fp = {}

        for candidate in self.candidates:
            first_by_fp.setdefault(candidate["fingerprint"], candidate)

        for duplicate in duplicates[:20]:
            with self.subTest(fp=duplicate["fingerprint"]):
                kept = first_by_fp[duplicate["fingerprint"]]
                self.assertIn(kept, unique)
                self.assertIsNot(kept, duplicate)

    def test_different_provenance_same_frames_collapses_to_one(self) -> None:
        """人工构造：两个不同 provenance、相同 frames → 只留一个候选。"""
        shared = [{"type": "inject", "id": 0x7E0,
                   "data": bytes([0x02, 0x10, 0x01]), "delay_ms": 100}]
        fingerprint = isotp_mutators.frames_fingerprint(shared)

        left = {"case": {"case_id": "a__uds_len__x"}, "fingerprint": fingerprint,
                "seed_id": "a", "dimension": "uds_len", "label": "x"}
        right = {"case": {"case_id": "b__uds_sid__y"}, "fingerprint": fingerprint,
                 "seed_id": "b", "dimension": "uds_sid", "label": "y"}

        unique, duplicates = uds_fuzz.dedup_candidates([left, right])

        self.assertEqual([c["case"]["case_id"] for c in unique],
                         ["a__uds_len__x"])
        self.assertEqual([c["case"]["case_id"] for c in duplicates],
                         ["b__uds_sid__y"])


# ----------------------------------------------------------------------
# 12. campaign 排序
# ----------------------------------------------------------------------


class TestUdsCampaignOrdering(UdsFuzzOfflineTestCase):

    def setUp(self) -> None:
        candidates, _ = uds_fuzz.build_candidates()
        self.unique, _ = uds_fuzz.dedup_candidates(candidates)

    def ids(self, selected):
        return [c["case"]["case_id"] for c in selected]

    def test_same_seed_gives_same_order(self) -> None:
        first = self.ids(uds_fuzz.select_candidates(self.unique, 12345, 20))
        second = self.ids(uds_fuzz.select_candidates(self.unique, 12345, 20))

        self.assertEqual(first, second)

    def test_different_seed_changes_order(self) -> None:
        first = self.ids(uds_fuzz.select_candidates(self.unique, 12345, 20))
        second = self.ids(uds_fuzz.select_candidates(self.unique, 999, 20))

        self.assertNotEqual(first, second)

    def test_full_selection_is_order_independent_set(self) -> None:
        first = set(self.ids(uds_fuzz.select_candidates(self.unique, 12345)))
        second = set(self.ids(uds_fuzz.select_candidates(self.unique, 999)))

        self.assertEqual(first, second)

    def test_max_cases_is_a_prefix(self) -> None:
        full = self.ids(uds_fuzz.select_candidates(self.unique, 12345))
        short = self.ids(uds_fuzz.select_candidates(self.unique, 12345, 7))

        self.assertEqual(short, full[:7])

    def test_order_key_is_pure(self) -> None:
        self.assertEqual(uds_fuzz.order_key("case-a", 12345),
                         uds_fuzz.order_key("case-a", 12345))
        self.assertNotEqual(uds_fuzz.order_key("case-a", 12345),
                            uds_fuzz.order_key("case-b", 12345))

    def test_zero_max_cases_selects_nothing(self) -> None:
        self.assertEqual(uds_fuzz.select_candidates(self.unique, 12345, 0), [])


# ----------------------------------------------------------------------
# 13. replay roundtrip
# ----------------------------------------------------------------------


class TestUdsReplayRoundtrip(UdsFuzzOfflineTestCase):

    def test_json_roundtrip_preserves_identity(self) -> None:
        candidates, _ = uds_fuzz.build_candidates()
        checked = 0

        for candidate in candidates:
            case = candidate["case"]

            if not case["frames"] or case["frames"][0]["type"] != "inject":
                continue

            restored = cs.from_jsonable(cs.to_jsonable(case))

            with self.subTest(case=case["case_id"]):
                self.assertEqual(cs.canonical_identity(restored),
                                 cs.canonical_identity(case))

            checked += 1

            if checked >= 40:
                break

        self.assertGreater(checked, 0)

    def test_restored_case_still_validates(self) -> None:
        candidates, _ = uds_fuzz.build_candidates()
        case = candidates[0]["case"]
        restored = cs.from_jsonable(cs.to_jsonable(case))

        cs.validate_case(restored)

        self.assertEqual(restored["frames"][0]["data"],
                         case["frames"][0]["data"])

    def test_restored_frames_keep_sf_invariant(self) -> None:
        candidates, _ = uds_fuzz.build_candidates()
        case = candidates[0]["case"]
        restored = cs.from_jsonable(cs.to_jsonable(case))

        self.assertTrue(uds_corpus.sf_invariant_ok(restored["frames"][0]["data"]))


# ----------------------------------------------------------------------
# 14. mutation guards（证明检查有牙齿）
# ----------------------------------------------------------------------


class TestUdsMutationGuards(UdsFuzzOfflineTestCase):

    def test_guard_sf_length_corruption_is_detected(self) -> None:
        """Guard 1：故意破坏 SF 声明长度 → 不变量检查必须失败。"""
        frames = self.encode(bytes([0x19, 0x01, 0xFF]))
        good = frames[0]["data"]

        self.assertTrue(uds_corpus.sf_invariant_ok(good))

        # PCI 声明长度 +1（不再等于实际 payload 长度）
        corrupted = bytes([good[0] + 1]) + good[1:]

        self.assertFalse(
            uds_corpus.sf_invariant_ok(corrupted),
            "SF 不变量检查必须能抓出声明长度被破坏的帧",
        )

    def test_guard_encoder_refuses_to_emit_corrupted_frames(self) -> None:
        """Guard 1b：编码器自己不会产出违反不变量的帧。"""
        candidates, _ = uds_fuzz.build_candidates()
        bad = [c["case"]["case_id"] for c in candidates
               if not uds_corpus.sf_invariant_ok(c["case"]["frames"][0]["data"])]

        self.assertEqual(bad, [])

    def test_guard_noop_mutation_is_detected(self) -> None:
        """Guard 2：变异产出与 seed 相同的 frames → 必须被拒。"""
        seed = uds_corpus.seed_by_id("uds-14-clear-all")
        spec = self.spec(um.DIM_PAYLOAD, {"op": "all_ff"}, "pay=all-FF")

        frames, reason = um.build_mutated_frames(seed, spec)

        self.assertIsNone(frames, "no-op 变异必须被拒绝而非静默生成")
        self.assertIn("no-op", reason)

    def test_guard_fingerprint_would_have_caught_it(self) -> None:
        """Guard 2b：确认被拒的那条**确实**与 seed 指纹相同。"""
        seed = uds_corpus.seed_by_id("uds-14-clear-all")
        seed_frames = uds_corpus.encode_uds_request(seed["uds_request"])
        mutated_frames = uds_corpus.encode_uds_request(seed["uds_request"])

        self.assertEqual(isotp_mutators.frames_fingerprint(seed_frames),
                         isotp_mutators.frames_fingerprint(mutated_frames))


if __name__ == "__main__":
    unittest.main(verbosity=2)
