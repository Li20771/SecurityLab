#!/usr/bin/env python3
"""
UDS Basic —— 教学用途的 Seed / Key 算法

======================================================================
重要声明（务必先读）
======================================================================

本模块是**纯教学用途**的示例实现，不是任何真实厂商算法：

1. 不使用任何真实 OEM / Tier1 的 seed-key 算法。
   真实车辆的 SecurityAccess 算法属于厂商机密，各车型不同。
2. 不使用任何真实 ECU 密钥。
   `SECRET` 是一个**公开的固定常数**，写在源码里，任何人都能读到。
   这是**刻意设计**：本实验的目的是理解协议流程与失败状态机，
   而不是提供任何真实保护能力。
3. **不具备密码学安全性**：
   - 算法是线性同余生成器（LCG）混合，可被逆向
   - 无抗时序攻击设计（比较使用普通 ==）
   - seed 由可预测的计数器派生，不具备随机性
4. `SECRET` 参与 key 派生，因此**真实场景中这个模块绝不能分发给诊断客户端**；
   本实验为了让客户端与测试能独立算出期望 key，刻意公开它。

======================================================================
本实验的 seed / key 长度约定
======================================================================

本实验**自行约定**：

    seed = 4 bytes
    key  = 4 bytes

请注意：ISO 14229-1 并未把"seed 与 key 必须等长、且必须为 4 字节"
规定为普遍强制要求。标准只要求 Tester 发送的 key 长度与 ECU
在 RequestSeed 中返回的 seed 长度相匹配，具体长度由 ECU 决定
（常见实现有 2、4、8、16 字节等）。

本实验选择 4 字节，纯粹是为了教学与测试方便：

- 4 字节 seed 使 `67 01 <seed>` 共 6 字节，可装入 ISO-TP 单帧（≤7 字节），
  因此 SecurityAccess 的测试不必纠结于分帧细节
- 断言直观，失败定位容易

======================================================================
算法概要
======================================================================

seed 生成（每次请求不同）：

    raw = (SEED_BASE + counter * 0x9E3779B1 + level * 0x85EBCA6B) & 0xFFFFFFFF

key 派生（由 seed 确定性决定）：

    value = seed

    重复 4 轮：
        value = (value * 0x41C64E6D + 0x3039 + SECRET) & 0xFFFFFFFF
        value ^= value >> 13
        value  = rotl32(value, 7)

    key = value ^ seed

--------------------------------------------------------------------------------

本模块**不依赖 CAN / ISO-TP / vcan0**，也不依赖任何第三方库，
因此可以被 ECU、诊断客户端和测试共同 import。
"""

# ======================================================================
# 算法参数
# ======================================================================

#: 教学用的公开固定常数。**不是真实密钥**，写在源码中供任何人查阅。
SECRET = 0x5A17C0DE

#: seed 序号基数，使 seed 看起来不像从 0/1 开始
SEED_BASE = 0x1000

#: 本实验自定义：seed 与 key 均为 4 字节（非 ISO 14229 普遍强制要求）
SEED_LENGTH = 4
KEY_LENGTH = 4

#: LCG 参数（取自经典数值，仅为混合效果，无密码学意义）
_LCG_MULTIPLIER = 0x41C64E6D
_LCG_INCREMENT = 0x3039

#: seed 序号混合常数（黄金比例 / MurmurHash 常数）
_SEED_COUNTER_MIX = 0x9E3779B1
_SEED_LEVEL_MIX = 0x85EBCA6B

#: key 派生的混合轮数
_MIX_ROUNDS = 4

_MASK32 = 0xFFFFFFFF


def _rotl32(value: int, shift: int) -> int:
    """32 位循环左移。"""
    value &= _MASK32
    return ((value << shift) | (value >> (32 - shift))) & _MASK32


# ======================================================================
# seed 生成
# ======================================================================


def generate_seed(counter: int = 0, level: int = 0x01) -> bytes:
    """
    生成一个 4 字节 seed。

    纯函数：给定相同的 (counter, level) 永远返回相同的 seed。
    seed 的"每次请求变化"由调用方递增 counter 实现
    （见 uds_server.EcuState.seed_counter）。

    :param counter: seed 序号，每次 RequestSeed 递增
    :param level:   安全等级（本实验只用 0x01）
    :return:        4 字节 seed

    >>> generate_seed(0, 0x01) == generate_seed(0, 0x01)
    True
    >>> generate_seed(0, 0x01) == generate_seed(1, 0x01)
    False
    >>> len(generate_seed(0, 0x01))
    4
    """
    raw = (
        SEED_BASE
        + counter * _SEED_COUNTER_MIX
        + level * _SEED_LEVEL_MIX
    ) & _MASK32

    return raw.to_bytes(SEED_LENGTH, "big")


def seed_is_valid(seed: bytes) -> bool:
    """
    判断 seed 是否为合法的 4 字节值。

    UDS 约定：ECU 已经解锁时，RequestSeed 可以返回全 0 seed
    （ISO 14229 中这一状态称为 ISOSAEReserved）。
    本实验不使用全 0 seed，因此全 0 视为"已解锁"的信号由调用方处理。
    """
    return isinstance(seed, (bytes, bytearray)) and len(seed) == SEED_LENGTH


# ======================================================================
# key 派生与校验
# ======================================================================


def derive_key(seed: bytes, secret: int = SECRET) -> bytes:
    """
    由 seed 确定性地派生 4 字节 key。

    纯函数：相同 seed 与 secret 永远得到相同 key。

    :param seed:   4 字节 seed
    :param secret: 教学用固定常数（可注入，便于测试与演示）
    :return:       4 字节 key
    :raises ValueError: seed 长度不是 4 字节

    >>> seed = generate_seed(0, 0x01)
    >>> derive_key(seed) == derive_key(seed)
    True
    >>> len(derive_key(seed))
    4
    """
    if not seed_is_valid(seed):
        raise ValueError(
            f"seed 必须是 {SEED_LENGTH} 字节，实际收到 "
            f"{len(seed) if isinstance(seed, (bytes, bytearray)) else type(seed).__name__}"
        )

    value = int.from_bytes(seed, "big")

    for _ in range(_MIX_ROUNDS):
        value = (value * _LCG_MULTIPLIER + _LCG_INCREMENT + secret) & _MASK32
        value ^= value >> 13
        value = _rotl32(value, 7)

    # 与 seed 异或，使 key 与 seed 强相关
    value ^= int.from_bytes(seed, "big")
    value &= _MASK32

    return value.to_bytes(KEY_LENGTH, "big")


def verify_key(seed: bytes, key: bytes, secret: int = SECRET) -> bool:
    """
    校验 key 是否与 seed 匹配。

    注意：为了教学演示的简单性，这里使用普通的 `==` 比较，
    **不具备抗时序攻击能力**。真实 ECU 应使用恒定时间比较。

    :param seed:   4 字节 seed
    :param key:    待校验的 key
    :param secret: 教学用固定常数
    :return:       True 表示匹配
    """
    if not isinstance(key, (bytes, bytearray)) or len(key) != KEY_LENGTH:
        return False

    if not seed_is_valid(seed):
        return False

    return derive_key(seed, secret) == bytes(key)


if __name__ == "__main__":
    # 手动观察用：打印前若干个 seed 及其对应 key
    print(f"SECRET = 0x{SECRET:08X}  (教学用公开常数，非真实密钥)")
    print(f"seed/key 长度 = {SEED_LENGTH} / {KEY_LENGTH} 字节（本实验自定义）")
    print()

    for index in range(4):
        sample_seed = generate_seed(index, 0x01)
        sample_key = derive_key(sample_seed)
        print(
            f"counter={index}  seed={sample_seed.hex(' ')}  "
            f"key={sample_key.hex(' ')}"
        )
