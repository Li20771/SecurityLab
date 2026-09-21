/*
 * SecurityLab YARA teaching rules - SYNTHETIC indicators only.
 * These match hand-made markers used in the lab exercises; they are NOT
 * signatures of real malware. Used to prove the rule -> scan -> result loop.
 */

rule SecurityLab_Synthetic_Beacon
{
    meta:
        author = "SecurityLab"
        description = "Detects the synthetic beacon marker used in YARA exercises"
        date = "2026-09-21"
        synthetic = true
    strings:
        $marker = "SYNMALWARE_C2_BEACON" ascii
        $url    = "http://syn-c2.invalid/beacon" ascii
    condition:
        any of them
}

rule SecurityLab_Synthetic_EcuFirmware
{
    meta:
        author = "SecurityLab"
        description = "Identifies the synthetic ECU firmware/diagnostic markers"
        date = "2026-09-21"
        synthetic = true
    strings:
        $magic = { 41 46 4C }                       // "AFL"
        $banner = "SYN-ECU" ascii
        $uds = "UDS 0x10/0x11/0x22/0x27" ascii
        $version_re = /SYN-ECU v[0-9]+\.[0-9]+/
    condition:
        2 of them
}
