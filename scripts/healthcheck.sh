#!/usr/bin/env bash
#
# SecurityLab 统一环境健康检查
#
#   bash scripts/healthcheck.sh            # 快速检查（默认，秒级；工具在位 + 轻量功能）
#   bash scripts/healthcheck.sh --extended # 额外跑真实闭环（UDS e2e / CAN capture / YARA）
#
# 结果：[PASS] 正常  [WARN] 非核心缺失或可恢复  [FAIL] 核心能力不可用
# 退出码：有 FAIL -> 1；否则 0（WARN 不致失败）。
# 仅在 WSL Ubuntu 主环境有意义；所有 CAN 操作只针对 vcan0。

set -u

EXTENDED=0
if [ "${1:-}" = "--extended" ]; then EXTENDED=1; fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PASS=0
WARN=0
FAIL=0

ok()   { echo "[PASS] $1"; PASS=$((PASS + 1)); }
warn() { echo "[WARN] $1"; WARN=$((WARN + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }
section() { echo; echo "=== $1 ==="; }

# 命令存在性检查：tool_present <命令> [warn|fail]（默认 warn）
tool_present() {
    local cmd="$1" level="${2:-warn}"
    if command -v "$cmd" >/dev/null 2>&1; then
        ok "$cmd"
    else
        if [ "$level" = "fail" ]; then fail "$cmd not found"; else warn "$cmd not found"; fi
    fi
}

echo "========================================"
echo " SecurityLab Environment Health Check"
echo "  $(date '+%Y-%m-%d %H:%M:%S')  extended=$EXTENDED"
echo "========================================"

section "System / WSL"
if grep -qi microsoft /proc/version 2>/dev/null; then
    ok "WSL2 kernel ($(uname -r))"
else
    warn "not running under WSL"
fi
command -v wslpath >/dev/null 2>&1 && ok "wslpath" || warn "wslpath not found"
if systemctl is-system-running 2>/dev/null | grep -q '^running$'; then
    ok "systemd running"
else
    warn "systemd not fully running ($(systemctl is-system-running 2>/dev/null))"
fi

section "binfmt / WSL interop"
if [ -e /proc/sys/fs/binfmt_misc/WSLInterop ]; then
    ok "WSLInterop handler present"
else
    warn "WSLInterop handler missing (Windows interop broken)"
fi
if [ -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]; then
    n=$(ls /proc/sys/fs/binfmt_misc 2>/dev/null | grep -c qemu)
    ok "qemu binfmt handlers ($n, incl. qemu-aarch64)"
else
    fail "qemu-aarch64 binfmt handler missing (ARM64 transparent exec broken)"
fi
# ARM64 编译 -> 执行 真闭环（返回码 42）
ARM_TMP="$(mktemp -d)"
if command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
    if printf 'int main(void){return 42;}\n' > "$ARM_TMP/t.c" \
       && aarch64-linux-gnu-gcc -static "$ARM_TMP/t.c" -o "$ARM_TMP/t" 2>/dev/null; then
        # 透明执行（binfmt）：显式保存退出码。
        # 历史缺陷：`if cmd; then RC=$?; else RC=$?; fi` 与 `&& [ "$?" = 42 ]`
        # 在 qemu 透传 guest 退出码 42（非零）时会短路，fallback 永不触发。
        "$ARM_TMP/t" >/dev/null 2>&1
        RC=$?
        if [ "$RC" -eq 42 ]; then
            ok "ARM64 compile + transparent exec (exit 42)"
        elif command -v qemu-aarch64 >/dev/null 2>&1; then
            # fallback：qemu-user 显式执行（qemu 透传 guest 退出码，
            # 因此必须先保存返回值再比较，不能用 && 链）。
            qemu-aarch64 "$ARM_TMP/t" >/dev/null 2>&1
            QRC=$?
            if [ "$QRC" -eq 42 ]; then
                ok "ARM64 compile + qemu-aarch64 exec (exit 42)"
            else
                fail "ARM64 binary built but neither binfmt nor qemu-aarch64 executed it (transparent rc=$RC, qemu rc=$QRC)"
            fi
        else
            fail "ARM64 binary built but transparent exec failed (rc=$RC) and qemu-aarch64 not found"
        fi
    else
        fail "aarch64-linux-gnu-gcc failed to build static binary"
    fi
else
    fail "aarch64-linux-gnu-gcc not found"
fi
rm -rf "$ARM_TMP"

section "Git"
if git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    ok "SecurityLab Git repository (HEAD $(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null))"
else
    fail "Not inside a Git repository"
fi
if [ -n "$(git -C "$PROJECT_ROOT" status --porcelain 2>/dev/null)" ]; then
    warn "Git working tree has uncommitted changes (by design during lab work)"
else
    ok "Git working tree clean"
fi

section "DSH / Agent"
# 非登录 shell 也能查询 user manager
[ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ] && export XDG_RUNTIME_DIR="/run/user/$(id -u)"
DSH_SVC_STATE="$(systemctl --user is-active dsh-web.service 2>/dev/null || true)"
if [ "$DSH_SVC_STATE" = "active" ]; then
    ok "dsh-web.service active (Web UI 127.0.0.1:8731, autostart via linger)"
elif command -v dsh >/dev/null 2>&1; then
    ok "dsh command available ($(dsh --version 2>/dev/null | head -1)); web service not active"
else
    warn "neither dsh-web.service active nor dsh on PATH (start: systemctl --user start dsh-web)"
fi

section "Python"
PYTHON_BIN=""
if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python"
elif [ -x "$HOME/.venvs/network/bin/python" ]; then
    PYTHON_BIN="$HOME/.venvs/network/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
fi
if [ -n "$PYTHON_BIN" ]; then
    ok "Python ($PYTHON_BIN, $($PYTHON_BIN --version 2>&1))"
else
    fail "Python interpreter not found"
fi

PYTHON_REQUIREMENTS="$PROJECT_ROOT/configs/environment/python-network-requirements.txt"
python_check() {
    local package="$1" module="$2" label="$3" expected="$4"
    [ -z "$PYTHON_BIN" ] && { fail "$label unavailable"; return; }
    if ! "$PYTHON_BIN" -c "import $module" >/dev/null 2>&1; then
        fail "$label unavailable"; return
    fi
    local actual
    actual=$("$PYTHON_BIN" -c "from importlib.metadata import version; print(version('$package'))" 2>/dev/null)
    if [ "$actual" = "$expected" ]; then ok "$label ($actual)"; else
        fail "$label version mismatch: expected $expected, found $actual"; fi
}
if [ ! -f "$PYTHON_REQUIREMENTS" ]; then
    fail "Python requirements file not found"
else
    while IFS= read -r requirement || [ -n "$requirement" ]; do
        case "$requirement" in ""|\#*) continue ;; esac
        package="${requirement%%==*}"; expected="${requirement#*==}"
        case "$package" in
            can-isotp) module="isotp"; label="can-isotp" ;;
            python-can) module="can"; label="python-can" ;;
            pwntools) module="pwn"; label="Pwntools" ;;
            *) module="$package"; label="$package" ;;
        esac
        python_check "$package" "$module" "$label" "$expected"
    done < "$PYTHON_REQUIREMENTS"
fi
# 额外安全研究包：存在即可（不强制版本，缺失记 WARN）
if [ -n "$PYTHON_BIN" ]; then
    for pair in "capstone:capstone" "unicorn:unicorn" "cryptography:cryptography"; do
        pkg="${pair%%:*}"; mod="${pair##*:}"
        if "$PYTHON_BIN" -c "import $mod" >/dev/null 2>&1; then
            v=$("$PYTHON_BIN" -c "from importlib.metadata import version;print(version('$pkg'))" 2>/dev/null)
            ok "$pkg ($v)"
        else
            warn "$pkg not importable in venv"
        fi
    done
fi

section "CAN / SocketCAN"
tool_present candump fail
tool_present cansend fail
tool_present canplayer warn
tool_present isotprecv warn
tool_present isotpsend warn
if [ -e /sys/module/can_isotp ]; then ok "kernel module can_isotp loaded"; else
    warn "can_isotp module not loaded (run: sudo modprobe can_isotp)"; fi
if ip link show vcan0 >/dev/null 2>&1; then
    if ip link show vcan0 2>/dev/null | grep -qE 'state UP|UP,'; then
        ok "vcan0 exists and is UP (mtu $(cat /sys/class/net/vcan0/mtu 2>/dev/null))"
    else
        fail "vcan0 exists but is not UP"
    fi
else
    fail "vcan0 not found"
fi
systemctl is-enabled vcan0.service >/dev/null 2>&1 && ok "vcan0.service enabled" || warn "vcan0.service not enabled"
systemctl is-active  vcan0.service >/dev/null 2>&1 && ok "vcan0.service active"  || fail "vcan0.service inactive"

section "Compilers / cross toolchains"
for t in gcc g++ clang make cmake ninja pkg-config git curl wget jq; do tool_present "$t" warn; done
tool_present aarch64-linux-gnu-gcc fail
tool_present arm-linux-gnueabihf-gcc warn
tool_present qemu-aarch64 fail
tool_present qemu-arm warn
tool_present gdb-multiarch fail

section "Reverse engineering"
for t in file readelf objdump nm strings gdb radare2 rabin2 patchelf binwalk; do tool_present "$t" warn; done

section "Dynamic analysis"
tool_present strace warn
tool_present ltrace warn

section "Firmware"
for t in mksquashfs unsquashfs binwalk mkfs.jffs2 mkfs.ubifs ubinize; do tool_present "$t" warn; done

section "Fuzzing"
tool_present afl-fuzz warn
tool_present afl-gcc warn
tool_present yara warn

section "Network security"
for t in nmap tcpdump tshark ncat socat nft iptables; do tool_present "$t" warn; done

section "Docker"
if command -v docker >/dev/null 2>&1; then
    if timeout 12 docker version --format 'Client={{.Client.Version}} Server={{.Server.Version}}' >/dev/null 2>&1; then
        ok "docker CLI -> engine ($(timeout 12 docker version --format '{{.Server.Version}}' 2>/dev/null))"
    else
        warn "docker installed but engine not reachable (start Docker Desktop; WSL distro restart may need: wsl --terminate docker-desktop)"
    fi
else
    warn "docker CLI not found"
fi

section "Vehicle lab offline tests"
UDS_LAB_DIR="$PROJECT_ROOT/labs/vehicle/uds-basic"
run_offline_test() {
    local script="$1" label="$2"
    if [ ! -f "$script" ]; then warn "$label test not found"; return; fi
    if [ -z "$PYTHON_BIN" ]; then fail "$label skipped: no Python"; return; fi
    if out=$("$PYTHON_BIN" "$script" 2>&1); then
        count=$(printf '%s\n' "$out" | sed -n 's/^Ran \([0-9]\+\) tests\?.*/\1/p' | tail -1)
        ok "$label tests passed (${count:-?} tests)"
    else
        fail "$label tests failed (python $script)"
    fi
}
run_offline_test "$UDS_LAB_DIR/tests/test_ecu_unit.py" "UDS unit"
run_offline_test "$UDS_LAB_DIR/tests/test_keygen_unit.py" "UDS keygen unit"
if [ -f "$UDS_LAB_DIR/tests/test_e2e_vcan.py" ]; then
    if ip link show vcan0 >/dev/null 2>&1; then
        echo "      e2e available: bash labs/vehicle/uds-basic/tests/run_tests.sh all"
    else
        warn "vcan0 missing, UDS e2e not runnable"
    fi
fi

# ---------------- extended: 真实闭环（较慢） ----------------
if [ "$EXTENDED" = "1" ]; then
    section "EXTENDED: UDS / ISO-TP e2e (vcan0)"
    if [ -x "$UDS_LAB_DIR/tests/run_tests.sh" ]; then
        if bash "$UDS_LAB_DIR/tests/run_tests.sh" all >/tmp/hc_uds.log 2>&1; then
            ok "uds-basic run_tests all PASS ($(grep -cE 'OK|PASS' /tmp/hc_uds.log) ok-lines)"
        else
            fail "uds-basic run_tests all FAILED (see /tmp/hc_uds.log)"
        fi
    else
        warn "uds run_tests.sh not found"
    fi

    section "EXTENDED: CAN capture -> save -> replay"
    CAP="$PROJECT_ROOT/labs/vehicle/can-tools/capture/capture_lab.sh"
    if [ -x "$CAP" ] || [ -f "$CAP" ]; then
        if bash "$CAP" >/tmp/hc_cap.log 2>&1 && grep -q 'CAPTURE_LAB=PASS' /tmp/hc_cap.log; then
            ok "CAN log/BLF/PCAP capture-replay PASS"
        else
            fail "capture lab FAILED (see /tmp/hc_cap.log)"
        fi
    else
        warn "capture_lab.sh not found"
    fi

    section "EXTENDED: YARA"
    YS="$PROJECT_ROOT/labs/yara-basic/scan.sh"
    if [ -f "$YS" ]; then
        if bash "$YS" >/tmp/hc_yara.log 2>&1 && grep -qiE 'YARA_RESULT=PASS|PASS' /tmp/hc_yara.log; then
            ok "YARA TP/TN scan PASS"
        else
            fail "YARA scan FAILED (see /tmp/hc_yara.log)"
        fi
    else
        warn "yara scan.sh not found"
    fi
    echo "      (fuzz / AFL 耗时长，不在此运行；见 labs/vehicle/can-tools/fuzz 与 labs/fuzzing/afl-demo)"
fi

echo
echo "========================================"
echo " Summary"
echo "========================================"
echo "PASS: $PASS"
echo "WARN: $WARN"
echo "FAIL: $FAIL"
echo
[ "$FAIL" -gt 0 ] && exit 1
exit 0
