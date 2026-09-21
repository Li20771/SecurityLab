/*
 * SecurityLab SYNTHETIC ECU demo program
 * ----------------------------------------
 * This is NOT real vehicle firmware. It is a hand-built teaching target for
 * the firmware-analysis pipeline (extraction -> ELF identification ->
 * strings/credential hunting -> QEMU user-mode execution).
 *
 * It is compiled statically for AArch64 so it can run directly under
 * qemu-aarch64 without the synthetic rootfs providing glibc/ld.
 *
 * Build version is injected with -DFW_VERSION_STR="..." so that v1/v2
 * firmware images differ and can be used for firmware-diff exercises.
 */
#include <stdio.h>

#ifndef FW_VERSION_STR
#define FW_VERSION_STR "SYN-ECU v1.0"
#endif

/* Intentionally fake, non-secret marker strings used in the
 * credential-hunting exercise. These are NOT real credentials. */
static const char DIAG_BANNER[] = "[SYN-ECU] diagnostic service ready (UDS 0x10/0x11/0x22/0x27)";
static const char CONFIG_PATH[] = "/etc/syn_ecu/config.ini";

int main(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    printf("[SYN-ECU] boot: %s\n", FW_VERSION_STR);
    printf("[SYN-ECU] loading %s\n", CONFIG_PATH);
    printf("%s\n", DIAG_BANNER);
    printf("[SYN-ECU] CAN iface=%s\n", "vcan0");
    return 0;
}
