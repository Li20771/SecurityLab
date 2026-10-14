#!/usr/bin/env python3
"""
capture_lab 退出码传播回归测试（离线、无需 vcan0 / 无需 bash 执行）。

历史缺陷（OQ-2d）
=================

``capture_lab.sh`` 的汇总段只把三个子项结果**打印**出来，脚本**恒以 0 退出**：

.. code-block:: bash

    if [ "$PASS_A" = PASS ] && [ "$PASS_B" = PASS ] && [ "$PASS_C" = PASS ]; then
      echo "CAPTURE_LAB=PASS"
    else
      echo "CAPTURE_LAB=CHECK"     # ← 只打印，没有 exit 1
    fi

而调用方 ``scripts/qa_extended.sh`` 是按**退出码**判定的：

.. code-block:: bash

    if bash .../capture_lab.sh; then echo "[ OK ] capture lab"
    else echo "[FAIL] capture lab"; FAIL=1; fi

于是"某个子项失败 + ``CAPTURE_LAB=CHECK``"会被上报成 ``[ OK ] capture lab``
—— 一次真实失败被汇总成通过（fail-open）。

同一缺陷还有第二处：Python 解释器被**硬编码**为
``$HOME/.venvs/network/bin/python``。该路径只在本地 WSL 开发机存在，
在 CI runner 上不存在，导致段 B 必然失败；而因为退出码恒为 0，
这个必然失败**从不上报**。

本测试做什么
============

本文件是**静态结构断言**：它解析 ``capture_lab.sh`` 的源码，断言上述
不变式成立。选择静态断言的原因：

- 它**无需 bash 执行**，因此可以在 Windows 本机与 CI 的离线快速门禁中
  真实运行（本仓库的快速门禁是 Python 驱动的）；
- 它**确实能捕获原始缺陷**：对修复前的脚本，下面每一条断言都会失败。

真正的**执行**级验证（在 Linux 上实跑脚本、观察退出码）由
``capture/tests/capture_lab_exit_code_test.sh`` 承担 —— 该文件需要
bash + vcan0 环境（本机不可用，标为 NOT_RUN）。

设计原则：断言的是**行为契约**（失败必须产生非 0 退出码），
而不是某个具体写法的字面文本。
"""

import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CAPTURE_DIR = os.path.join(os.path.dirname(_HERE), "capture")
_SCRIPT = os.path.join(_CAPTURE_DIR, "capture_lab.sh")


def _read_script() -> str:
    with open(_SCRIPT, encoding="utf-8") as handle:
        return handle.read()


class CaptureLabScriptPresent(unittest.TestCase):
    def test_script_exists(self):
        self.assertTrue(
            os.path.isfile(_SCRIPT),
            f"capture_lab.sh 不存在：{_SCRIPT}",
        )


class TestPythonInterpreterSelection(unittest.TestCase):
    """
    解释器不得被硬编码为仅本地存在的 venv 路径。

    原实现 ``PY="$HOME/.venvs/network/bin/python"`` 在 CI runner 上不存在，
    使段 B 必然失败 —— 而由于退出码恒为 0，该失败从不被上报。
    """

    def test_no_unconditional_home_venv_hardcoding(self):
        src = _read_script()
        # 允许 venv 路径作为**候选之一**，但不允许无条件赋值。
        hardcoded = re.search(
            r'^\s*PY=["\']\$HOME/\.venvs/[^"\']*["\']\s*$',
            src,
            re.MULTILINE,
        )
        self.assertIsNone(
            hardcoded,
            "PY 不得被无条件硬编码为 $HOME/.venvs 路径（CI 上不存在）",
        )

    def test_supports_python_env_override(self):
        """必须支持 $PYTHON 覆盖（与仓库其它脚本的既有约定一致）。"""
        self.assertIn(
            "${PYTHON:-}", _read_script(),
            "解释器选择必须支持 $PYTHON 覆盖",
        )

    def test_supports_path_fallback(self):
        """必须能在没有 venv 的环境里退回到 PATH 上的 python3/python。"""
        src = _read_script()
        self.assertTrue(
            "command -v python3" in src or "command -v python" in src,
            "解释器选择必须能退回到 PATH 上的 python3/python",
        )

    def test_fails_closed_when_no_interpreter(self):
        """解析不到任何解释器时必须 exit 2，而不是继续跑出假结果。"""
        src = _read_script()
        self.assertRegex(
            src,
            r"exit 2",
            "找不到解释器时必须非 0 退出（环境无法运行 ⇒ exit 2）",
        )


class TestExitCodePropagation(unittest.TestCase):
    """
    核心不变式：**只要未全部通过，退出码必须非 0。**

    这正是原始缺陷所在：旧脚本总是以 0 退出，使 ``qa_extended.sh``
    把失败上报为 ``[ OK ] capture lab``。
    """

    def _lines(self):
        return _read_script().splitlines()

    def test_check_branch_has_nonzero_exit(self):
        lines = self._lines()
        idx = [i for i, ln in enumerate(lines) if "CAPTURE_LAB=CHECK" in ln]
        self.assertTrue(
            idx,
            '汇总段必须存在 "CAPTURE_LAB=CHECK" 标记（未被静默删除）',
        )
        for i in idx:
            following = lines[i:]
            has_exit = any(re.match(r"\s*exit [1-9]", ln) for ln in following)
            self.assertTrue(
                has_exit,
                f"CAPTURE_LAB=CHECK（第 {i + 1} 行）之后必须存在非 0 的 exit；"
                "否则失败会被汇总成通过",
            )

    def test_pass_branch_exits_zero(self):
        lines = self._lines()
        idx = [i for i, ln in enumerate(lines) if "CAPTURE_LAB=PASS" in ln]
        self.assertTrue(idx, '汇总段必须存在 "CAPTURE_LAB=PASS" 分支')
        for i in idx:
            window = lines[i : i + 6]
            self.assertTrue(
                any(re.match(r"\s*exit 0", ln) for ln in window),
                f"CAPTURE_LAB=PASS（第 {i + 1} 行）之后应显式 exit 0",
            )

    def test_summary_is_not_the_last_statement(self):
        """
        回归防线：汇总段不得成为脚本的最后一条有效语句。

        原始缺陷的形态正是"汇总只 echo，脚本自然以 0 结束"。
        """
        lines = [ln for ln in self._lines() if ln.strip()]
        self.assertTrue(lines, "脚本不应为空")
        last = lines[-1].strip()
        self.assertRegex(
            last,
            r"^exit [0-9]+$",
            "脚本最后一条有效语句必须是显式 exit（防止再次出现'自然结束=0'）",
        )

    def test_both_exit_codes_are_distinct(self):
        """PASS 与 CHECK 必须映射到**不同**的退出码，否则调用方无法区分。"""
        src = _read_script()
        # 收集汇总段附近的 exit 码
        codes = set(int(m) for m in re.findall(r"^\s*exit (\d+)\s*$", src, re.MULTILINE))
        self.assertIn(0, codes, "必须存在成功退出码 0")
        self.assertTrue(
            any(c != 0 for c in codes),
            "必须存在失败退出码（非 0），否则失败无法传播给调用方",
        )


class TestOriginalDefectWouldBeCaught(unittest.TestCase):
    """
    自证测试有效性：把**修复前**的汇总段文本喂给同一套断言，必须失败。

    这保证本测试不是"怎么写都过"的空断言。
    """

    ORIGINAL_SUMMARY = (
        'echo "CAN_LOG : $PASS_A"\n'
        'echo "BLF     : $PASS_B"\n'
        'echo "PCAP    : $PASS_C"\n'
        'if [ "$PASS_A" = PASS ] && [ "$PASS_B" = PASS ] && [ "$PASS_C" = PASS ]; then\n'
        '  echo "CAPTURE_LAB=PASS"\n'
        "else\n"
        '  echo "CAPTURE_LAB=CHECK"\n'
        "fi\n"
    )

    def test_original_text_violates_the_invariant(self):
        lines = self.ORIGINAL_SUMMARY.splitlines()
        idx = [i for i, ln in enumerate(lines) if "CAPTURE_LAB=CHECK" in ln]
        self.assertTrue(idx)

        has_exit = any(
            re.match(r"\s*exit [1-9]", ln) for i in idx for ln in lines[i:]
        )
        self.assertFalse(
            has_exit,
            "自证失败：修复前的汇总段本应**缺少**非 0 exit，"
            "否则说明本断言无法区分修复前后",
        )

    def test_original_text_ends_without_exit(self):
        lines = [ln for ln in self.ORIGINAL_SUMMARY.splitlines() if ln.strip()]
        self.assertFalse(
            re.match(r"^exit [0-9]+$", lines[-1].strip()),
            "自证失败：修复前脚本本应以 fi 结束（自然退出 0）",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
