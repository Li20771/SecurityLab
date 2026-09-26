#!/usr/bin/env python3
"""
C2-A2 —— **Legacy ↔ Current 差分对拍**基础设施

设计出处：``docs/OQ_C2_GATEWAY_POLICY_DESIGN_REVIEW_2026-09-25.md`` §13
（C2-A2 = "legacy ↔ policy 逐字节对拍（空策略）"）。

## 本模块提供的核心主张

**同一个 case，两条实现路径各自独立执行一次，再交给差分比较器**：

```
              same case (can_id + payload + 完整初始 ECU 配置)
                     │
        ┌────────────┴────────────┐
        ▼                         ▼
  Legacy 冻结实现            Current 生产实现
  （pre-policy gateway）     （gateway.py @ HEAD，空策略）
        │                         │
   response + state          response + state (+ trace)
        └────────────┬────────────┘
                     ▼
            DifferentialResult
              ├── response_diff
              ├── state_diff        （逐 ECU → field → legacy/current 值）
              └── metadata          （case_id / can_id / payload / 执行计数）
                     ▼
              pass/fail gate（mismatches == 0）
```

**这不是 "Current ↔ 冻结 JSON"**（那是 C2-A1 的验收门），也不是把两条路径
合并到同一个 gateway 对象后比较 —— 两条路径**各自实例化、各自执行**。

## 两条路径的独立性边界（准确表述）

```
独立：
    gateway.py 实现本体 —— legacy 副本（pre-policy）vs current（含 policy）。

受控共享依赖：
    uds_server.py / uds_types.py / service_spec.py / did_database.py / … 等
    协议层与 ECU 基础设施 —— **同一个模块对象**被两条路径共同调用。

为什么这种共享不会把 A2 退化成 "Current 自测"：

  * 共享依赖在 ``4059cf5``（A0 捕获 commit）与 HEAD 之间**逐字节相同**
    （见 :data:`CONTROLLED_SHARED_DEPENDENCIES` 的 blob SHA 钉住）；
  * 因此两条路径的差异**只可能来自 gateway.py 本身** —— 这正是 C2 改动的
    全部范围；
  * 反过来说，若将来有阶段改动协议层，pin 会 fail-closed，A2 的有效性
    前提**不会被静默破坏**。
```

## Legacy 实现从哪来

**不是**新写一份"看起来像 legacy"的代码，而是把 ``gateway.py@4059cf5`` 的
git blob **逐字节冻结**进 ``tests/legacy/``，并用两层哈希钉住：

* :data:`LEGACY_SOURCE_BLOB_SHA` —— git blob SHA（可由内容独立重算，无需 git）
* :data:`LEGACY_SOURCE_LF_SHA256` —— LF 规范化内容 SHA-256（平台无关）

git 可用时另做一次与 ``git show 4059cf5:<path>`` 的**交叉校验**
（:func:`git_cross_check`）；git 不可用时 A2 仍必须可执行（跳过而非失败）。

安全边界：本模块只做本地离线差分，不接触真实 CAN / ECU / 车辆。
"""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_LAB_DIR = os.path.dirname(_HERE)
_ECU_DIR = os.path.join(_LAB_DIR, "ecu")
if _ECU_DIR not in sys.path:
    sys.path.insert(0, _ECU_DIR)

from uds_server import EcuState  # noqa: E402

# ======================================================================
# 1. Provenance —— legacy 实现的来源与冻结哈希
# ======================================================================

#: A0 冻结 oracle 的捕获 commit —— legacy 行为的**来源状态**。
LEGACY_SOURCE_COMMIT = "4059cf5d3b10c6b94b571e1bf44633941c9b3dea"

#: 上述 commit 的短 SHA（用于 git 交叉校验与报告）。
LEGACY_SOURCE_COMMIT_SHORT = "4059cf5"

#: 被冻结的生产文件（仓库相对路径，POSIX 分隔符 —— git 需要）。
LEGACY_SOURCE_RELPATH = "labs/vehicle/uds-basic/ecu/gateway.py"

#: 冻结副本位置（本目录下 ``legacy/``）。
LEGACY_IMPL_PATH = os.path.join(_HERE, "legacy", "c2_a0_gateway_legacy_impl.py")

#: 冻结副本的模块名 —— 刻意与 ``gateway`` 不同，避免遮蔽 current 实现。
LEGACY_MODULE_NAME = "c2_a0_gateway_legacy_impl"

#: git blob SHA（= ``git rev-parse 4059cf5:labs/vehicle/uds-basic/ecu/gateway.py``）。
LEGACY_SOURCE_BLOB_SHA = "e64cd088fc86bf9e6b1c5dfb2bb1b80cae5ab39a"

#: LF 规范化内容的 SHA-256（平台无关；Windows 工作区可能是 CRLF）。
LEGACY_SOURCE_LF_SHA256 = "754a44de007eb5bda9906166bd16b9da343278f3a2d6fbb705a7148fb423b054"

#: legacy 冻结副本中**必须不存在**的 current-only policy 符号。
#: 与 A0 捕获脚本的 fail-closed 护栏使用**同一组** marker（口径一致）。
CURRENT_ONLY_POLICY_SYMBOLS = (
    "PolicyTrace",
    "route_with_trace",
    "policy_evaluate",
    "GatewayPolicy",
)

#: current 实现中**必须存在**的 policy 路径证据（否则两条路径并非"不同实现"）。
CURRENT_POLICY_REQUIRED_SYMBOLS = (
    "DEFAULT_POLICY",
    "route_with_trace",
    "_route_core",
)

#: 受控共享依赖 —— 协议层 / ECU 基础设施在 ``4059cf5`` 时的 git blob SHA。
#: 这些模块被 Legacy 与 Current **共同调用**。钉住它们使"两条路径只差
#: gateway.py"成为**可验证事实**而非假设：任何一项漂移都会 fail-closed。
#: （``gateway.py`` 本体**不在此表** —— 它是被差分的主体。）
#:
#: .. important::
#:    **C3-A1-X1 Shared Dependency Rebaseline（C3 授权，2026-09-26）**
#:
#:    本表的 **3 个条目**已按 C3-A1 的**冻结设计**做过一次受控 rebaseline：
#:
#:         ecu.py             710d6515… → 0817e17b…
#:         gateway_runtime.py 85604271… → 0c12cb7b…
#:         uds_server.py      72342bc1… → 94c74e12…
#:
#:    原 pin 指向 ``4059cf5``（C2 捕获）时代的 blob；C3-A1 有意修改了这三个模块
#:    （新增 ``security_access_services`` / 委托绑定 / 入口层 provider wiring）。
#:    fail-closed **正确触发**后，按 A2 文档的处置纪律
#:    （"不得静默更新 pin —— 必须重新评估并记录理由"）完成重新评估与记录。
#:
#:    **这不是放宽检查**：本表语义（逐模块字节同一性、任何漂移即失败）**未变**，
#:    fail-closed 机制**未删除**。新增的 C3 载荷**未**被任何 pin 覆盖为"免检"。
#:
#:    影响评估（详见 ``test_c2_a2_legacy_current_differential.py`` 与
#:    ``docs/OQ_C2_A2_LEGACY_CURRENT_DIFFERENTIAL_2026-09-25.md`` 的 Rebaseline Addendum）：
#:
#:    * ``uds_server.py``       **纯增量**（+25 / 0 删除）
#:    * ``ecu.py``              仅改为委托；``_now()`` 仍按 **ecu 命名空间**解析、
#:                              ``SECURITY_CONFIG`` 仍是**原对象引用**
#:    * ``gateway_runtime.py``  入口层纯增量；C2-A2 的 20 条差分执行**不经过**它
#:
#:    其余 9 个条目仍指向 ``4059cf5`` 时代的 blob，**未变**。
CONTROLLED_SHARED_DEPENDENCIES = {
    "did_database.py": "84150295c82134f54b62a09b2f694f1f61f65644",
    "dtc_database.py": "4d80ef2418b007a7fded022d183d4b69c1ecd914",
    "dtc_runtime.py": "cbc63e7e3daf71409cc91e66c18e96b193f17145",
    # C3-A1 rebaseline（原 710d6515e1e50204c398f82e3368559d87b4c806）
    "ecu.py": "0817e17be858ce7eec132733418b72199de10e50",
    "file_database.py": "b0df31992180b4764bbc0d2d1af8c2576679a75d",
    # C3-A1 rebaseline（原 85604271db0640c732bb32cffd3f36047ebbcece）
    "gateway_runtime.py": "0c12cb7bc06771daa72732d5422c94c05cb3fe79",
    "keygen.py": "29162aa930ae84033450aba1dc4505a50e512370",
    "routine_database.py": "addd49e662f3215280a6cf552e31dbf7e5917346",
    "service_spec.py": "128f8e5c287547213d6c345f80b3371ceec7930e",
    "transfer_database.py": "b255083535896f9cb18063df871b5686c211a220",
    # C3-A1 rebaseline（原 72342bc1a8decc44fdb857071e54cd87a31ec101）
    "uds_server.py": "94c74e126d4114587f8a7888845ef1111990074f",
    "uds_types.py": "2d07d66b709feea2ae51d1fef7a3f0aff5f0da19",
}

#: current 生产实现（被差分的主体之一）。
CURRENT_IMPL_PATH = os.path.join(_ECU_DIR, "gateway.py")

#: 冻结输入集（20 条 case）—— A2 **只**审计这 20 条既有输入。
GOLDEN_PATH = os.path.join(_HERE, "golden", "c2_gateway_legacy.json")

#: A0/A1 的状态投影口径（三字段 + 逐 ECU）。
STATE_FIELDS = ("session", "security_level", "did_overrides")

#: 自定义 handler 的具名种类（与 A0 corpus 的 ``handler`` 字段同构）。
HANDLER_DEFAULT = "default"
HANDLER_ECHO_77 = "echo_77"

#: 实现标识。
IMPL_LEGACY = "legacy"
IMPL_CURRENT = "current"


# ======================================================================
# 2. 哈希工具（不依赖 git）
# ======================================================================


def lf_normalized(data: bytes) -> bytes:
    """把 CRLF 规范化为 LF —— 使哈希**平台无关**（Windows 工作区可能是 CRLF）。"""
    return data.replace(b"\r\n", b"\n")


def read_lf(path: str) -> bytes:
    with open(path, "rb") as handle:
        return lf_normalized(handle.read())


def git_blob_sha(data_lf: bytes) -> str:
    """
    由内容直接计算 **git blob SHA-1**：``sha1(b"blob <len>\\0" + content)``。

    刻意不调用 git —— 使"冻结副本是否为该 blob"这件事在**没有 git 的环境**
    （如 CI 浅克隆、导出目录）下同样可验证。
    """
    header = b"blob %d\x00" % len(data_lf)
    return hashlib.sha1(header + data_lf).hexdigest()


def file_blob_sha(path: str) -> str:
    return git_blob_sha(read_lf(path))


def file_lf_sha256(path: str) -> str:
    return hashlib.sha256(read_lf(path)).hexdigest()


# ======================================================================
# 3. Legacy 冻结副本
# ======================================================================


def legacy_source_text() -> str:
    """冻结副本的源码文本（用于符号审计 —— 不需要 import）。"""
    return read_lf(LEGACY_IMPL_PATH).decode("utf-8")


def legacy_source_violations() -> list[str]:
    """返回冻结副本中**不应出现**的 current-only policy 符号（空 = 合规）。"""
    source = legacy_source_text()
    return [marker for marker in CURRENT_ONLY_POLICY_SYMBOLS if marker in source]


def current_source_text() -> str:
    return read_lf(CURRENT_IMPL_PATH).decode("utf-8")


def current_source_missing_symbols() -> list[str]:
    """返回 current 实现中**缺失**的 policy 路径符号（空 = 合规）。"""
    source = current_source_text()
    return [marker for marker in CURRENT_POLICY_REQUIRED_SYMBOLS if marker not in source]


def shared_dependency_drift() -> dict[str, tuple[str, str]]:
    """
    受控共享依赖的漂移。

    :returns: ``{模块名: (钉住的 blob SHA, 当前 blob SHA)}``；空 dict = 无漂移。
    """
    drift = {}
    for name, pinned in CONTROLLED_SHARED_DEPENDENCIES.items():
        actual = file_blob_sha(os.path.join(_ECU_DIR, name))
        if actual != pinned:
            drift[name] = (pinned, actual)
    return drift


@dataclass(frozen=True)
class GitCrossCheck:
    """``git show 4059cf5:<path>`` 与冻结副本的交叉校验结果。"""

    #: 本地是否**可用**（git 存在 ∧ 该 commit 存在 ∧ 命令成功）。
    available: bool

    #: ``True``/``False`` = 已比较的结论；``None`` = 未能比较（跳过）。
    matches: bool | None

    detail: str


def git_cross_check() -> GitCrossCheck:
    """
    与 git 历史交叉校验冻结副本。

    **git 不可用（或浅克隆中没有该 commit）时返回 ``available=False``，
    不视为失败** —— A2 必须在无 git 环境下仍可执行。
    """
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{LEGACY_SOURCE_COMMIT}^{{commit}}"],
            cwd=_LAB_DIR,
            capture_output=True,
        )
    except OSError as exc:  # git 不存在
        return GitCrossCheck(False, None, f"git 不可用（{exc.__class__.__name__}）—— 跳过交叉校验")

    if probe.returncode != 0:
        return GitCrossCheck(
            False,
            None,
            f"本地没有 commit {LEGACY_SOURCE_COMMIT_SHORT}（浅克隆 / 未 fetch）—— 跳过交叉校验",
        )

    show = subprocess.run(
        ["git", "show", f"{LEGACY_SOURCE_COMMIT}:{LEGACY_SOURCE_RELPATH}"],
        cwd=_LAB_DIR,
        capture_output=True,
    )
    if show.returncode != 0:
        return GitCrossCheck(
            False, None, f"git show 失败 —— 跳过交叉校验：{show.stderr.decode().strip()}"
        )

    if lf_normalized(show.stdout) == read_lf(LEGACY_IMPL_PATH):
        return GitCrossCheck(
            True, True, f"冻结副本 == git show {LEGACY_SOURCE_COMMIT_SHORT}:{LEGACY_SOURCE_RELPATH}"
        )
    return GitCrossCheck(
        True, False, f"冻结副本与 git show {LEGACY_SOURCE_COMMIT_SHORT} 的内容**不一致**"
    )


def load_legacy_gateway():
    """
    加载**冻结的 pre-policy gateway 实现**（独立模块名，不遮蔽 current）。

    模块级只做一次 import，但**每次调用都不共享 ECU / gateway 实例** ——
    实例的新鲜性由 :func:`build_ecus` / :func:`run_legacy_execution` 保证。
    """
    existing = sys.modules.get(LEGACY_MODULE_NAME)
    if existing is not None:
        return existing

    spec = importlib.util.spec_from_file_location(LEGACY_MODULE_NAME, LEGACY_IMPL_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - 仅防御
        raise RuntimeError(f"无法加载 legacy 冻结副本：{LEGACY_IMPL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[LEGACY_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def load_current_gateway():
    """加载**当前生产实现** ``ecu/gateway.py``（含 policy 机制）。"""
    import gateway  # 经 sys.path 解析到 ecu/gateway.py

    return gateway


# ======================================================================
# 4. 冻结输入集
# ======================================================================


@dataclass(frozen=True)
class FrozenCase:
    """一条冻结 case（输入 + 历史锚点）。**输入集不可扩写。**"""

    case_id: str
    description: str
    can_id: int
    payload: bytes
    ecus: tuple
    #: 历史锚点（A0 捕获的 legacy 行为）—— 只用于 anchor 断言，不用于主差分。
    #:
    #: **逐字保留 corpus 的 JSON 形状**（``[[response_id, payload_hex], …]``），
    #: 与 :func:`response_to_golden` 的产出口径一致 —— 刻意不做 tuple 转换，
    #: 否则 ``tuple of lists`` 与 ``tuple of tuples`` 会**永远不相等**。
    expected_response: list
    expected_gateway_state: dict

    @property
    def request_ids(self) -> set[int]:
        return {ecu["request_id"] for ecu in self.ecus}


def load_frozen_corpus() -> dict:
    with open(GOLDEN_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def frozen_cases() -> list[FrozenCase]:
    """
    读取冻结 corpus 的 20 条 case。

    **只读**：本模块没有任何写入 corpus 的代码路径。
    """
    return [
        FrozenCase(
            case_id=entry["case_id"],
            description=entry["description"],
            can_id=entry["can_id"],
            payload=bytes.fromhex(entry["payload"]),
            ecus=tuple(entry["ecus"]),
            expected_response=list(entry["expected_response"]),
            expected_gateway_state=entry["expected_gateway_state"],
        )
        for entry in load_frozen_corpus()["cases"]
    ]


# ======================================================================
# 5. 实例构造 —— 两条路径**各自**构造，绝不共用 ECU 对象
# ======================================================================


def echo_77_handler(payload: bytes) -> bytes:
    """确定性自定义 handler（与 A0 corpus 的 ``echo_77`` 种类同构）。"""
    return bytes([0x77]) + payload


def _handler_for(kind: str):
    if kind == HANDLER_ECHO_77:
        return echo_77_handler
    return None  # None ⇒ VirtualEcu 绑定该实现自己的默认协议层 dispatcher


def fresh_state(spec: dict) -> EcuState:
    """由 case 规格构造**全新**的 :class:`EcuState`（每次调用都是新对象）。"""
    state = EcuState()
    state.session = spec["session"]
    state.security_level = spec["security_level"]
    state.did_overrides = {
        int(did, 16): bytes.fromhex(value) for did, value in spec["did_overrides"].items()
    }
    return state


def build_ecus(case: FrozenCase, module) -> list:
    """
    用**指定实现模块**的 ``VirtualEcu`` 构造一批**全新** ECU 实例。

    Legacy 与 Current 各自调用本函数一次 ⇒ 两批对象、两批 ``EcuState``
    彼此独立（禁止共用实例）。
    """
    return [
        module.VirtualEcu(
            name=spec["name"],
            request_id=spec["request_id"],
            response_id=spec["response_id"],
            state=fresh_state(spec["state"]),
            handler=_handler_for(spec["handler"]),
        )
        for spec in case.ecus
    ]


def gateway_state_projection(gateway) -> dict:
    """
    逐 ECU 三字段状态投影 —— 与 A0/A1 **同口径**（可直接与冻结锚点比较）。

    ``did_overrides`` 规范化为按 DID 排序的 ``{hex: hex}``，不依赖 dict 插入顺序。
    """
    return {
        ecu.name: {
            "session": ecu.state.session,
            "security_level": ecu.state.security_level,
            "did_overrides": {
                f"{did:04x}": value.hex() for did, value in sorted(ecu.state.did_overrides.items())
            },
        }
        for ecu in gateway.ecus
    }


# ======================================================================
# 6. 执行（每条路径**各自**执行一次）
# ======================================================================


@dataclass(frozen=True)
class Execution:
    """一次实现路径的执行结果。"""

    #: ``IMPL_LEGACY`` 或 ``IMPL_CURRENT``。
    implementation: str

    #: ``((response_id, payload), …)``。
    results: tuple

    #: 逐 ECU 三字段状态投影。
    state: dict

    #: ``PolicyTrace``（仅 current 有；legacy 恒为 ``None``）。
    trace: object | None = None

    #: 本执行路径对该 case 的 ``route*`` 调用次数 —— 恒为 1（单请求不变式）。
    executions: int = 1

    #: 实际执行过的 gateway 实例（供"两路径不共用实例"的结构断言使用）。
    gateway: object = field(default=None, repr=False, compare=False)

    @property
    def response_count(self) -> int:
        return len(self.results)


def run_legacy_execution(case: FrozenCase, legacy_module=None) -> Execution:
    """**Legacy 路径**：全新 ECU 实例 + 冻结实现 + 恰好一次 ``route()``。"""
    module = load_legacy_gateway() if legacy_module is None else legacy_module
    gateway = module.DiagnosticGateway(build_ecus(case, module))
    results = gateway.route(case.can_id, case.payload)
    return Execution(
        implementation=IMPL_LEGACY,
        results=tuple(results),
        state=gateway_state_projection(gateway),
        trace=None,
        gateway=gateway,
    )


def run_current_execution(case: FrozenCase, current_module=None) -> Execution:
    """
    **Current 路径**：全新 ECU 实例 + 生产实现 + 恰好一次 ``route_with_trace()``。

    用 ``route_with_trace()`` 而非 ``route()`` 只是为了**顺带**取得 trace 作
    辅助证据 —— 两者共用同一个 ``_route_core``（C2-OD-9），因此 wire / state
    语义完全相同（C2-A1 已验证）。策略为生产默认（空策略）。

    .. note::
       **不为通过 A2 而注入任何策略** —— 这里断言策略确实是 ``DEFAULT_POLICY``。
    """
    module = load_current_gateway() if current_module is None else current_module
    gateway = module.DiagnosticGateway(build_ecus(case, module))
    assert gateway.policy is module.DEFAULT_POLICY, (
        "A2 的 current 路径必须运行在生产默认（空策略）下"
    )
    results, trace = gateway.route_with_trace(case.can_id, case.payload)
    return Execution(
        implementation=IMPL_CURRENT,
        results=tuple(results),
        state=gateway_state_projection(gateway),
        trace=trace,
        gateway=gateway,
    )


# ======================================================================
# 7. 差分模型
# ======================================================================


def format_payload(payload: bytes) -> str:
    return "<empty>" if not payload else payload.hex(" ")


def format_response(results: tuple) -> str:
    if not results:
        return "[]"
    return "[" + ", ".join(format_response_item(item) for item in results) + "]"


def format_response_item(item: tuple | None) -> str:
    if item is None:
        return "<missing>"
    response_id, payload = item
    return f"(0x{response_id:03X}, {payload.hex(' ') or '<empty>'})"


def format_state_value(value) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {v}" for k, v in sorted(value.items())) + "}"
    return repr(value)


@dataclass(frozen=True)
class ResponseItemDiff:
    """单条 response 的差异（按位置对齐；缺失一侧为 ``None``）。"""

    index: int
    legacy: tuple | None
    current: tuple | None


@dataclass(frozen=True)
class StateFieldDiff:
    """逐 ECU → field 的状态差异。"""

    ecu: str
    field: str
    legacy: object
    current: object


@dataclass(frozen=True)
class DifferentialResult:
    """
    一个 case 的完整差分结果。

    差分**只**由 :attr:`legacy` 与 :attr:`current` 两条真实执行产生；
    ``trace`` 不参与比较（legacy 没有该概念）。
    """

    case_id: str
    description: str
    can_id: int
    payload: bytes
    legacy: Execution
    current: Execution

    # ---- 判定 ----

    @property
    def response_matches(self) -> bool:
        return self.legacy.results == self.current.results

    @property
    def state_matches(self) -> bool:
        return self.legacy.state == self.current.state

    @property
    def matched(self) -> bool:
        return self.response_matches and self.state_matches

    # ---- 差异定位 ----

    @property
    def response_diffs(self) -> tuple[ResponseItemDiff, ...]:
        """按位置逐条定位；条数不同也由缺失侧表达出来。"""
        diffs = []
        for index in range(max(len(self.legacy.results), len(self.current.results))):
            left = self.legacy.results[index] if index < len(self.legacy.results) else None
            right = self.current.results[index] if index < len(self.current.results) else None
            if left != right:
                diffs.append(ResponseItemDiff(index=index, legacy=left, current=right))
        return tuple(diffs)

    @property
    def state_diffs(self) -> tuple[StateFieldDiff, ...]:
        """逐 ECU、逐字段定位（含"某一侧缺少该 ECU"的情形）。"""
        diffs = []
        for name in sorted(set(self.legacy.state) | set(self.current.state)):
            left = self.legacy.state.get(name)
            right = self.current.state.get(name)
            if left is None or right is None:
                diffs.append(StateFieldDiff(ecu=name, field="<ecu>", legacy=left, current=right))
                continue
            for name_field in STATE_FIELDS:
                if left[name_field] != right[name_field]:
                    diffs.append(
                        StateFieldDiff(
                            ecu=name,
                            field=name_field,
                            legacy=left[name_field],
                            current=right[name_field],
                        )
                    )
        return tuple(diffs)

    # ---- 失败报告（CI 失败后可直接定位）----

    def failure_report(self) -> str:
        lines = [
            f"[MISMATCH] case={self.case_id}",
            f"  description = {self.description}",
            f"  can_id      = 0x{self.can_id:03X}",
            f"  payload     = {format_payload(self.payload)}",
            f"  executions  = legacy={self.legacy.executions} current={self.current.executions}",
        ]

        if not self.response_matches:
            lines.append(
                f"  response    : 条数 legacy={self.legacy.response_count} "
                f"current={self.current.response_count}"
            )
            for diff in self.response_diffs:
                lines.append(f"    [{diff.index}] legacy  = {format_response_item(diff.legacy)}")
                lines.append(f"        current = {format_response_item(diff.current)}")

        if not self.state_matches:
            lines.append("  state       : 逐 ECU 差异")
            for diff in self.state_diffs:
                lines.append(
                    f"    {diff.ecu}.{diff.field}: "
                    f"legacy={format_state_value(diff.legacy)} "
                    f"current={format_state_value(diff.current)}"
                )

        return "\n".join(lines)

    def summary_line(self) -> str:
        verdict = "OK  " if self.matched else "DIFF"
        return (
            f"[{verdict}] {self.case_id:42s} "
            f"can_id=0x{self.can_id:03X} payload={format_payload(self.payload):12s} "
            f"legacy={format_response(self.legacy.results):46s} "
            f"current={format_response(self.current.results)}"
        )


def compare(case: FrozenCase, legacy: Execution, current: Execution) -> DifferentialResult:
    """比较器：两条执行 → :class:`DifferentialResult`。**不修改任何一侧。**"""
    if legacy.implementation != IMPL_LEGACY:
        raise ValueError(f"expected legacy execution, got {legacy.implementation!r}")
    if current.implementation != IMPL_CURRENT:
        raise ValueError(f"expected current execution, got {current.implementation!r}")
    return DifferentialResult(
        case_id=case.case_id,
        description=case.description,
        can_id=case.can_id,
        payload=case.payload,
        legacy=legacy,
        current=current,
    )


def run_case(case: FrozenCase, legacy_module=None, current_module=None) -> DifferentialResult:
    """
    **A2 主执行路径**：同一个 case → 两条路径各自实例化、各自执行一次 → 比较器。

    两条路径的执行是**顺序且隔离**的：legacy 的 ECU 状态**不会**被 current
    复用（各自 :func:`build_ecus`）。
    """
    legacy = run_legacy_execution(case, legacy_module)
    current = run_current_execution(case, current_module)
    return compare(case, legacy, current)


def run_all_cases(legacy_module=None, current_module=None) -> list[DifferentialResult]:
    return [run_case(case, legacy_module, current_module) for case in frozen_cases()]


# ======================================================================
# 8. 历史锚点 —— 只作一致性证据，**不是** A2 的核心证明
# ======================================================================


def response_to_golden(results: tuple) -> list:
    """
    ``((response_id, payload), …)`` → ``[[response_id, payload_hex], …]``。

    输出形状与 corpus 的 ``expected_response`` **逐字一致**（同为 list of
    list），使锚点比较是纯粹的值比较，不掺入容器类型差异。
    """
    return [[response_id, payload.hex()] for response_id, payload in results]


@dataclass(frozen=True)
class AnchorResult:
    """
    某一条执行路径与**冻结 golden** 的一致性。

    .. important::
       这只是"各自与历史锚点一致"的一致性证据。
       A2 的核心证明是 ``legacy-live == current-live``（:class:`DifferentialResult`）。
    """

    label: str
    case_id: str
    response_matches: bool
    state_matches: bool

    @property
    def matched(self) -> bool:
        return self.response_matches and self.state_matches


def anchor_against_golden(case: FrozenCase, execution: Execution, label: str) -> AnchorResult:
    return AnchorResult(
        label=label,
        case_id=case.case_id,
        response_matches=response_to_golden(execution.results) == case.expected_response,
        state_matches=execution.state == case.expected_gateway_state,
    )


# ======================================================================
# 9. 汇总（A2 gate 的证据数字）
# ======================================================================


@dataclass(frozen=True)
class DifferentialSummary:
    """A2 gate 的汇总口径 —— 任何漂移都会使数字变化，从而立刻可见。"""

    cases: int
    legacy_executions: int
    current_executions: int
    response_matches: int
    state_matches: int
    legacy_anchor_matches: int
    current_anchor_matches: int
    mismatched_case_ids: tuple

    @property
    def mismatches(self) -> int:
        return len(self.mismatched_case_ids)

    @property
    def passed(self) -> bool:
        return self.mismatches == 0

    def as_lines(self) -> list[str]:
        return [
            f"cases                = {self.cases}",
            f"legacy executions    = {self.legacy_executions}",
            f"current executions   = {self.current_executions}",
            f"response matches     = {self.response_matches} / {self.cases}",
            f"state    matches     = {self.state_matches} / {self.cases}",
            f"legacy  anchor       = {self.legacy_anchor_matches} / {self.cases}",
            f"current anchor       = {self.current_anchor_matches} / {self.cases}",
            f"mismatches           = {self.mismatches}",
        ]


def summarize_with_anchors(
    results: list[DifferentialResult],
    legacy_anchors: list[AnchorResult],
    current_anchors: list[AnchorResult],
) -> DifferentialSummary:
    """由差分结果 + 两侧锚点汇总出 A2 gate 的全部证据数字。"""
    return DifferentialSummary(
        cases=len(results),
        legacy_executions=sum(r.legacy.executions for r in results),
        current_executions=sum(r.current.executions for r in results),
        response_matches=sum(1 for r in results if r.response_matches),
        state_matches=sum(1 for r in results if r.state_matches),
        legacy_anchor_matches=sum(1 for a in legacy_anchors if a.matched),
        current_anchor_matches=sum(1 for a in current_anchors if a.matched),
        mismatched_case_ids=tuple(r.case_id for r in results if not r.matched),
    )


def run_all_with_anchors() -> tuple[list[DifferentialResult], list[AnchorResult], list[AnchorResult]]:
    """一次跑完全部 20 条 case，并分别生成两侧的 golden 锚点结果。"""
    results = run_all_cases()
    cases = {case.case_id: case for case in frozen_cases()}
    legacy_anchors = []
    current_anchors = []
    for result in results:
        case = cases[result.case_id]
        legacy_anchors.append(
            anchor_against_golden(case, result.legacy, "legacy-vs-frozen-golden")
        )
        current_anchors.append(
            anchor_against_golden(case, result.current, "current-vs-frozen-golden")
        )
    return results, legacy_anchors, current_anchors
