#!/usr/bin/env python3
"""
P4X 离线测试：schema / mutator / determinism / replay

**不需要 vcan0**：本文件只测试纯数据与纯函数层，
不启动 ECU、不打开 CAN socket。

覆盖（Phase 4X-P1 §34）::

    case schema validation
    invalid interface / CAN ID / DLC / frame hex
    mutation 不改变无关字段
    same seed + mutation = same frames
    case_id 稳定
    replay frames identical
    SN / PCI / SF / FF / FC status / BS / STmin / padding mutation

运行::

    /home/lilike/.venvs/network/bin/python tests/test_fuzz_offline.py
"""

import json
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
import isotp_corpus as corpus  # noqa: E402
import isotp_mutators as mut  # noqa: E402


# ----------------------------------------------------------------------
# corpus 层：必须是纯数据
# ----------------------------------------------------------------------


def _code_tokens(path: str) -> set:
    """
    提取一个 Python 源文件的"代码层"字符串 token。

    只用 AST，**不含注释**；docstring 单独排除——因为文档里可能合理提到
    ``subprocess`` / ``socket`` 等词，用纯文本 grep 会产生假阳性。
    """
    import ast

    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    docstrings = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)

            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(body[0].value.value)

    tokens = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                tokens.add(node.value)
        elif isinstance(node, ast.Name):
            tokens.add(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.add(node.attr)

    return tokens


class TestCorpusIsPureData(unittest.TestCase):
    """corpus 与 mutator 不允许引入 I/O 或 CAN 依赖。"""

    def test_corpus_has_no_can_or_io_imports(self) -> None:
        tokens = _code_tokens(os.path.join(_FUZZ_DIR, "isotp_corpus.py"))

        for forbidden in ("can", "isotp", "subprocess", "socket", "time"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, tokens)

    def test_mutators_have_no_io_or_send(self) -> None:
        tokens = _code_tokens(os.path.join(_FUZZ_DIR, "isotp_mutators.py"))

        for forbidden in ("can", "isotp", "subprocess", "socket", "time"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, tokens)

    def test_fuzz_engine_does_not_import_ecu_side(self) -> None:
        """fuzzer 不能 import ECU 侧模块（保持独立 tester 身份）。"""
        import ast

        with open(os.path.join(_FUZZ_DIR, "isotp_fuzz.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())

        imported = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])

        for forbidden in ("ecu", "uds_server", "uds_types", "keygen",
                          "did_database"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, imported)


class TestCorpusContents(unittest.TestCase):
    """corpus 覆盖 P1 §5 要求的类别。"""

    def test_required_categories_present(self) -> None:
        for category in ("baseline", "pci", "single_frame", "first_frame",
                         "consecutive_frame", "sequence_number", "flow_control",
                         "padding", "recovery"):
            with self.subTest(category=category):
                self.assertTrue(
                    corpus.seeds_by_category(category),
                    f"类别 {category} 为空",
                )

    def test_addressing_is_reference_only(self) -> None:
        """addressing 保留为参考，不参与变异。"""
        for s in corpus.seeds_by_category("addressing"):
            self.assertTrue(s["reference_only"])

        self.assertNotIn("addressing", corpus.MUTABLE_CATEGORIES)

    def test_category_names_in_seed(self) -> None:
        """每个 seed 的 category 必须在 MUTABLE 或 addressing 之中。"""
        valid = set(corpus.CATEGORIES)

        for s in corpus.CORPUS:
            with self.subTest(seed=s["seed_id"]):
                self.assertIn(s["category"], valid)

    def test_can_ids_are_fixed(self) -> None:
        self.assertEqual(corpus.INJECT_ID, 0x7E0)
        self.assertEqual(corpus.OBSERVE_ID, 0x7E8)
        self.assertEqual(corpus.INTERFACE, "vcan0")

    def test_seed_ids_unique(self) -> None:
        ids = [s["seed_id"] for s in corpus.CORPUS]
        self.assertEqual(len(ids), len(set(ids)), "seed_id 有重复")

    def test_all_frames_within_dlc(self) -> None:
        for s in corpus.CORPUS:
            for action in s["actions"]:
                if action["type"] != "inject":
                    continue

                with self.subTest(seed=s["seed_id"]):
                    self.assertLessEqual(
                        len(action["data"]), corpus.CAN_DATA_MAX_LEN
                    )

    def test_mutation_specs_match_structure(self) -> None:
        """没有 CF 的 seed 不应产生 SN 变异（P1 §15：不造无意义 mutation）。"""
        for s in corpus.CORPUS:
            if s.get("reference_only"):
                continue

            specs = mut.mutation_specs_for_seed(s)
            dims = {sp["dimension"] for sp in specs}

            # 用与生产 mutator 相同的 PCI 判定（await_fc 语义上是 CTS）
            has_cf = any(
                mut._pci_of(a) == corpus.PCI_CF for a in s["actions"]
            )
            has_fc = any(
                mut._pci_of(a) == corpus.PCI_FC for a in s["actions"]
            )

            if not has_cf:
                with self.subTest(seed=s["seed_id"]):
                    self.assertNotIn(mut.DIM_SN, dims)

            if not has_fc:
                with self.subTest(seed=s["seed_id"]):
                    self.assertNotIn(mut.DIM_FC_STATUS, dims)
                    self.assertNotIn(mut.DIM_BS, dims)
                    self.assertNotIn(mut.DIM_STMIN, dims)


# ----------------------------------------------------------------------
# schema 校验
# ----------------------------------------------------------------------


def _sample_case() -> dict:
    seed_def = corpus.get_seed("SN_SKIP_1_3")
    spec = {"dimension": mut.DIM_SN, "params": {"value": 2, "which": "last"},
            "label": "sn=2"}
    actions, _ = mut.apply_mutation(seed_def, spec)

    return cs.build_case(
        case_id=cs.make_case_id(seed_def["seed_id"], spec["dimension"], spec["label"]),
        seed_id=seed_def["seed_id"],
        mutation={"dimension": spec["dimension"], "label": spec["label"],
                  "params": spec["params"]},
        actions=actions,
        category=seed_def["category"],
    )


class TestCaseSchema(unittest.TestCase):
    """testcase 结构与取值校验。"""

    def test_valid_case_passes(self) -> None:
        case = _sample_case()
        cs.validate_case(case)

        self.assertEqual(case["schema_version"], cs.SCHEMA_VERSION)
        self.assertEqual(case["interface"], "vcan0")

    def test_rejects_bad_schema_version(self) -> None:
        case = _sample_case()
        case["schema_version"] = 99

        with self.assertRaises(cs.SchemaError):
            cs.validate_case(case)

    def test_rejects_invalid_interface(self) -> None:
        for bad in ("can0", "vcan1", "eth0", ""):
            case = _sample_case()
            case["interface"] = bad

            with self.subTest(interface=bad):
                with self.assertRaises(cs.SchemaError):
                    cs.validate_case(case)

    def test_rejects_invalid_can_id(self) -> None:
        case = _sample_case()
        case["frames"][0]["id"] = 0x800  # 超出 11-bit

        with self.assertRaises(cs.SchemaError):
            cs.validate_case(case)

    def test_rejects_invalid_dlc(self) -> None:
        case = _sample_case()
        case["frames"][0]["data"] = bytes(9)  # 超过经典 CAN 上限

        with self.assertRaises(cs.SchemaError):
            cs.validate_case(case)

    def test_rejects_invalid_frame_hex(self) -> None:
        case = _sample_case()
        case["frames"][0]["data"] = "zz zz"

        with self.assertRaises(cs.SchemaError):
            cs.validate_case(case)

    def test_rejects_empty_frames(self) -> None:
        case = _sample_case()
        case["frames"] = []

        with self.assertRaises(cs.SchemaError):
            cs.validate_case(case)

    def test_rejects_missing_mutation_label(self) -> None:
        case = _sample_case()
        case["mutation"] = {"dimension": "sn"}

        with self.assertRaises(cs.SchemaError):
            cs.validate_case(case)

    def test_rejects_unknown_action_type(self) -> None:
        case = _sample_case()
        case["frames"] = [{"type": "teleport"}]

        with self.assertRaises(cs.SchemaError):
            cs.validate_case(case)

    def test_rejects_missing_can_ids_key(self) -> None:
        case = _sample_case()
        case["can_ids"] = {"inject": "0x7E0"}

        with self.assertRaises(cs.SchemaError):
            cs.validate_case(case)


class TestCaseIdStability(unittest.TestCase):
    """case_id 必须只由 (seed, dimension, label) 决定。"""

    def test_case_id_is_deterministic(self) -> None:
        first = cs.make_case_id("SN_SKIP_1_3", "sn", "sn=2")
        second = cs.make_case_id("SN_SKIP_1_3", "sn", "sn=2")

        self.assertEqual(first, second)

    def test_case_id_differs_by_label(self) -> None:
        self.assertNotEqual(
            cs.make_case_id("SN_SKIP_1_3", "sn", "sn=2"),
            cs.make_case_id("SN_SKIP_1_3", "sn", "sn=3"),
        )

    def test_generated_case_ids_unique(self) -> None:
        import isotp_fuzz as engine

        all_cases = engine.generate_cases()
        ids = [c["case_id"] for c in all_cases]

        self.assertEqual(len(ids), len(set(ids)), "生成的 case_id 有重复")
        self.assertTrue(all_cases, "未生成任何 case")


# ----------------------------------------------------------------------
# canonical replay identity
# ----------------------------------------------------------------------


class TestReplayIdentity(unittest.TestCase):
    """identity 只依赖 frames/mutation，不受执行结果影响。"""

    def test_identity_ignores_result(self) -> None:
        case = _sample_case()
        base = cs.canonical_identity(case)

        case["result"] = {
            "class": "DISCARD",
            "timestamp": "2026-01-01T00:00:00",
            "errno": 84,
            "kernel_events": ["something happened"],
        }

        self.assertEqual(base, cs.canonical_identity(case))

    def test_identity_changes_with_frames(self) -> None:
        case = _sample_case()
        base = cs.canonical_identity(case)

        case["frames"][2]["data"] = bytes([0x22, 0x00])

        self.assertNotEqual(base, cs.canonical_identity(case))

    def test_identity_changes_with_mutation(self) -> None:
        case = _sample_case()
        base = cs.canonical_identity(case)

        case["mutation"]["params"] = {"value": 3, "which": "last"}

        self.assertNotEqual(base, cs.canonical_identity(case))

    def test_json_roundtrip_preserves_identity(self) -> None:
        case = _sample_case()
        base = cs.canonical_identity(case)

        blob = json.dumps(cs.to_jsonable(case))
        restored = cs.from_jsonable(json.loads(blob))

        self.assertEqual(base, cs.canonical_identity(restored))

    def test_json_roundtrip_preserves_frames(self) -> None:
        case = _sample_case()

        restored = cs.from_jsonable(json.loads(json.dumps(cs.to_jsonable(case))))

        for original, back in zip(case["frames"], restored["frames"]):
            self.assertEqual(original["type"], back["type"])

            if original["type"] == "inject":
                self.assertEqual(original["id"], back["id"])
                self.assertEqual(original["data"], back["data"])


# ----------------------------------------------------------------------
# mutator 行为
# ----------------------------------------------------------------------


class TestSnMutation(unittest.TestCase):
    """SN mutation 只改 CF 的 PCI 低半字节。"""

    def setUp(self) -> None:
        self.seed_def = corpus.get_seed("SN_SKIP_1_3")

    def test_changes_only_target_pci_nibble(self) -> None:
        """
        只改一帧、只改 PCI 低半字节。

        注意 SN_SKIP_1_3 的末帧本来就是 SN=3，因此这里用 0/1/2/0xF
        （都会真正改变帧序列），no-op 情形由下一个用例单独覆盖。
        """
        for value in (0, 1, 2, 0xF):
            with self.subTest(value=value):
                actions = mut.mutate_sn(
                    [dict(a) for a in self.seed_def["actions"]], value=value
                )

                changed = [
                    i for i, (old, new) in enumerate(
                        zip(self.seed_def["actions"], actions)
                    )
                    if old != new
                ]

                self.assertEqual(len(changed), 1, "只应有一帧被改动")

                idx = changed[0]
                old_data = self.seed_def["actions"][idx]["data"]
                new_data = actions[idx]["data"]

                self.assertEqual(len(old_data), len(new_data), "DLC 不应改变")
                self.assertEqual(old_data[1:], new_data[1:], "payload 不应改变")
                self.assertEqual(old_data[0] & 0xF0, new_data[0] & 0xF0,
                                 "PCI 高半字节不应改变")
                self.assertEqual(new_data[0] & 0x0F, value & 0x0F)

    def test_noop_mutation_is_reported_as_skip(self) -> None:
        """
        把 SN 改成它原本的值 → 不产生新帧序列，应作为 skip 报告。

        这类"无意义 mutation"（P1 §15）不应被当成失败，也不应进入执行队列。
        """
        spec = {"dimension": mut.DIM_SN, "params": {"value": 3, "which": "last"},
                "label": "sn=3"}

        actions, reason = mut.apply_mutation(self.seed_def, spec)

        self.assertIsNone(actions)
        self.assertIn("不改变帧序列", reason)

    def test_does_not_touch_delay_or_id(self) -> None:
        actions = mut.mutate_sn(
            [dict(a) for a in self.seed_def["actions"]], value=2
        )

        for old, new in zip(self.seed_def["actions"], actions):
            self.assertEqual(old["type"], new["type"])

            if old["type"] == "inject":
                self.assertEqual(old["id"], new["id"])
                self.assertEqual(old.get("delay_ms"), new.get("delay_ms"))

    def test_rejects_out_of_range(self) -> None:
        with self.assertRaises(mut.MutationError):
            mut.mutate_sn([dict(a) for a in self.seed_def["actions"]], value=16)

    def test_rejects_seed_without_cf(self) -> None:
        seed_def = corpus.get_seed("BASELINE_SF_1001")

        with self.assertRaises(mut.MutationError):
            mut.mutate_sn([dict(a) for a in seed_def["actions"]], value=2)


class TestPciMutation(unittest.TestCase):
    """PCI mutation 只改高半字节，保持 DLC 合法。"""

    def test_invalid_nibbles_applied(self) -> None:
        seed_def = corpus.get_seed("CF_WITHOUT_FF_SINGLE")
        original = [dict(a) for a in seed_def["actions"]]

        for nibble in (0x4, 0x5, 0x6, 0x7, 0xF):
            with self.subTest(nibble=nibble):
                actions = mut.mutate_pci([dict(a) for a in original], value=nibble)
                new_data = actions[0]["data"]

                self.assertEqual(new_data[0] & 0xF0, nibble << 4)
                self.assertEqual(new_data[0] & 0x0F, original[0]["data"][0] & 0x0F)
                self.assertLessEqual(len(new_data), corpus.CAN_DATA_MAX_LEN)

    def test_all_invalid_nibbles_are_not_basic_frames(self) -> None:
        seed_def = corpus.get_seed("CF_WITHOUT_FF_SINGLE")

        for nibble in corpus.INVALID_PCI_NIBBLES:
            actions = mut.mutate_pci(
                [dict(a) for a in seed_def["actions"]], value=nibble
            )

            with self.subTest(nibble=nibble):
                self.assertTrue(corpus.is_invalid_pci(actions[0]["data"][0]))


class TestSfLengthMutation(unittest.TestCase):
    """SF 长度 mutation 覆盖 0..7 且不改 payload。"""

    def test_declared_lengths(self) -> None:
        seed_def = corpus.get_seed("SF_DECLARES_MORE_THAN_PROVIDED")
        original = [dict(a) for a in seed_def["actions"]]

        for declared in range(8):
            with self.subTest(declared=declared):
                actions = mut.mutate_sf_length(
                    [dict(a) for a in original], declared=declared
                )

                self.assertEqual(actions[0]["data"][0] & 0x0F, declared)
                self.assertEqual(actions[0]["data"][1:], original[0]["data"][1:])

    def test_rejects_out_of_range(self) -> None:
        seed_def = corpus.get_seed("SF_DECLARES_MORE_THAN_PROVIDED")

        with self.assertRaises(mut.MutationError):
            mut.mutate_sf_length([dict(a) for a in seed_def["actions"]], declared=16)


class TestFfLengthMutation(unittest.TestCase):
    """FF 长度 mutation 覆盖声明<实际/相等/大于/边界。"""

    def test_declared_lengths_encode_two_bytes(self) -> None:
        seed_def = corpus.get_seed("FF_INCOMPLETE_NO_CF")
        original = [dict(a) for a in seed_def["actions"]]

        for declared in (0, 1, 3, 8, 0xFFF):
            with self.subTest(declared=declared):
                actions = mut.mutate_ff_length(
                    [dict(a) for a in original], declared=declared
                )
                data = actions[0]["data"]
                encoded = ((data[0] & 0x0F) << 8) | data[1]

                self.assertEqual(encoded, declared)
                self.assertEqual(data[2:], original[0]["data"][2:], "数据不应改变")

    def test_rejects_out_of_range(self) -> None:
        seed_def = corpus.get_seed("FF_INCOMPLETE_NO_CF")

        with self.assertRaises(mut.MutationError):
            mut.mutate_ff_length([dict(a) for a in seed_def["actions"]],
                                 declared=0x1000)


class TestFcStatusMutation(unittest.TestCase):
    """FC 状态 mutation，含保留值单独分类。"""

    def setUp(self) -> None:
        self.seed_def = corpus.get_seed("FC_UNSOLICITED_CTS")

    def test_statuses(self) -> None:
        original = [dict(a) for a in self.seed_def["actions"]]

        for status in (0, 1, 2, 3, 0xF):
            with self.subTest(status=status):
                actions = mut.mutate_fc_status(
                    [dict(a) for a in original], status=status
                )
                data = actions[0]["data"]

                self.assertEqual(data[0] & 0x0F, status)
                self.assertEqual(data[1:], original[0]["data"][1:])

    def test_reserved_status_classified_separately(self) -> None:
        """保留 FS 与 OVERFLOW 必须能区分（P1 §14）。"""
        specs = mut.mutation_specs_for_seed(self.seed_def)
        hints = {
            s["label"]: s.get("class_hint")
            for s in specs if s["dimension"] == mut.DIM_FC_STATUS
        }

        self.assertIsNone(hints.get("fs=2"), "OVERFLOW 不应被标成保留")
        self.assertEqual(hints.get("fs_reserved=3"), "FC_RESERVED_STATUS")
        self.assertEqual(hints.get("fs_reserved=15"), "FC_RESERVED_STATUS")

    def test_reserved_status_constant(self) -> None:
        self.assertIn(0x3, corpus.FC_RESERVED_STATUS)
        self.assertIn(0xF, corpus.FC_RESERVED_STATUS)
        self.assertNotIn(0x2, corpus.FC_RESERVED_STATUS)


class TestBsAndStminMutation(unittest.TestCase):
    """
    BS / STmin mutation。

    注意：corpus 里 FC 有两种表示——显式 ``inject 30 xx xx`` 与
    ``await_fc``（语义上就是"等 FF 后回 CTS"）。因此这两个 mutator
    必须能同时处理两者，且**只改 FC 字段**。
    """

    def setUp(self) -> None:
        # 其中 index 1 是 await_fc（= CTS），index 2 是显式 FC 帧
        self.seed_def = corpus.get_seed("FC_BS_1")

    def test_bs_changes_only_fc_field(self) -> None:
        """默认 target=last → 命中 index 2（显式 FC 帧），只改 BS 字节。"""
        original = [dict(a) for a in self.seed_def["actions"]]

        for bs in (0, 1, 2, 255):
            with self.subTest(bs=bs):
                actions = mut.mutate_bs([dict(a) for a in original], value=bs)

                self.assertEqual(actions[2]["data"], bytes([0x30, bs, 0x00]))

                # 其它动作完全不变（含 index 1 的 await_fc）
                for i in (0, 1, 3):
                    self.assertEqual(actions[i], original[i])

    def test_bs_can_target_await_fc(self) -> None:
        """显式 target=first → 命中 index 1（await_fc），改其 block_size。"""
        original = [dict(a) for a in self.seed_def["actions"]]

        for bs in (0, 2, 255):
            with self.subTest(bs=bs):
                actions = mut.mutate_bs(
                    [dict(a) for a in original], value=bs, which="first"
                )
                target = actions[1]

                self.assertEqual(target["type"], "await_fc")
                self.assertEqual(target["block_size"], bs)
                self.assertEqual(target["stmin"], original[1]["stmin"])
                self.assertEqual(target["timeout_ms"], original[1]["timeout_ms"])

                for i in (0, 2, 3):
                    self.assertEqual(actions[i], original[i])

    def test_remaining_cf_count_computation(self) -> None:
        """
        剩余 CF 数 = ceil((声明总长 - FF 数据字节) / 7)。

        22 字节 payload：FF 带 6 字节，剩 16 → 3 个 CF。
        """
        self.assertEqual(
            mut.remaining_cf_count(corpus.get_seed("BASELINE_MF_22B")["actions"]), 3
        )
        # FF 声明 3 字节（已在 FF 内交付）→ 0 个 CF
        self.assertEqual(
            mut.remaining_cf_count(
                corpus.get_seed("FF_DECLARES_SMALL_EXTRA_CF")["actions"]
            ),
            0,
        )
        # 声明 16 字节：FF 6 字节 + 剩 10 → 2 个 CF
        self.assertEqual(
            mut.remaining_cf_count(
                corpus.get_seed("FF_DECLARES_LARGE_SHORT_CF")["actions"]
            ),
            2,
        )
        # 没有 FF 的 seed → 无法判定
        self.assertIsNone(
            mut.remaining_cf_count(corpus.get_seed("BASELINE_SF_1001")["actions"])
        )

    def test_bs_boundary_spec_present_when_ff_exists(self) -> None:
        """含 FF 的 seed 必须产生一个"恰好等于剩余 CF 数"的 BS 边界变异。"""
        specs = mut.mutation_specs_for_seed(self.seed_def)
        boundaries = [s for s in specs if s.get("boundary") == "remaining_cf_count"]

        self.assertEqual(len(boundaries), 1, "应恰好有一个 remaining 边界变异")

        remaining = mut.remaining_cf_count(self.seed_def["actions"])
        self.assertEqual(boundaries[0]["params"]["value"], remaining)
        self.assertIn("remaining", boundaries[0]["label"])

    def test_bs_boundary_reaches_case_id(self) -> None:
        """边界标记必须透传到 case，否则结果里无法识别该边界。"""
        import isotp_fuzz as engine

        cases = engine.generate_cases(dimensions=[mut.DIM_BS])
        marked = [c for c in cases
                  if c["mutation"].get("boundary") == "remaining_cf_count"]

        self.assertTrue(marked, "没有任何 remaining 边界 case 被生成")
        self.assertTrue(all("remaining" in c["case_id"] for c in marked))

    def test_no_bs_spec_without_ff(self) -> None:
        """没有 FF 的 seed 不产生 BS 变异（P1 §15：不造无意义 mutation）。"""
        seed_def = corpus.get_seed("FF_INCOMPLETE_NO_CF")  # 有 FF 但无 FC
        specs = mut.mutation_specs_for_seed(seed_def)
        self.assertEqual([s for s in specs if s["dimension"] == mut.DIM_BS], [])

        # 反过来：有 FC 且有 FF 的 seed 一定有 BS
        specs = mut.mutation_specs_for_seed(self.seed_def)
        self.assertTrue([s for s in specs if s["dimension"] == mut.DIM_BS])

    def test_stmin_changes_only_fc_field(self) -> None:
        original = [dict(a) for a in self.seed_def["actions"]]

        for stmin in (0x00, 0x7F, 0xF1, 0xF9, 0x80, 0xF0, 0xFA, 0xFF):
            with self.subTest(stmin=stmin):
                actions = mut.mutate_stmin([dict(a) for a in original], value=stmin)

                self.assertEqual(actions[2]["data"], bytes([0x30, 0x01, stmin]))

                for i in (0, 1, 3):
                    self.assertEqual(actions[i], original[i])

    def test_stmin_can_target_await_fc(self) -> None:
        original = [dict(a) for a in self.seed_def["actions"]]

        actions = mut.mutate_stmin(
            [dict(a) for a in original], value=0x80, which="first"
        )

        self.assertEqual(actions[1]["stmin"], 0x80)
        self.assertEqual(actions[1]["block_size"], original[1]["block_size"])
        self.assertEqual(actions[2], original[2])

    def test_explicit_fc_frame_also_mutable(self) -> None:
        """显式 ``inject 30 xx xx`` 形式的 FC 同样可改，且只改对应字节。"""
        seed_def = corpus.get_seed("FC_UNSOLICITED_CTS")
        original = [dict(a) for a in seed_def["actions"]]

        actions = mut.mutate_bs([dict(a) for a in original], value=2)
        self.assertEqual(actions[0]["data"], bytes([0x30, 0x02, 0x00]))

        actions = mut.mutate_stmin([dict(a) for a in original], value=0xF1)
        self.assertEqual(actions[0]["data"], bytes([0x30, 0x00, 0xF1]))

        actions = mut.mutate_fc_status([dict(a) for a in original], status=1)
        self.assertEqual(actions[0]["data"], bytes([0x31, 0x00, 0x00]))

    def test_await_fc_rejects_non_cts_status(self) -> None:
        """await_fc 语义固定为 CTS，改 FS 应报错（WAIT/OVERFLOW 要显式帧）。"""
        seed_def = corpus.get_seed("FC_BS_1")

        with self.assertRaises(mut.MutationError):
            mut.mutate_fc_status([dict(a) for a in seed_def["actions"]],
                                 status=corpus.FC_WAIT, which="first")

    def test_stmin_classification_follows_iso_encoding(self) -> None:
        """0xF1–0xF9 是合法微秒档，不能被归为 reserved。"""
        self.assertEqual(corpus.classify_stmin(0x00), "legal_ms")
        self.assertEqual(corpus.classify_stmin(0x7F), "legal_ms")
        self.assertEqual(corpus.classify_stmin(0xF1), "legal_us")
        self.assertEqual(corpus.classify_stmin(0xF9), "legal_us")
        self.assertEqual(corpus.classify_stmin(0x80), "reserved")
        self.assertEqual(corpus.classify_stmin(0xF0), "reserved")
        self.assertEqual(corpus.classify_stmin(0xFA), "reserved")
        self.assertEqual(corpus.classify_stmin(0xFF), "reserved")

    def test_stmin_specs_carry_class(self) -> None:
        specs = mut.mutation_specs_for_seed(self.seed_def)
        by_label = {
            s["label"]: s.get("stmin_class")
            for s in specs if s["dimension"] == mut.DIM_STMIN
        }

        self.assertEqual(by_label.get("stmin=0xF1"), "legal_us")
        self.assertEqual(by_label.get("stmin=0x80"), "reserved")


class TestPaddingMutation(unittest.TestCase):
    """padding/DLC mutation：payload 不变，只改 DLC 与尾部填充。"""

    def test_pad_to_dlc_8(self) -> None:
        """
        填充到 DLC=8。

        注意：PAD_SF_ZERO_PADDING 的种子帧本身已经是 8 字节，
        因此这里用一个更短的帧来验证"确实补了填充字节"。
        """
        seed_def = corpus.get_seed("PAD_SF_ZERO_PADDING")
        original = [dict(a) for a in seed_def["actions"]]
        self.assertEqual(len(original[0]["data"]), 8, "种子本身应是 DLC=8")

        # 先截断到 3 字节，再补回 8 字节——验证 padding 语义
        short = mut.mutate_padding([dict(a) for a in original], pad_byte=0x00, dlc=3)
        self.assertEqual(len(short[0]["data"]), 3)

        padded = mut.mutate_padding(short, pad_byte=0xAA, dlc=8)
        data = padded[0]["data"]

        self.assertEqual(len(data), 8)
        self.assertEqual(data[:3], original[0]["data"][:3], "payload 前缀不应改变")
        self.assertEqual(data[3:], bytes([0xAA] * 5), "尾部应为填充字节")

    def test_short_dlc_truncates(self) -> None:
        seed_def = corpus.get_seed("PAD_SF_ZERO_PADDING")
        original = [dict(a) for a in seed_def["actions"]]

        actions = mut.mutate_padding(
            [dict(a) for a in original], pad_byte=0x00, dlc=2
        )

        self.assertEqual(len(actions[0]["data"]), 2)

    def test_rejects_dlc_over_8(self) -> None:
        seed_def = corpus.get_seed("PAD_SF_ZERO_PADDING")

        with self.assertRaises(mut.MutationError):
            mut.mutate_padding([dict(a) for a in seed_def["actions"]], dlc=9)

    def test_dlc_targets_within_classic_can(self) -> None:
        for dlc in mut.DLC_TARGETS:
            with self.subTest(dlc=dlc):
                self.assertLessEqual(dlc, corpus.CAN_DATA_MAX_LEN)


# ----------------------------------------------------------------------
# 确定性 / 生成
# ----------------------------------------------------------------------


class TestDeterminism(unittest.TestCase):
    """same seed + mutation = same frames（同参数必得同结果）。"""

    def test_generate_cases_is_deterministic(self) -> None:
        import isotp_fuzz as engine

        first = engine.generate_cases()
        second = engine.generate_cases()

        self.assertEqual(len(first), len(second))

        for a, b in zip(first, second):
            self.assertEqual(a["case_id"], b["case_id"])
            self.assertEqual(cs.canonical_identity(a), cs.canonical_identity(b))

    def test_mutation_is_pure(self) -> None:
        """同一输入重复施加 mutator，结果一致且不修改入参。"""
        seed_def = corpus.get_seed("SN_SKIP_1_3")
        before = [dict(a) for a in seed_def["actions"]]
        snapshot = [dict(a) for a in before]

        spec = {"dimension": mut.DIM_SN, "params": {"value": 7, "which": "last"},
                "label": "sn=7"}

        a1, _ = mut.apply_mutation(seed_def, spec)
        a2, _ = mut.apply_mutation(seed_def, spec)

        self.assertEqual(mut.frames_fingerprint(a1), mut.frames_fingerprint(a2))
        self.assertEqual(before, snapshot, "mutator 不应修改输入 actions")

    def test_skip_reason_for_inapplicable_mutation(self) -> None:
        seed_def = corpus.get_seed("BASELINE_SF_1001")
        spec = {"dimension": mut.DIM_SN, "params": {"value": 2}, "label": "sn=2"}

        actions, reason = mut.apply_mutation(seed_def, spec)

        self.assertIsNone(actions)
        self.assertTrue(reason, "不适用的 mutation 必须给出原因")


class TestSmokeSelection(unittest.TestCase):
    """smoke 集合必须 ≤30 且覆盖各类别。"""

    def setUp(self) -> None:
        import isotp_fuzz as engine

        self.cases = engine.build_smoke_cases()

    def test_within_budget(self) -> None:
        self.assertLessEqual(len(self.cases), 30)
        self.assertGreater(len(self.cases), 0)

    def test_covers_required_categories(self) -> None:
        categories = {c["category"] for c in self.cases}

        for required in ("baseline", "pci", "single_frame", "first_frame",
                         "sequence_number", "flow_control", "padding"):
            with self.subTest(category=required):
                self.assertIn(required, categories)

    def test_all_cases_valid(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["case_id"]):
                cs.validate_case(case)


class TestOracleClassification(unittest.TestCase):
    """分类优先级与 STATE_CORRUPTION 语义。"""

    def test_priority_order(self) -> None:
        # 进程死亡优先于一切（除 kernel anomaly）
        self.assertEqual(
            cs.classify_result(False, "uds", cs.RECOVERY_FAIL, 84, False, False),
            cs.ECU_CRASH,
        )
        # kernel anomaly 优先级最高
        self.assertEqual(
            cs.classify_result(False, "none", cs.RECOVERY_FAIL, None, True, False),
            cs.KERNEL_ANOMALY,
        )

    def test_state_corruption_not_masked_by_alive(self) -> None:
        """进程存活 + 恢复失败 → STATE_CORRUPTION（P1 §22）。"""
        self.assertEqual(
            cs.classify_result(True, "none", cs.RECOVERY_FAIL, None, False, False),
            cs.STATE_CORRUPTION,
        )

    def test_transport_error_when_errno_present(self) -> None:
        self.assertEqual(
            cs.classify_result(True, "none", cs.RECOVERY_PASS, 84, False, False),
            cs.TRANSPORT_ERROR,
        )

    def test_discard_and_uds_response(self) -> None:
        self.assertEqual(
            cs.classify_result(True, "none", cs.RECOVERY_PASS, None, False, False),
            cs.DISCARD,
        )
        self.assertEqual(
            cs.classify_result(True, "uds", cs.RECOVERY_PASS, None, False, False),
            cs.UDS_RESPONSE,
        )

    def test_hang_after_state_corruption(self) -> None:
        self.assertEqual(
            cs.classify_result(True, "none", cs.RECOVERY_NOT_RUN, None, False, True),
            cs.HANG,
        )

    def test_new_errno_detection(self) -> None:
        self.assertFalse(cs.is_new_errno(84))
        self.assertFalse(cs.is_new_errno(70))
        self.assertFalse(cs.is_new_errno(90))
        self.assertTrue(cs.is_new_errno(88))
        self.assertTrue(cs.is_new_errno(74))
        self.assertFalse(cs.is_new_errno(None))

    def test_contained_errnos_match_p3r2(self) -> None:
        self.assertEqual(set(cs.CONTAINED_ERRNOS), {84, 70, 90})


class TestKernelOracleHelpers(unittest.TestCase):
    """dmesg 差分与过滤（不需要真实 dmesg）。"""

    def test_delta_finds_new_lines(self) -> None:
        import isotp_fuzz as engine

        before = ["a", "b", "c"]
        after = ["a", "b", "c", "d"]

        self.assertEqual(engine.dmesg_delta(before, after), ["d"])

    def test_filter_matches_anomaly_patterns(self) -> None:
        import isotp_fuzz as engine

        lines = [
            "random noise",
            "can: isotp: something happened",
            "BUG: unable to handle kernel NULL pointer dereference",
        ]
        hits = engine.filter_kernel_anomalies(lines)

        self.assertEqual(len(hits), 2)

    def test_filter_ignores_broad_can_matches(self) -> None:
        """P1 §23：不能用宽泛的 "can"，否则会命中大量无关噪音。"""
        import isotp_fuzz as engine

        lines = ["can you believe this", "scanning done", "American"]
        self.assertEqual(engine.filter_kernel_anomalies(lines), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
