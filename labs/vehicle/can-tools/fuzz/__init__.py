#!/usr/bin/env python3
"""
Stateful ISO-TP fuzzing harness（Phase 4X）

模块划分::

    isotp_corpus.py    纯数据：seed corpus、状态模型常量、payload 常量
    isotp_mutators.py  纯函数：各维度 mutator（不发送、不等待、不抓包）
    case_schema.py     testcase schema、校验、oracle 分类、replay identity
    isotp_fuzz.py      执行引擎：ECU 进程隔离、raw CAN 注入/观察、oracle
    replay.py          精确重放已保存的 testcase

边界（Phase 4X-P1 §3）::

    interface  = vcan0（固定）
    inject_id  = 0x7E0
    observe_id = 0x7E8
    第一版不做 CAN ID fuzzing，不接真实 CAN 接口

本包**不**导入 ECU 侧任何模块（``ecu`` / ``uds_server`` / ``uds_types``），
也不复用 ECU 的 socket —— fuzzer 始终是独立 tester。
"""

__all__ = [
    "case_schema",
    "isotp_corpus",
    "isotp_fuzz",
    "isotp_mutators",
]
