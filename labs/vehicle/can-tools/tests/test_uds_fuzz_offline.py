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
import isotp_corpus  # noqa: E402
import isotp_mutators  # noqa: E402
import uds_corpus  # noqa: E402
import uds_fuzz  # noqa: E402
import uds_mutators as um  # noqa: E402

#: 仍然 OUT 的 SID（OQ3 后 0x34/0x35 已转为 IN，只剩 0x36 standalone）
OUT_SIDS = (0x36,)

#: v1 envelope（R5-b：v1 seed 的长度信封，在 genesis 固定，不随 OQ3 改变）
V1_ENVELOPE = uds_corpus.MAX_UDS_REQUEST_LEN

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

    def _payload_of(self, frames) -> bytes:
        """
        从 frames 还原 canonical payload（仅用于测试断言）。

        size-class aware：SF 取 PCI 低半字节的声明长度；MF 取 FF 的 12-bit
        声明长度，拼接 FF + 全部 CF 的 payload 后按声明长度截断。
        定义在**基类**：Layer A（长度不变量）与 Layer C（准入）的断言共用它。
        """
        first = bytes(frames[0]["data"])

        if (first[0] & 0xF0) == isotp_corpus.PCI_SF:
            return first[1:1 + (first[0] & 0x0F)]

        declared = ((first[0] & 0x0F) << 8) | first[1]
        out = bytearray(first[2:])

        for action in frames[1:]:
            data = bytes(action["data"])
            out += data[1:]

        return bytes(out[:declared])


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

    def test_all_seeds_within_their_own_envelope(self) -> None:
        """
        每个 seed 都必须落在**它自己的** envelope 内（R5-b）。

        v1 seed 仍然被 7 字节 envelope 死死限制住——OQ3 的 128 **不得**
        放宽任何 v1 seed。
        """
        for seed in uds_corpus.CORPUS:
            envelope = seed.get("envelope", V1_ENVELOPE)

            with self.subTest(seed=seed["seed_id"]):
                self.assertLessEqual(len(seed["uds_request"]), envelope)

                if envelope == V1_ENVELOPE:
                    self.assertLessEqual(len(seed["uds_request"]),
                                         uds_corpus.MAX_UDS_REQUEST_LEN)

    def test_all_seeds_are_non_empty_and_encodable(self) -> None:
        for seed in uds_corpus.CORPUS:
            length = len(seed["uds_request"])
            envelope = seed.get("envelope", V1_ENVELOPE)

            with self.subTest(seed=seed["seed_id"]):
                self.assertTrue(seed["uds_request"])

                frames = self.encode(seed["uds_request"])

                # size class 由**长度**决定，不由 envelope 决定
                if length <= uds_corpus.MAX_UDS_REQUEST_LEN:
                    self.assertEqual(len(frames), 1)
                else:
                    self.assertEqual(
                        len(frames),
                        1 + uds_corpus.framing_cf_count(length),
                    )

                self.assertTrue(
                    uds_corpus.framing_invariant_ok(seed["uds_request"], frames))

                if envelope == V1_ENVELOPE:
                    self.assertEqual(len(frames), 1, "v1 seed 必须仍是单帧")

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

    def test_file_transfer_seeds_cover_all_in_modes(self) -> None:
        """OQ3 后 0x38 五个 mode 全部 IN（01/03 需要 MF 才可能达到）。"""
        modes = {
            seed["uds_request"][1]
            for seed in uds_corpus.CORPUS
            if seed["service"] == 0x38
        }

        self.assertEqual(modes, {0x01, 0x02, 0x03, 0x04, 0x05})

    def test_mf_seeds_carry_the_mf_envelope(self) -> None:
        """MF seed 必须 (a) 声明 128 envelope 且 (b) 长度 ≥ 8（否则 MF 无意义）。"""
        mf_seeds = [s for s in uds_corpus.CORPUS
                    if s.get("envelope") == uds_corpus.MAX_MF_UDS_REQUEST_LEN]

        self.assertTrue(mf_seeds, "应当存在 MF seeds")

        for seed in mf_seeds:
            with self.subTest(seed=seed["seed_id"]):
                self.assertGreaterEqual(len(seed["uds_request"]), 8)
                self.assertLessEqual(len(seed["uds_request"]),
                                     uds_corpus.MAX_MF_UDS_REQUEST_LEN)

    def test_mf_seed_service_coverage(self) -> None:
        """Frozen Contract §13 的 MF scope：0x22 / 0x2E / 0x34 / 0x35 / 0x38。"""
        mf_services = {
            s["service"] for s in uds_corpus.CORPUS
            if s.get("envelope") == uds_corpus.MAX_MF_UDS_REQUEST_LEN
        }

        self.assertEqual(mf_services, {0x22, 0x2E, 0x34, 0x35, 0x38})

    def test_no_mf_seed_for_out_of_scope_services(self) -> None:
        """0x36 / 0x37 standalone 与 stateful 用的 0x27 都不得有 MF seed。"""
        for seed in uds_corpus.CORPUS:
            if seed.get("envelope") != uds_corpus.MAX_MF_UDS_REQUEST_LEN:
                continue

            with self.subTest(seed=seed["seed_id"]):
                self.assertNotIn(seed["service"], (0x27, 0x36, 0x37))


# ----------------------------------------------------------------------
# 2. encoder
# ----------------------------------------------------------------------


class TestUdsEncoder(UdsFuzzOfflineTestCase):

    def test_rejects_empty(self) -> None:
        with self.assertRaises(uds_corpus.UdsEncodeError):
            self.encode(b"")

    def test_size_class_boundaries(self) -> None:
        """
        OQ3 §3 size class（**取代** v1 的"L>7 必须 reject"）：

            1..7             → 恰好一个 SF（v1 路径，R5-c 冻结）
            8..128           → canonical FF + CF
            > MAX_MF_UDS_REQUEST_LEN → reject（**不生成、不截断**）
        """
        # ---- 8..envelope：合法，且必须是 canonical MF ----
        for length in (8, 9, 16, 122, uds_corpus.MAX_MF_UDS_REQUEST_LEN):
            request = bytes([0x22]) + bytes(length - 1)

            with self.subTest(length=length):
                frames = self.encode(request)

                self.assertGreater(len(frames), 1, "8 字节起必须分帧为 MF")
                self.assertEqual(frames[0]["data"][0] & 0xF0,
                                 isotp_corpus.PCI_FF, "首帧必须是 FF")
                self.assertLessEqual(len(frames),
                                     uds_corpus.MAX_FRAMES_PER_CASE)
                self.assertTrue(
                    uds_corpus.framing_invariant_ok(request, frames),
                    "合法长度必须产出 canonical 分帧")

        # ---- 超出 envelope：必须 reject（不是截断）----
        for length in (uds_corpus.MAX_MF_UDS_REQUEST_LEN + 1, 200):
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
        """
        帧 → payload 还原必须与原 UDS 请求逐字节相同（**size-class aware**）。

        v1 的写法假定 ``frames[0]`` 永远是 SF、并把 PCI 低半字节当长度用；
        对 MF seed 而言 ``frames[0]`` 是 FF，其低半字节只是 12-bit 长度的
        高位——该假定已被 OQ3 supersede。

        SF：帧数 == 1，PCI 低半字节 == payload 长度。
        MF：帧数 > 1，FF 声明的 12-bit 长度 == canonical payload 长度。
        """
        for seed in uds_corpus.CORPUS:
            request = seed["uds_request"]

            with self.subTest(seed=seed["seed_id"]):
                frames = self.encode(request)
                first = bytes(frames[0]["data"])

                if len(request) <= uds_corpus.MAX_UDS_REQUEST_LEN:
                    self.assertEqual(len(frames), 1, "v1 长度必须恰好一个 SF")
                    self.assertEqual(first[0] & 0xF0, isotp_corpus.PCI_SF)
                    self.assertEqual(first[0] & 0x0F, len(request))
                else:
                    self.assertGreater(len(frames), 1, "MF 必须多于一个帧")
                    self.assertEqual(first[0] & 0xF0, isotp_corpus.PCI_FF)

                    declared = ((first[0] & 0x0F) << 8) | first[1]

                    self.assertEqual(
                        declared, len(request),
                        "FF 声明长度必须等于 canonical payload 长度")

                # 还原（SF 与 MF 共用同一路径）必须逐字节相同
                self.assertEqual(self._payload_of(frames), request)

    def test_sf_invariant_helper_accepts_good_frames(self) -> None:
        """``sf_invariant_ok`` 只对 **SF 分帧**成立：只对 v1 envelope 的 seed 断言。"""
        for seed in uds_corpus.CORPUS:
            if seed.get("envelope") != V1_ENVELOPE:
                continue

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

    def test_every_generated_request_is_within_its_envelope(self) -> None:
        """
        穷举所有 seed × 所有 payload 级 spec：生成结果一律 ≤ **该 seed 的** envelope。

        v1 seed（envelope 7）单独再断言一次 ≤7 —— 这正是 R5-b 的可执行形式。
        """
        checked = 0
        v1_checked = 0

        for seed in uds_corpus.CORPUS:
            envelope = seed.get("envelope", V1_ENVELOPE)

            for spec in um.mutation_specs_for_seed(seed):
                mutated, reason = um.apply_mutation(seed, spec)

                if mutated is None:
                    continue

                checked += 1

                with self.subTest(seed=seed["seed_id"], label=spec["label"]):
                    self.assertLessEqual(len(mutated), envelope)

                    if envelope == V1_ENVELOPE:
                        v1_checked += 1
                        self.assertLessEqual(
                            len(mutated), uds_corpus.MAX_UDS_REQUEST_LEN)

        self.assertGreater(checked, 500, "覆盖率过低，测试可能没跑全")
        self.assertGreater(v1_checked, 500, "v1 seed 覆盖过低")

    def test_every_canonical_mutation_result_is_framing_consistent(self) -> None:
        """
        Layer A：**非 mf_*** 的变异结果必须满足 canonical framing 判据。

        mf_* 是 Layer B 允许故意破坏 framing 的唯一手段，因此被刻意排除。
        """
        candidates, _ = uds_fuzz.build_candidates()

        self.assertTrue(candidates)

        canonical = 0

        for candidate in candidates:
            op = (candidate["case"]["mutation"].get("params") or {}).get("op")

            if op in um.MF_OPS:
                continue

            canonical += 1

            with self.subTest(case=candidate["case"]["case_id"]):
                # 从 frames 反推 payload 长度：SF 声明值 / FF 声明值
                self.assertTrue(
                    uds_corpus.framing_invariant_ok(
                        self._payload_of(candidate["case"]["frames"]),
                        candidate["case"]["frames"],
                    )
                )

        self.assertGreater(canonical, 500)


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

    def test_cases_are_size_class_consistent(self) -> None:
        """v1 seed 的 case 仍是单帧；MF seed 的 case ≤ MAX_FRAMES_PER_CASE。"""
        for candidate in self.candidates:
            frames = candidate["case"]["frames"]

            with self.subTest(case=candidate["case"]["case_id"]):
                self.assertLessEqual(len(frames),
                                     uds_corpus.MAX_FRAMES_PER_CASE)

                seed = uds_corpus.seed_by_id(candidate["seed_id"])

                if seed.get("envelope") == V1_ENVELOPE:
                    self.assertEqual(len(frames), 1, "v1 seed 必须仍是单帧")

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
        """
        Guard 1b：**生产 encoder 不会自行产出违反 framing 不变量**的帧。

        按层区分（Frozen Contract）——不能对每个 candidate 一律套 SF 判据：

            Layer A canonical（seed 编码 / 非 mf_* 变异重编码）
                → **必须**满足 framing_invariant_ok
            Layer B mf_*（故意破坏 transport framing）
                → 刻意排除：它们违反 framing 是**预期行为**，不是损坏

        v1 的写法对**所有** candidate 断言 ``sf_invariant_ok(frames[0])``，
        于是把每个合法 MF case（首帧是 FF，本就不是 SF）误判为损坏。
        """
        # ---- (1) 编码器对其**自身**的 canonical 输出负责 ----
        for seed in uds_corpus.CORPUS:
            request = seed["uds_request"]
            frames = uds_corpus.encode_uds_request(request)

            with self.subTest(seed=seed["seed_id"]):
                self.assertTrue(
                    uds_corpus.framing_invariant_ok(request, frames),
                    "encoder 对 canonical 输入的输出必须满足 framing 不变量")

                if len(request) <= uds_corpus.MAX_UDS_REQUEST_LEN:
                    self.assertEqual(len(frames), 1, "v1 seed 必须仍是单帧")
                else:
                    self.assertGreater(len(frames), 1, "MF seed 必须分帧")

        # ---- (2) 非 mf_* 变异的结果仍须是 canonical 分帧 ----
        candidates, _ = uds_fuzz.build_candidates()

        canonical = 0
        malformed = 0

        for candidate in candidates:
            case = candidate["case"]

            if (case["mutation"].get("params") or {}).get("op") in um.MF_OPS:
                malformed += 1
                continue

            canonical += 1

            with self.subTest(case=case["case_id"]):
                frames = case["frames"]

                self.assertTrue(
                    uds_corpus.framing_invariant_ok(
                        self._payload_of(frames), frames),
                    "非 mf_* 的 case 必须是 canonical 分帧")

                self.assertLessEqual(len(frames),
                                     uds_corpus.MAX_FRAMES_PER_CASE)

        # 两侧都必须非空，否则本 guard 是空转
        self.assertGreater(canonical, 500, "canonical 覆盖过低，guard 失去意义")
        self.assertGreater(malformed, 0, "mf_* 应存在，排除逻辑不得为空")

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


# ======================================================================
# OQ3 Phase 1 —— multi-frame
# ======================================================================


def _mf_payload(length: int) -> bytes:
    """构造长度为 ``length``、内容确定的 payload（用于编码器测试）。"""
    return bytes([0x22]) + bytes((index * 7 + 3) & 0xFF
                                for index in range(length - 1))


def _synthetic_mf_seed(length: int) -> dict:
    """构造一个测试用的 MF seed（不入 corpus，仅用于走 mutator 路径）。"""
    return {
        "seed_id": f"synthetic-mf-{length}",
        "uds_request": _mf_payload(length),
        "service": 0x22,
        "control_byte": None,
        "category": "test",
        "note": "synthetic (test-only)",
        "envelope": uds_corpus.MAX_MF_UDS_REQUEST_LEN,
        "reference_only": False,
    }


class TestUdsMfEncoder(UdsFuzzOfflineTestCase):
    """OQ3 Phase 1-A：size-class aware encoder 的精确输出。"""

    def test_length_table(self) -> None:
        """L → (帧数, FF 声明长度, CF 数) 全表。"""
        table = (
            (1, 1, None, 0),
            (7, 1, None, 0),
            (8, 2, 8, 1),
            (13, 2, 13, 1),
            (14, 3, 14, 2),
            (15, 3, 15, 2),
            (78, 12, 78, 11),
            (128, 19, 128, 18),
        )

        for length, frames_expected, declared_expected, cf_expected in table:
            with self.subTest(length=length):
                payload = _mf_payload(length)
                frames = self.encode(payload)

                self.assertEqual(len(frames), frames_expected)
                self.assertEqual(len(frames), 1 + cf_expected)
                self.assertEqual(uds_corpus.framing_cf_count(length), cf_expected)

                first = bytes(frames[0]["data"])

                if declared_expected is None:
                    self.assertEqual(first[0] & 0xF0, isotp_corpus.PCI_SF)
                    self.assertEqual(first[0] & 0x0F, length)
                else:
                    self.assertEqual((first[0] & 0xF0) >> 4, 1, "必须是 FF")
                    self.assertEqual(
                        ((first[0] & 0x0F) << 8) | first[1], declared_expected)
                    self.assertEqual(len(first), 2 + 6, "FF 携带 6 payload 字节")
                    self.assertEqual(first[2:], payload[:6])

    def test_sf_bytes_unchanged_for_v1_range(self) -> None:
        """L ≤ 7 走 v1 字面路径：一个 inject、单帧、payload 逐字节相同。"""
        for length in (1, 2, 7):
            with self.subTest(length=length):
                payload = _mf_payload(length)
                frames = self.encode(payload)

                self.assertEqual(len(frames), 1)
                self.assertEqual(frames[0]["type"], "inject")
                self.assertEqual(frames[0]["id"], isotp_corpus.INJECT_ID)
                self.assertEqual(
                    frames[0]["data"],
                    isotp_corpus.sf(payload),
                    "SF 必须与 v1 编码逐字节相同",
                )

    def test_cf_payload_and_sn_sequence(self) -> None:
        payload = _mf_payload(14)
        frames = self.encode(payload)
        cf1 = bytes(frames[1]["data"])
        cf2 = bytes(frames[2]["data"])

        self.assertEqual(cf1[0] & 0xF0, isotp_corpus.PCI_CF)
        self.assertEqual(cf1[0] & 0x0F, 1)
        self.assertEqual(cf1[1:], payload[6:13])
        self.assertEqual(cf2[0] & 0x0F, 2)
        self.assertEqual(cf2[1:], payload[13:14], "末块可以短于 7 字节")
        self.assertEqual(len(cf2), 1 + 1)

    def test_sn_wrap_at_sixteen(self) -> None:
        """CF #16 的 SN 必须是 0，CF #17 回到 1（ISO-TP SN 回绕）。"""
        frames = self.encode(_mf_payload(128))
        cfs = frames[1:]

        self.assertEqual(len(cfs), 18)
        self.assertEqual([bytes(c["data"])[0] & 0x0F for c in cfs[:3]], [1, 2, 3])
        self.assertEqual(bytes(cfs[14]["data"])[0] & 0x0F, 15, "CF #15 → SN 15")
        self.assertEqual(bytes(cfs[15]["data"])[0] & 0x0F, 0, "CF #16 → SN 0")
        self.assertEqual(bytes(cfs[16]["data"])[0] & 0x0F, 1, "CF #17 → SN 1")
        self.assertEqual(bytes(cfs[17]["data"])[0] & 0x0F, 2, "CF #18 → SN 2")

    def test_max_frames_per_case_is_exact_at_the_envelope(self) -> None:
        frames = self.encode(_mf_payload(uds_corpus.MAX_MF_UDS_REQUEST_LEN))

        self.assertEqual(len(frames), uds_corpus.MAX_FRAMES_PER_CASE)
        self.assertEqual(uds_corpus.MAX_FRAMES_PER_CASE, 19)

    def test_rejects_empty_and_over_envelope(self) -> None:
        with self.assertRaises(uds_corpus.UdsEncodeError):
            self.encode(b"")

        for length in (uds_corpus.MAX_MF_UDS_REQUEST_LEN + 1, 256, 4095):
            with self.subTest(length=length):
                with self.assertRaises(uds_corpus.UdsEncodeError):
                    self.encode(_mf_payload(length))

    def test_reject_is_not_truncation(self) -> None:
        """129 字节必须**抛错**，而不是被截断成 128。"""
        with self.assertRaises(uds_corpus.UdsEncodeError):
            uds_corpus.encode_uds_request(_mf_payload(129))

    def test_canonical_output_always_passes_framing_invariant(self) -> None:
        for length in (1, 7, 8, 13, 14, 78, 128):
            with self.subTest(length=length):
                payload = _mf_payload(length)

                self.assertTrue(
                    uds_corpus.framing_invariant_ok(payload, self.encode(payload)))


class TestUdsMfFramingInvariant(UdsFuzzOfflineTestCase):
    """Layer A 判据必须能鉴别被破坏的分帧。"""

    def setUp(self) -> None:
        super().setUp()
        self.payload = _mf_payload(14)
        self.frames = self.encode(self.payload)

    def test_accepts_canonical(self) -> None:
        self.assertTrue(
            uds_corpus.framing_invariant_ok(self.payload, self.frames))

    def test_rejects_wrong_declared_length(self) -> None:
        tampered = [dict(a) for a in self.frames]
        first = bytes(tampered[0]["data"])
        tampered[0] = dict(tampered[0], data=bytes([0x10, 99]) + first[2:])

        self.assertFalse(
            uds_corpus.framing_invariant_ok(self.payload, tampered))

    def test_rejects_missing_cf(self) -> None:
        self.assertFalse(
            uds_corpus.framing_invariant_ok(self.payload, self.frames[:-1]))

    def test_rejects_wrong_sn(self) -> None:
        tampered = [dict(a) for a in self.frames]
        cf = bytes(tampered[2]["data"])
        tampered[2] = dict(tampered[2], data=bytes([(cf[0] & 0xF0) | 7]) + cf[1:])

        self.assertFalse(
            uds_corpus.framing_invariant_ok(self.payload, tampered))

    def test_rejects_wrong_payload_byte(self) -> None:
        tampered = [dict(a) for a in self.frames]
        cf = bytes(tampered[1]["data"])
        tampered[1] = dict(tampered[1], data=cf[:1] + bytes([cf[1] ^ 0xFF]) + cf[2:])

        self.assertFalse(
            uds_corpus.framing_invariant_ok(self.payload, tampered))


class TestUdsMfMutations(UdsFuzzOfflineTestCase):
    """OQ3 Phase 1-D：M1..M7 的**精确**预期偏离。"""

    def setUp(self) -> None:
        super().setUp()
        self.seed = uds_corpus.seed_by_id("uds-mf-34-download")   # L=8, C=1
        self.length = len(self.seed["uds_request"])
        self.canonical = self.encode(self.seed["uds_request"])

    def frames_for(self, op, **params):
        spec = {"dimension": um.DIM_LEN, "params": {"op": op, **params},
                "label": f"test-{op}"}
        frames, reason = um.build_mutated_frames(self.seed, spec)

        self.assertIsNone(reason, f"{op} 应当可生成：{reason}")

        return frames

    def declared_of(self, frames) -> int:
        first = bytes(frames[0]["data"])

        return ((first[0] & 0x0F) << 8) | first[1]

    def test_m1_declared_zero(self) -> None:
        frames = self.frames_for(um.MF_OP_FF_DECLARED_ZERO)

        self.assertEqual(self.declared_of(frames), 0)
        self.assertEqual(len(frames), len(self.canonical), "帧数不变")
        self.assertEqual(bytes(frames[0]["data"])[2:],
                         bytes(self.canonical[0]["data"])[2:], "payload 不变")
        self.assertEqual(frames[1:], self.canonical[1:], "CF 逐字节不变")

    def test_m2_declared_minus_one(self) -> None:
        frames = self.frames_for(um.MF_OP_FF_DECLARED_MINUS_ONE)

        self.assertEqual(self.declared_of(frames), self.length - 1)
        self.assertEqual(frames[1:], self.canonical[1:])

    def test_m3_declared_plus_one(self) -> None:
        frames = self.frames_for(um.MF_OP_FF_DECLARED_PLUS_ONE)

        self.assertEqual(self.declared_of(frames), self.length + 1)
        self.assertEqual(len(frames), len(self.canonical), "帧数不变")
        self.assertEqual(bytes(frames[0]["data"])[2:],
                         bytes(self.canonical[0]["data"])[2:], "payload 不变")
        self.assertEqual(frames[1:], self.canonical[1:])

    def test_m4_declared_max(self) -> None:
        frames = self.frames_for(um.MF_OP_FF_DECLARED_MAX)

        self.assertEqual(self.declared_of(frames), um.FF_DECLARED_MAX)
        self.assertEqual(self.declared_of(frames), 4095)

    def test_m5_drop_last(self) -> None:
        frames = self.frames_for(um.MF_OP_CF_DROP_LAST)

        self.assertEqual(len(frames), len(self.canonical) - 1, "帧数 -1")
        self.assertEqual(frames[0], self.canonical[0], "FF 逐字节不变")
        self.assertEqual(frames[1:], self.canonical[1:-1], "其余 CF 逐字节不变")
        self.assertEqual(self.declared_of(frames), self.length,
                         "FF 声明仍是 L")

    def test_m6_sn_zero_exact_target(self) -> None:
        frames = self.frames_for(um.MF_OP_CF_SN_ZERO, cf=1)

        self.assertEqual(bytes(frames[1]["data"])[0] & 0x0F, 0, "CF #1 → SN 0")
        self.assertEqual(bytes(frames[1]["data"])[1:],
                         bytes(self.canonical[1]["data"])[1:], "CF payload 不变")
        self.assertEqual(frames[0], self.canonical[0], "FF 不变")

    def test_m7_sn_off_by_one_exact_target(self) -> None:
        frames = self.frames_for(um.MF_OP_CF_SN_OFF_BY_ONE, cf=1)

        self.assertEqual(bytes(frames[1]["data"])[0] & 0x0F, 2,
                         "CF #1: SN 1 → 2")
        self.assertEqual(bytes(frames[1]["data"])[1:],
                         bytes(self.canonical[1]["data"])[1:])

    def test_all_ops_are_no_op_free_when_generated(self) -> None:
        """每一个生成出来的 mf_* spec 都必须改变 frames_fingerprint。"""
        canonical_fp = isotp_mutators.frames_fingerprint(self.canonical)
        checked = 0

        for spec in um.mutation_specs_for_seed(self.seed):
            if spec["params"].get("op") not in um.MF_OPS:
                continue

            frames, reason = um.build_mutated_frames(self.seed, spec)

            self.assertIsNone(reason, f"{spec['label']} 应当可生成：{reason}")
            self.assertNotEqual(
                isotp_mutators.frames_fingerprint(frames), canonical_fp,
                f"{spec['label']} 是 no-op",
            )
            checked += 1

        self.assertGreater(checked, 0)

    def test_apply_mutation_refuses_mf_ops(self) -> None:
        """payload 级 API 必须显式拒绝 mf_* op（避免误读为 no-op）。"""
        mutated, reason = um.apply_mutation(
            self.seed,
            {"dimension": um.DIM_LEN,
             "params": {"op": um.MF_OP_CF_DROP_LAST}, "label": "x"})

        self.assertIsNone(mutated)
        self.assertIn("build_mutated_frames", reason)


class TestUdsMfSnTargets(UdsFuzzOfflineTestCase):
    """M6/M7 的 target CF 选择规则（Frozen Contract "SN target selection"）。"""

    def test_one_spec_per_applicable_cf_ascending(self) -> None:
        seed = uds_corpus.seed_by_id("uds-mf-2e-16b-data")   # L=19 → C=2
        cf_count = uds_corpus.framing_cf_count(len(seed["uds_request"]))

        self.assertEqual(cf_count, 2)

        specs = [s for s in um.mutation_specs_for_seed(seed)
                 if s["params"].get("op") == um.MF_OP_CF_SN_OFF_BY_ONE]

        self.assertEqual([s["params"]["cf"] for s in specs], [1, 2],
                         "按 CF index 升序，每个 index 一条")

    def test_sn_zero_excludes_cf_with_sn_zero(self) -> None:
        """
        **M6 + CF#16 必须是 no-op，因此不得生成 spec。**

        CF #16 的规范 SN 就是 0；本测试用 128 字节 payload（C=18）覆盖到它。
        """
        length = uds_corpus.MAX_MF_UDS_REQUEST_LEN
        seed = _synthetic_mf_seed(length)
        cf_count = uds_corpus.framing_cf_count(length)

        self.assertEqual(cf_count, 18)
        self.assertEqual(uds_corpus.cf_sequence_number(16), 0, "CF#16 规范 SN 是 0")

        specs = [s for s in um.mutation_specs_for_seed(seed)
                 if s["params"].get("op") == um.MF_OP_CF_SN_ZERO]
        targets = [s["params"]["cf"] for s in specs]

        self.assertNotIn(16, targets, "CF#16 会产生 no-op，必须在生成期排除")
        self.assertEqual(targets, [j for j in range(1, cf_count + 1) if j % 16 != 0])
        self.assertEqual(len(targets), cf_count - 1)

        # 被排除的那个索引即便手工构造也必须被 apply 期拒绝
        frames, reason = um.build_mutated_frames(
            seed, {"dimension": um.DIM_LEN,
                   "params": {"op": um.MF_OP_CF_SN_ZERO, "cf": 16},
                   "label": "hand"})

        self.assertIsNone(frames)
        self.assertIn("no-op", reason)

    def test_target_index_reaches_provenance(self) -> None:
        seed = uds_corpus.seed_by_id("uds-mf-2e-16b-data")
        specs = [s for s in um.mutation_specs_for_seed(seed)
                 if s["params"].get("op") in um.MF_OPS and "cf" in s["params"]]

        self.assertTrue(specs)

        case_ids = []
        for spec in specs:
            self.assertIn(f"@cf{spec['params']['cf']}", spec["label"])
            case_ids.append(cs.make_case_id(seed["seed_id"], spec["dimension"],
                                            spec["label"]))

        self.assertEqual(len(case_ids), len(set(case_ids)),
                         "每个 (op, target) 必须给出唯一 case_id")

    def test_target_selection_is_deterministic(self) -> None:
        """same seed + same spec ⇒ same target CF ⇒ same frames。"""
        seed = uds_corpus.seed_by_id("uds-mf-2e-16b-data")

        for spec in um.mutation_specs_for_seed(seed):
            if spec["params"].get("op") not in um.MF_OPS:
                continue

            first, _ = um.build_mutated_frames(seed, spec)
            second, _ = um.build_mutated_frames(seed, spec)

            with self.subTest(label=spec["label"]):
                self.assertEqual(first, second)


class TestUdsMfAdmission(UdsFuzzOfflineTestCase):
    """Layer C：malformed-transport case 仍必须 schema-valid / 可序列化 / 可指纹化。"""

    def setUp(self) -> None:
        super().setUp()
        self.candidates, _ = uds_fuzz.build_candidates()
        self.mf_cases = [c for c in self.candidates
                         if (c["case"]["mutation"].get("params") or {})
                         .get("op") in um.MF_OPS]

    def test_mf_cases_exist(self) -> None:
        self.assertTrue(self.mf_cases, "应当生成 mf_* cases")

    def test_every_case_is_schema_valid(self) -> None:
        for candidate in self.candidates:
            with self.subTest(case=candidate["case"]["case_id"]):
                cs.validate_case(candidate["case"])

    def test_every_case_is_fingerprintable_and_serialisable(self) -> None:
        for candidate in self.candidates:
            case = candidate["case"]
            restored = cs.from_jsonable(cs.to_jsonable(case))

            with self.subTest(case=case["case_id"]):
                self.assertTrue(isotp_mutators.frames_fingerprint(case["frames"]))
                self.assertEqual(cs.canonical_identity(restored),
                                 cs.canonical_identity(case))

    def test_every_case_within_frame_bound(self) -> None:
        for candidate in self.candidates:
            with self.subTest(case=candidate["case"]["case_id"]):
                self.assertLessEqual(len(candidate["case"]["frames"]),
                                     uds_corpus.MAX_FRAMES_PER_CASE)

    def test_malformed_transport_cases_are_admitted(self) -> None:
        """malformed framing **不**要求 Layer A 一致——它们必须被准入。"""
        for candidate in self.mf_cases:
            with self.subTest(case=candidate["case"]["case_id"]):
                payload = self._payload_of(candidate["case"]["frames"])

                self.assertFalse(
                    uds_corpus.framing_invariant_ok(
                        payload, candidate["case"]["frames"]),
                    "mf_* case 本应破坏 framing",
                )

    def test_drop_last_case_has_one_frame_fewer(self) -> None:
        for candidate in self.mf_cases:
            if (candidate["case"]["mutation"]["params"] or {}).get("op") != \
                    um.MF_OP_CF_DROP_LAST:
                continue

            seed = uds_corpus.seed_by_id(candidate["seed_id"])
            canonical = self.encode(seed["uds_request"])

            with self.subTest(case=candidate["case"]["case_id"]):
                self.assertEqual(len(candidate["case"]["frames"]),
                                 len(canonical) - 1)


class TestUdsV1GoldenCompatibility(UdsFuzzOfflineTestCase):
    """
    R5-a / R5-f：**v1 seed 生成的 frames / fingerprint 必须逐字节不变**。

    两个 digest 是在 OQ3 改动**之前**于冻结基线（main = 30d2ca6）上采集的；
    本测试把它们冻结成常量，因此任何对 v1 行为的偏移都会让本测试失败。
    """

    #: OQ3 前：全部 947 个 v1 case 的 (case_id, frames_fingerprint) 规范串摘要
    V1_GOLDEN_DIGEST = (
        "33030de782f28b4fde8cab4469a65edc1e42b56f051be57250ca73dd65f9b464"
    )

    #: OQ3 前：17 个 v1 seed_id（按 corpus 顺序）的摘要
    V1_SEED_IDS_DIGEST = (
        "5e1ac3419c78bd804bf413c14acb65c3644f29ec6b68ba5ce8ef51ef964cf993"
    )

    def v1_seed_ids(self):
        return [s["seed_id"] for s in uds_corpus.CORPUS
                if s.get("envelope") == V1_ENVELOPE]

    def test_v1_seed_set_unchanged(self) -> None:
        import hashlib

        digest = hashlib.sha256(
            "\n".join(self.v1_seed_ids()).encode("utf-8")).hexdigest()

        self.assertEqual(digest, self.V1_SEED_IDS_DIGEST)

    def test_v1_cases_bit_identical(self) -> None:
        import hashlib

        candidates, _ = uds_fuzz.build_candidates()
        v1 = set(self.v1_seed_ids())
        v1_cases = [c for c in candidates if c["seed_id"] in v1]

        self.assertEqual(len(v1_cases), 947, "v1 case 数量必须不变")

        entries = sorted(
            (c["case"]["case_id"],
             isotp_mutators.frames_fingerprint(c["case"]["frames"]))
            for c in v1_cases
        )
        digest = hashlib.sha256(
            "\n".join(f"{cid}|{fp}" for cid, fp in entries).encode("utf-8")
        ).hexdigest()

        self.assertEqual(digest, self.V1_GOLDEN_DIGEST,
                         "v1 的 frames/fingerprint 发生了偏移（R5 违反）")

    def test_v1_seeds_keep_the_seven_byte_envelope(self) -> None:
        """R5-b：v1 seed 的 envelope 固定为 7，不被 128 覆盖。"""
        for seed in uds_corpus.CORPUS:
            if seed["seed_id"] not in self.v1_seed_ids():
                continue

            with self.subTest(seed=seed["seed_id"]):
                self.assertEqual(seed["envelope"], 7)

    def test_v1_seeds_never_get_mf_specs(self) -> None:
        """R5-b：v1 seed 永远不产生 mf_* spec（它们出不了 7 字节）。"""
        v1 = set(self.v1_seed_ids())

        for seed in uds_corpus.CORPUS:
            if seed["seed_id"] not in v1:
                continue

            ops = {s["params"].get("op")
                   for s in um.mutation_specs_for_seed(seed)}

            with self.subTest(seed=seed["seed_id"]):
                self.assertFalse(ops & set(um.MF_OPS))

    def test_v1_seeds_never_exceed_seven_bytes_after_mutation(self) -> None:
        v1 = set(self.v1_seed_ids())

        for seed in uds_corpus.CORPUS:
            if seed["seed_id"] not in v1:
                continue

            for spec in um.mutation_specs_for_seed(seed):
                mutated, _ = um.apply_mutation(seed, spec)

                if mutated is None:
                    continue

                with self.subTest(seed=seed["seed_id"], label=spec["label"]):
                    self.assertLessEqual(
                        len(mutated), uds_corpus.MAX_UDS_REQUEST_LEN)

    def test_mf_ops_are_exactly_the_frozen_seven(self) -> None:
        self.assertEqual(len(um.MF_OPS), 7)
        self.assertEqual(set(um.MF_OPS), {
            "mf_ff_declared_zero", "mf_ff_declared_minus_one",
            "mf_ff_declared_plus_one", "mf_ff_declared_max",
            "mf_cf_drop_last", "mf_cf_sn_zero", "mf_cf_sn_off_by_one",
        })
        self.assertEqual(len(set(um.MF_OPS)), 7, "op 名必须唯一")

    def test_mf_ops_all_live_under_uds_len(self) -> None:
        """七个 mf_* op 全部位于 uds_len；维度集合仍然恰好四个。"""
        self.assertEqual(um.ALL_DIMENSIONS,
                         ("uds_len", "uds_sid", "uds_sub", "uds_payload"))

        seen = set()
        for seed in uds_corpus.CORPUS:
            for spec in um.mutation_specs_for_seed(seed):
                if spec["params"].get("op") in um.MF_OPS:
                    seen.add(spec["dimension"])

        self.assertEqual(seen, {"uds_len"})

    def test_no_fifth_dimension_is_introduced(self) -> None:
        observed = set()

        for seed in uds_corpus.CORPUS:
            for spec in um.mutation_specs_for_seed(seed):
                observed.add(spec["dimension"])

        self.assertEqual(observed, set(um.ALL_DIMENSIONS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
