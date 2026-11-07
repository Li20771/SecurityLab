"""OTA 安全层：包装 uds-basic 的 0x34/0x36/0x37（P16 应用层安全测试）。

安全策略（三层，按"校验顺序即安全属性"设计）：

1. **0x34 RequestDownload 门控**（越权升级）::

       default session            → NRC 0x7F serviceNotSupportedInActiveSession
       未通过 SecurityAccess      → NRC 0x33 securityAccessDenied
       否则                        → 委托 uds-basic 的 service_request_download

2. **0x36 TransferData** 完全委托 uds-basic（分块完整性沿用其 NRC 契约）::

       错序 / 跳号                → 0x73 wrongBlockSequenceCounter
       完全重复上一条（幂等）      → 0x76 正响应、不追加
       块超长（data > 126）       → 0x13
       累计超过 0x34 声明 size    → 0x13
       无 active download         → 0x24

3. **0x37 RequestTransferExit 完成时**::

       传输完整（buffer == size）→ 先验签（SM2 over manifest），再判版本：
           签名失败            → 0x72 generalProgrammingFailure（中止 transfer）
           版本非单调（<= 当前）→ 0x72（防降级 / 防重放）
           低于 manifest 下限   → 0x72
           全部通过            → 委托 uds-basic 返回 0x77 并更新 current_version
       传输不完整                → 委托（0x22，保持 active）
       无 active transfer        → 委托（0x24）

**刻意顺序**（对齐 P16 F-02 教训）：**先验签、后判版本**——绝不拿未认证的
manifest 字段做决策。攻击者密钥签名的 `version=9.9.9` 包必须死在
signature_invalid，而不是先被版本判定"放行"。
"""

import time
from dataclasses import dataclass

import uds_server
import uds_types

from ota_image import parse_version, split_package, verify_manifest

__all__ = ["NRC_GENERAL_PROGRAMMING_FAILURE", "OtaState", "OtaEcu", "DEFAULT_SECURITY_CONFIG"]

#: 0x72 generalProgrammingFailure：编程/校验阶段的失败（签名、降级、包结构）。
NRC_GENERAL_PROGRAMMING_FAILURE = 0x72

#: 与 uds-basic 默认值一致的安全访问参数（测试可覆盖）。
DEFAULT_SECURITY_CONFIG = {
    "max_attempts": 3,
    "lockout_seconds": 10,
    "seed_expiry_seconds": 5,
}


@dataclass
class OtaState:
    """OTA 应用层状态（与 uds-basic 的 EcuState 分离，不修改其字段）。"""

    current_version: str = "2.3.0"
    last_verdict: str = ""
    last_detail: str = ""


class OtaEcu:
    """OTA 门控 ECU：协议层状态用 ``uds_server.EcuState``，OTA 状态用 ``OtaState``。

    :param public_key: OEM 验签公钥（64 字节 x‖y，模拟 HSM 预置）
    :param state: ``uds_server.EcuState`` 实例；默认使用全局 ``STATE``
    :param ota_state: ``OtaState`` 实例（可注入当前版本）
    :param security_config: 0x27 参数；默认与 uds-basic 一致
    """

    def __init__(
        self,
        public_key: bytes,
        state=None,
        ota_state: OtaState | None = None,
        security_config: dict | None = None,
    ) -> None:
        self.public_key = bytes(public_key)
        self.state = state if state is not None else uds_server.STATE
        self.ota = ota_state if ota_state is not None else OtaState()
        self.security_config = security_config or dict(DEFAULT_SECURITY_CONFIG)

    def handle(self, request: bytes, now: float | None = None) -> bytes:
        """处理一条 UDS 请求（每次请求现场求值 0x27 依赖，与 uds-basic 入口一致）。"""
        now = time.monotonic() if now is None else now

        services = {
            **uds_server.SERVICES,
            **uds_server.security_access_services(
                now=now, security_config=self.security_config
            ),
            **self._ota_services(),
        }

        return uds_server.handle_uds_request(request, self.state, services=services)

    # ------------------------------------------------------------------
    # 服务覆盖
    # ------------------------------------------------------------------

    def _ota_services(self) -> dict:
        return {
            0x34: self._request_download,
            0x36: self._transfer_data,
            0x37: self._request_transfer_exit,
        }

    def _request_download(self, request: bytes, state) -> bytes:
        if state.session == uds_types.SESSION_DEFAULT:
            return uds_types.negative_response(
                0x34, uds_types.NRC_SERVICE_NOT_SUPPORTED_IN_SESSION
            )

        if state.security_level < uds_types.SECURITY_LEVEL1:
            return uds_types.negative_response(
                0x34, uds_types.NRC_SECURITY_ACCESS_DENIED
            )

        return uds_server.service_request_download(request, state)

    def _transfer_data(self, request: bytes, state) -> bytes:
        # 分块完整性沿用 uds-basic 的 NRC 契约（错序 0x73 / 幂等 0x76 /
        # 超长 0x13 / 越界 0x13 / 无 active 0x24）。本层只做透传，
        # 是将来增加"逐块哈希校验"的扩展点。
        return uds_server.service_transfer_data(request, state)

    def _request_transfer_exit(self, request: bytes, state) -> bytes:
        verdict: tuple[str, str] | None = None
        accepted_version: str | None = None

        complete = (
            state.download_active
            and len(request) == 1
            and len(state.download_buffer) == state.download_size
        )

        if complete:
            verdict, accepted_version = self._evaluate(bytes(state.download_buffer))
            self.ota.last_verdict, self.ota.last_detail = verdict

            if verdict[0] != "pass":
                self._abort_download(state)
                return uds_types.negative_response(
                    0x37, NRC_GENERAL_PROGRAMMING_FAILURE
                )

        response = uds_server.service_request_transfer_exit(request, state)

        if verdict is not None and response[:1] == b"\x77":
            self.ota.current_version = accepted_version

        return response

    # ------------------------------------------------------------------
    # 校验与中止
    # ------------------------------------------------------------------

    def _evaluate(self, payload: bytes) -> tuple[tuple[str, str], str | None]:
        """返回 ``((verdict, detail), accepted_version)``。

        顺序不可调换：先验签（认证），再判版本（授权决策）。
        """
        try:
            manifest, signature = split_package(payload)
        except ValueError as exc:
            return ("malformed_package", str(exc)), None

        if not verify_manifest(manifest, signature, self.public_key):
            return ("signature_invalid", "manifest 签名验证失败"), None

        version = manifest["version"]

        if parse_version(version) <= parse_version(self.ota.current_version):
            return (
                "version_not_monotonic",
                f"{version} <= 当前 {self.ota.current_version}",
            ), None

        if parse_version(version) < parse_version(manifest["min_version"]):
            return (
                "below_min_version",
                f"{version} < 下限 {manifest['min_version']}",
            ), None

        return ("pass", f"{version} > 当前 {self.ota.current_version}"), version

    @staticmethod
    def _abort_download(state) -> None:
        """镜像 uds-basic 0x37 成功分支的 download 字段清理（fail-closed 中止）。

        注意：本函数是 uds-basic 清理逻辑的**镜像**；若 uds-basic 的
        download 字段集合变化，这里需要同步（有单测断言字段被清空）。
        """
        state.download_active = False
        state.download_address = 0
        state.download_size = 0
        state.download_expected_block = 0
        state.download_buffer.clear()
        state.download_last_block_request = None
