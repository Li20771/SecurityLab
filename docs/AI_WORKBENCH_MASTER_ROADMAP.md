# AI Security Research Workbench — Master Roadmap

> **定位：** 独立于 SecurityLab 的 AI 基础设施长期规划。
> **当前状态：** PAUSED。
> **重要：** 本路线图是规划基线，不是自动执行指令。任何实现、安装、插件开发或模型切换，都需要后续明确任务并按设计评审执行。

## 1. 项目目标

把 DSH 建设成适合个人网络安全、车联网安全、逆向和固件研究的主 AI 工作平台；同时保留 Codex 作为独立第二 Agent。

目标终局：

```text
                         用户
                          │
                   总体设计 / 审核
                          │
             ┌────────────┴────────────┐
             │                         │
            DSH                      Codex
          主 Agent                第二 Agent
             │                         │
      ┌──────┼───────┐                 │
      │      │       │                 │
  DeepSeek  MiMo   专用插件         国产模型
      │      │       │                 │
      └──────┴───────┘                 │
             │                         │
             └──────────┬──────────────┘
                        ▼
                安全研究工具层
                        │
             ┌──────────┼──────────┐
             │          │          │
            Git       Terminal     MCP
             │          │          │
             └──────────┼──────────┘
                        ▼
                   SecurityLab
```

## 2. 核心架构原则

1. DSH 是平台和 Agent Harness，不等同于某一个模型。
2. Codex 保持独立，不作为 DSH 内部子 Agent。
3. DeepSeek 与 MiMo 是 DSH 的初始模型提供者；以后可更换。
4. 项目上下文必须来自仓库和版本化文档，不依赖聊天记忆。
5. 安全边界尽量由工具层、插件和执行前检查强制，而不是只依赖模型提示词。
6. 模型输出是假设；源码、测试、运行结果、Git 和 CI 才是证据。
7. AI 工作bench 与 SecurityLab production 解耦。
8. 多模型接入不等于自动路由；自动路由必须有明确策略和可追溯记录。
9. 高风险操作需要比普通分析更严格的授权。
10. 优先使用官方插件/provider 接口，不默认 fork 或修改 DSH 核心。

## 3. 上游基线（实施前必须重新核对）

本路线图创建时核对到的公开资料：

- DSH 最新公开预发布版本：v0.1.7-rc.2（2026-09-24）。DSH 仍处于快速迭代阶段。
- DSH Creator mode 已转向 Plugin Manager 持久化插件。
- DSH 已持续增加 Subagent、Background Task、MCP、模型/provider 路由等 Agent 能力。
- DeepSeek-V4.1-Flash 当前 API 模型名为 `deepseek-flash`。
- MiMo-V2.6-Pro / Flash 提供 OpenAI 与 Anthropic 兼容接口，并支持 1M context。
- Codex 当前配置支持自定义 `model_providers` 与 profiles，可连接第三方 OpenAI-compatible provider。

这些都是版本敏感事实。真正开始实施时必须重新检查官方文档和当前版本，不能把本节当成永久 API 保证。

参考：

- DSH Releases: https://github.com/deepseek-ai/deepseek-harness/releases
- DeepSeek API Changelog: https://api-docs.deepseek.com/updates/
- MiMo V2.6 Pro: https://mimo.mi.com/models/en-US/mimo-v2.6-pro
- MiMo Pricing: https://mimo.mi.com/docs/en-US/pricing
- Codex config: https://github.com/openai/codex/blob/main/codex-rs/config/src/config_toml.rs

## 4. AI-00：需求与架构冻结

状态：NOT STARTED。

需要冻结：

- DSH = 主 Agent 平台。
- Codex = 独立第二 Agent。
- DSH 初始双模型 = DeepSeek V4.1 + MiMo V2.6。
- SecurityLab = workspace / research target，而不是 AI framework。
- 首选插件化定制，不直接 fork DSH。
- provider、model、routing policy 独立于 SecurityLab production。
- 凭据只能存在本地安全配置中，不得进入 Git。

交付物：Implementation Design / Architecture Freeze。

## 5. AI-01：DSH 稳定基线

目标：在定制之前建立一个可回滚的 DSH baseline。

任务：

- 记录 exact DSH version。
- 建立 stable / experimental profile 隔离。
- 验证 workspace、terminal、session、tool、MCP、model switching。
- 记录当前已知 upstream limitations。
- 保存升级前可恢复配置。

退出条件：AI 工作bench可以回滚到已知可用 DSH baseline，且不修改 SecurityLab。

## 6. AI-02：DSH 模型层

### DeepSeek

初始模型：`deepseek-flash`（当前 DeepSeek-V4.1-Flash API 名称）。

验证：

- 对话
- 多轮
- 代码生成
- tool calls
- 长上下文
- streaming
- 错误恢复

### MiMo

初始模型：

- `mimo-v2.6-pro`
- `mimo-v2.6-flash`
- UltraSpeed 仅在需要时评估

验证：

- 对话 / 多轮
- coding
- tool calls
- 1M context
- multimodal input
- streaming
- reasoning / tool-call compatibility
- cost / latency

原则：不做脱离实际工作流的“谁更强”结论，以真实任务 benchmark 为准。

## 7. AI-03：SecurityLab Context Plugin

目标：解决跨会话项目上下文丢失问题。

建议暴露结构化信息：

```text
project
main_sha
current_branch
current_phase
current_oq
roadmap_state
frozen_decisions
security_boundaries
deferred_items
important_history
```

当前 SecurityLab 关键上下文示例：

- C3 = COMPLETE / EXIT PASS
- Phase 4-A0 = COMPLETE
- Phase 4-A1 = PAUSED
- PR #55 = historical A4 BLOCKED evidence

状态必须从仓库真实文件 / Git 可核实来源推导，不能靠模型记忆补全。

## 8. AI-04：Security Guard Plugin

这是整个 Workbench 的核心安全层之一。

### Git

- 默认禁止直接修改 main。
- 写入前检查 branch。
- history rewrite 需要明确授权。
- remote push / merge 属于高影响操作。
- destructive git commands 需要更高等级授权。

### 车联网

- SecurityLab 默认只允许 vcan0。
- 非 vcan0 的车联网实验默认拒绝。
- 真实 CAN / ECU / vehicle 默认拒绝。
- 不允许模型把 synthetic ECU 描述为真实 ECU。

### 系统

- sudo / /etc / /usr / WSL / Docker 全局配置等提高权限级别。
- 不允许凭据进入日志。
- API key、Cookie、SSH private key 永不写入 Git。

### 测试

- 识别 vcan0 exclusive tests。
- 防止并发占用 vcan0。
- skipped / blocked 不得被包装成 PASS。

## 9. AI-05：Evidence / Audit Plugin

目标：把 Agent 工作变成可追踪的研究证据链。

建议记录：

```text
Experiment ID
Task
Agent
Provider
Model
Workspace
Base SHA
Branch
Tool calls
Changed files
Tests
Results
Git diff
Human approvals
Final status
```

证据原则：

- AI 输出 = hypothesis。
- executable tests = evidence。
- Git diff = mutation evidence。
- CI run = CI evidence。
- skipped / blocked = 保持原状态。
- 历史报告不得为后验结果而改写。

## 10. AI-06：Security Research Tools Plugin

把常用研究动作逐步变成窄而明确的工具，而不是全部暴露成无限 shell。

候选能力：

- Git / GitHub
- repository inspection
- pytest / project tests
- ruff / mypy
- vcan checks
- CAN / ISO-TP / UDS
- replay / capture
- QEMU
- GDB / GDB-multiarch
- Ghidra
- AFL++
- YARA
- firmware analysis

每个工具明确说明：inputs、scope、mutation level、vcan requirement、result semantics。

## 11. AI-07：Model Router v1

第一版采用 rule-based routing，不让模型自由选择无限 provider。

初步策略形态：

```text
简单 / 高频任务              → MiMo Flash
复杂 repo / 安全审计 / 长任务 → DeepSeek V4.1
多模态任务                   → MiMo V2.6
需要独立第二意见             → Codex
```

这是示例 policy shape，不是永久 winner 结论。

要求：

- provider/model 必须记录。
- router 只能从受限 allowlist 选择。
- 高影响操作可以强制指定模型。
- 不允许“模型自己决定模型且没有审计记录”。

## 12. AI-08：Codex Second Agent

Codex 独立于 DSH。

职责：

- 独立代码 review
- 独立安全 review
- alternative implementation
- second-opinion analysis
- 对 DSH 结论进行独立复核

使用 Codex 官方支持的 custom `model_providers` / profiles。

不修改 Codex core，除非未来出现明确的、独立的需求。

## 13. AI-09：Codex 国产模型 Provider Layer

候选对象：提供稳定 OpenAI-compatible API 的国产 coding / reasoning 模型。

资格条件：

- API compatibility
- reliable tool calling
- streaming
- long context
- predictable errors
- acceptable latency
- acceptable cost
- acceptable availability

不得仅根据宣传 benchmark 认定可用。

## 14. AI-10：Cross-Agent Review

目标：建立真正的第二意见机制。

```text
Task
 ├── DSH analysis
 ├── Codex independent analysis
 ├── executable verification
 └── human decision
```

两个 Agent 的互相同意不是证据。最终以源码、测试、运行结果、Git、CI 和实验记录为准。

## 15. AI-11：AI Security Benchmark

使用真实 SecurityLab 任务建立长期 benchmark。

### Repository understanding

- architecture discovery
- execution path tracing
- state transition discovery
- dependency analysis

### Security review

- authorization boundary
- Gateway policy
- handler/provider seam
- state-machine edge cases
- UDS access-control

### Coding

- scoped implementation
- tests
- no regression
- no forbidden changes

### Agent behavior

- tool correctness
- tool-loop rate
- branch correctness
- test interpretation
- long-task completion

### Engineering

- diff quality
- Git discipline
- reproducibility
- evidence quality

指标：

```text
task completion
code correctness
security finding rate
tool-call success
tool-loop rate
test pass rate
wrong-edit rate
human intervention count
token usage
cost
latency
```

## 16. AI-12：Model Routing v2

只在 benchmark 数据形成后推进。

可使用：

- task type
- context size
- tool complexity
- historical success rate
- latency
- cost
- human intervention rate
- failure risk

router 仍必须受到 allowlist 和安全策略约束。

## 17. AI-13：Long-running Agent Workflows

目标：支持长时间研究任务进入后台并可靠恢复。

适用候选：

- large repo audit
- full offline regression
- firmware analysis
- Ghidra batch
- fuzz / replay
- documentation consistency checks

必须具备：

- timeout
- progress
- cancellation
- resource limits
- recoverable failures
- correct skipped/blocked semantics

## 18. AI-14：Long-term AI Workbench

最终形态：

```text
DSH
 + Context
 + Security Guard
 + Evidence / Audit
 + Security Tools
 + Model Router
 + Subagents
 + Codex Second Agent
 + Benchmark
 = AI Security Research Workbench
```

目标是长期支持：

- repeatable research workflows
- model/provider replacement
- independent second opinions
- auditable evidence
- cross-session continuity
- workspace portability
- safe SecurityLab integration

## 19. 与 SecurityLab 的边界

AI Workbench 与 SecurityLab 保持两个概念边界。

### AI Workbench owns

- Agent configuration
- model/provider configuration
- plugins
- routing policy
- AI benchmark
- evidence metadata
- AI-specific scripts

### SecurityLab owns

- UDS / CAN / Gateway experiments
- virtual ECU
- security tests
- project architecture
- project roadmap
- experiment evidence

AI 功能不应该为了方便而直接改 SecurityLab production。

## 20. 持久化上下文策略

不能依赖聊天会话记忆。

优先级：

1. Git repository
2. Master Roadmap
3. Capability Inventory
4. Design Freeze documents
5. Exit reports
6. Git state

AI-side cache 可以存在，但必须可丢弃、可重建。

## 21. 权限模型

建议按影响程度分层：

```text
read < analyze < test < modify < system-change < remote-change
```

影响越高，越需要显式审批与证据。

特别禁止：

- secret leakage
- silent main modification
- unapproved Git history rewrite
- real CAN / ECU / vehicle actions
- skipped tests reported as passed
- model confidence used as substitute for evidence

SecurityLab 的 `AGENTS.md` 继续作为仓库侧安全和 Git 规则的权威依据。

## 22. DSH 版本策略

DSH 目前处于快速迭代 / pre-release 阶段，因此：

- exact version 必须记录。
- stable / experimental profile 分离。
- 升级先实验后迁移。
- 避免依赖 undocumented internals。
- 优先使用官方 Plugin Manager / plugin API。
- 保留 rollback。

## 23. 总执行顺序

```text
AI-00  Requirements / Architecture Freeze
AI-01  DSH Baseline
AI-02  DeepSeek + MiMo model layer
AI-03  SecurityLab Context Plugin
AI-04  Security Guard Plugin
AI-05  Evidence / Audit Plugin
AI-06  Security Research Tools Plugin
AI-07  Model Router v1
AI-08  Codex Second Agent
AI-09  Domestic Models for Codex
AI-10  Cross-Agent Review
AI-11  AI Security Benchmark
AI-12  Model Routing v2
AI-13  Long-running workflows
AI-14  Long-term Workbench
```

这是依赖顺序，不代表每一项必须马上实施。

## 24. 当前状态

```text
AI Workbench roadmap       = COMPLETE
AI-00                     = NOT STARTED
Implementation             = PAUSED
DSH customization          = NOT STARTED
DeepSeek integration       = NOT STARTED
MiMo integration           = NOT STARTED
Codex integration          = NOT STARTED
SecurityLab changes        = NONE
```

恢复执行时，从 **AI-00 Requirements / Architecture Freeze** 开始。

**Roadmap is a planning baseline, not an automatic execution trigger.**

## 25. Non-goals

- 不因为 benchmark 结果就自动替换主模型。
- 不把 DSH fork 作为默认定制路径。
- 不把 Codex 合并成 DSH 内部 Agent。
- 不让模型拥有无限工具或无限权限。
- 不把 SecurityLab 变成 AI framework。
- 不因 AI Workbench 而扩大 SecurityLab 到真实 CAN / ECU / vehicle。
- 不自动完成远程 merge。

## 26. Change policy

路线图只记录架构、阶段、状态、依赖和长期边界。

具体实现应进入单独 branch / design / PR；临时 bug、一次性命令和实验结果不要堆进本文件。
