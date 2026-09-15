# Documentation Index

The current implemented system is described in [GLOBAL_DEV_SPEC.md](../GLOBAL_DEV_SPEC.md).
Classification and maintenance rules live in [AGENTS.md](AGENTS.md).

Dated documents preserve the decisions and evidence of their original scope.
Their status and code examples may describe an older system; check current code
and the global specification before implementation or operation. Directory
placement does not imply approval, implementation, or current validity.

## design (9)

Architecture, specifications, interfaces, and invariants.

- [ChemQA Two-Layer Artifact and Protocol Flow Design](design/2026-04-28-chemqa-two-layer-artifact-protocol-design.md)
- [ChemQA Phase-Scoped Agent Driver Design](design/2026-04-29-chemqa-phase-scoped-agent-driver-design.md)
- [Benchmark Agent Workspace Attempt Isolation Specification](design/2026-07-14-benchmark-agent-workspace-attempt-isolation-design.md)
- [Verifier-Grounded Benchmark 与 OpenClaw Single-LLM 标准接入使用规格](design/2026-07-15-verifier-grounded-openclaw-single-llm-integration-usage-spec.md)
- [Benchmark Attempt Workspace Behavior and Contamination Adjudication Specification](design/2026-07-16-benchmark-attempt-workspace-behavior-and-adjudication-spec.md)
- [Benchmark Forbidden Path Root Containment Specification](design/2026-07-16-benchmark-forbidden-path-root-containment-spec.md)
- [Benchmark Audit Error Allowlist and Cancellation Specification](design/2026-07-23-benchmark-audit-error-allowlist-and-cancellation-spec.md)
- [Skill Runner Deadline, Process Group, and Async Skill Specification](design/2026-07-23-skill-runner-deadline-process-group-async-spec.md)
- [Benchmark Infra 单一 LLM Attempt 容器化改动计划与接口设计](design/2026-09-08-benchmark-single-llm-containerization-plan.md)

## plan (13)

Implementation sequences and diagnosis plans.

- [Benchmark Architecture Refactor Implementation Plan](plan/2026-04-23-benchmark-refactor.md)
- [Benchmark Unattended Completion Diagnosis Plan](plan/2026-04-25-benchmark-unattended-completion-diagnosis.md)
- [Chem Provider Skills Implementation Plan](plan/2026-04-27-chem-provider-skills-development.md)
- [ChemQA Evaluable Answer Recovery Implementation Plan](plan/2026-04-27-chemqa-evaluable-answer-recovery-implementation.md)
- [ChemQA Phase-Scoped Agent Driver Implementation Plan](plan/2026-04-29-chemqa-phase-scoped-agent-driver.md)
- [OpenClaw Healthcheck Repair Implementation Plan](plan/2026-04-29-openclaw-healthcheck-repair-design.md)
- [ChemQA P0 Answer Projection and Revision Propagation Fix Plan](plan/2026-05-02-chemqa-p0-answer-projection-revision-fix.md)
- [Skill Injection Routing Architecture Repair Implementation Plan](plan/2026-05-07-skill-injection-routing-architecture-repair-plan.md)
- [Autonomous Skill Discovery and Audit Implementation Plan](plan/2026-05-08-autonomous-skill-discovery-audit-implementation-plan.md)
- [Benchmark Inputs and Runner Convergence Implementation Plan](plan/2026-05-09-benchmark-inputs-and-runner-convergence.md)
- [Benchmark Result Contract and Skill Runtime Health Implementation Plan](plan/2026-05-09-benchmark-result-contract-skill-runtime-health.md)
- [Benchmark Agent Workspace Attempt Isolation Implementation Plan](plan/2026-07-14-benchmark-agent-workspace-attempt-isolation.md)
- [Benchmark Attempt Workspace Behavior and Adjudication Implementation Plan](plan/2026-07-16-benchmark-attempt-workspace-behavior-and-adjudication.md)

## handoff (5)

Context and remaining work for a new maintainer.

- [OpenClaw Session Race 与历史 Timeout 误重试修复交接文档](handoff/2026-09-13-openclaw-session-race-timeout-fix-handoff.md)
- [OpenClaw Benchmark Bootstrap、Rescue Context 与路径投影修复交接文档](handoff/2026-09-14-openclaw-bootstrap-rescue-path-fix-handoff.md): GPT-5.6 SOL follow-up evidence and the approved implementation plan for bootstrap suppression, isolated rescue context, and safe path projection.
- [Benchmark Infra 迁移遗留问题交接文档](handoff/2026-09-11-benchmark-infra-open-issues-handoff.md)
- [Benchmark Infra 重构待修复问题交接文档](handoff/infra-fix-handoff.md)
- [Benchmark Runtime 架构优化问题交接文档](handoff/2026-09-15-benchmark-runtime-optimization-handoff.md)：只读扫描结论、优先级和分阶段重构交接方案。

## report (7)

Observed results, validation, and supporting evidence.

- [最新 8 道专家题及 qwen3.7-max 表现报告](report/2026-07-21-qwen3.7-max-latest-8-expert-tasks.md)
- [Benchmark Infra Follow-up Validation](report/2026-09-11-benchmark-infra-fix-validation.md)
- [Benchmark Infra Fix Validation](report/infra-fix-validation.md)
- [Provider DNS and Container Name Validation](report/2026-09-11-provider-dns-container-name-validation.md): DNS incident evidence, naming fix, and startup-check verification.
- [Container Proxy and DNS Follow-up](report/2026-09-11-container-proxy-dns-followup.md): upstream NODATA evidence and shared proxy configuration for preflight and attempts.
- [OpenClaw Session Race and Timeout Retry Fix Validation](report/2026-09-13-openclaw-session-race-timeout-fix-validation.md): outcome precedence, session ownership evidence, typed timeout/takeover diagnostics, Docker contracts, and Qwen acceptance.
- [OpenClaw Bootstrap, Rescue Context, and Path Projection Validation](report/2026-09-14-openclaw-bootstrap-rescue-path-fix-validation.md): configuration suppression, frozen rescue context, safe path projection, and validation limits.

## guide (1)

Operating instructions for existing capabilities.

- [Benchmark Dashboard 使用说明](guide/benchmark-dashboard-usage.md)

## research (1)

External evidence, feasibility, and candidate exploration.

- [Verifier-grounded open-generation chemical benchmark 目标性质调研报告](research/2026-05-26-verifier-grounded-chemical-benchmark-target-properties.md)
