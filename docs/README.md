# Docs index

Start here if you are new to the repo. Operational guides first, then history.

## Guides (read these)

| Doc | What it covers |
|---|---|
| [deployment.md](deployment.md) | Docker Compose setup, secrets, troubleshooting |
| [telegram-setup.md](telegram-setup.md) | Bot token, webhook secret, local tunnel |
| [observability.md](observability.md) | OpenTelemetry → Phoenix tracing, per-node spans, kill-switch |
| [demo-runbook.md](demo-runbook.md) | 5 demo scenarios for an SME customer |
| [feature-scorecard.md](feature-scorecard.md) | Feature-by-feature maturity score (1–5) |
| [codebase-map.md](codebase-map.md) | Request flow, 13 node, ngưỡng/config, 20 bảng, endpoint, eval commands, known gaps |
| [adr/](adr/) | Architecture Decision Records — model/provider choice, LangGraph, Telegram lib, embedding governance |

## Plans and research (why things look the way they do)

| Doc | What it covers |
|---|---|
| [upgrade-plan.md](upgrade-plan.md) → [v2](upgrade-plan-v2.md) → [v3](upgrade-plan-v3.md) → [v4](upgrade-plan-v4.md) | Successive upgrade plans: demo-ready → smarter/cheaper → CI/coverage/observability → production |
| [upgrade-plan-support-graph.md](upgrade-plan-support-graph.md) | Customer-support graph (second persona) |
| [agent-orchestration-2026-research.md](agent-orchestration-2026-research.md) | LangGraph orchestration vs 2026 trends |
| [wayfinder/](wayfinder/) | Research tickets + locked spec for the v3-0 agent effectiveness/resilience work |
| [break-down.md](break-down.md) | Feature breakdown derived from code |

## History

| Doc | What it covers |
|---|---|
| [specs/](specs/) | Original spec-kit specs 001–006 (infra, RAG eval, agent, HITL, memory, Telegram) |
| [project-log.md](project-log.md) | Running project log |
| [week1/](week1/) … [week4/](week4/) | Weekly build notes and reports |
| [../reports/](../reports/) | Scenario test reports, eval run checkpoints, architecture analysis |
