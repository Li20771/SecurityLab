# TRAE State — SecurityLab Autonomous Build

> 断点恢复文件。任何 `wsl --shutdown` / `--terminate` / Docker Desktop 重启 / 系统服务修改之前更新此文件与 TRAE_STATE.json。

## 2026-09-18 (evening session)

**current_phase**: Phase 1 — WSL / Interop (N-01 durable fix)
**current_task**: Install binfmt self-healing on Ubuntu; verify via Kali cold boot

### Completed (this session)

- Replay `--all` 目录过滤修复验收：offline 34+50+89 全过；30/30 replay identity OK / class 一致 / issue=0 / EXIT=0。**不重做、不覆盖、不提交。**
- N-01 根因：Kali 冷启动间歇性清空 VM 共享的 binfmt_misc 注册表（非 remount，条目被删；Kali 的 WSL protectBinfmt drop-in `echo -1 > WSLInterop` 报 EACCES）。证据：`.p1x-probe/*-OUTPUT.txt`。
- N-01 廉价恢复已验证：`wsl -d Ubuntu -u root -- systemctl restart systemd-binfmt.service`。

### Protected / do-not-touch

- 未提交修改：`replay.py`、`test_fuzz_offline.py`（用户资产，不得 reset/clean/commit）
- `docs/ENVIRONMENT_INVENTORY_2026-09-17.md`、`docs/PHASE0_PHASE1_EXECUTION_2026-09-17.md`
- `labs/vehicle/fuzz-out/`（30 testcase + smoke-summary.json）
- 所有 Docker volume/image/container

### Next action

1. Phase 0 baseline rescan → `docs/TRAE_BASELINE_2026-09-18.md`
2. Phase 1: 安装 Ubuntu binfmt 自愈（path unit + systemd-binfmt 重跑），Kali 冷启动回归验证
3. Phase 2: Kali user@1000.service
4. Phase 3: Docker Desktop + WSL Integration（需用户在 Docker Desktop GUI 勾选，或验证 settings.json）

### Rollback

本 session 尚未修改系统配置。新增文件均可安全删除。
