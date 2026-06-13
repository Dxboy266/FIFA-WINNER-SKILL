# Agent Dashboard And Analysis Refactor Plan

## Goal

Make AI Octopus Paul closer to a reusable matchday analysis agent: stronger information intake, thicker prediction reasoning, static dashboard-first presentation, and clean agent-to-agent invocation.

## Reference Mapping

| Reference | Pattern To Adopt | Local Work |
|---|---|---|
| `ZhangCraigXG/work-cup-2026` | Coach-room mindset: schedule, ranking, team data, player status, injuries, suspensions, group outlook, opponent-by-opponent thinking. | Use `matchday_intelligence_briefing.py` before prediction. Add availability, travel, weather, and opponent context as visible evidence sections. |
| `Crain99/worldcut-2026` | Page-first match cards, visible analysis history, SQLite for query/history, mobile-readable dashboard. | Use `prediction_visual_dashboard.py` as the primary shareable output. Keep SQLite as an index over locked JSON. |

Do not adopt betting recommendations, bankroll simulation, stake sizing, or guaranteed-win language.

## Current Decisions

- JSON/Markdown are canonical.
- SQLite is a query/index layer for matches, predictions, analysis layers, evaluations, root causes, and dashboard stats.
- Dashboard/page output is the preferred promotional artifact.
- Report and poster prompts are retained as optional tools.
- Tianji overlay is part of the entertainment model at 40%.
- Tianji calculations use match-local venue time when known; user-facing output can display converted times separately.
- Root `SKILL.md` is removed. Canonical source skill is `skills/fifa-winner-skill/SKILL.md`.

## Analysis Layer Target

Each prediction should expose structured layers instead of one shallow paragraph:

- Evidence integrity: source tiers, missing context, freshness.
- Fundamentals: ranking, squad depth, recent form, historical proxy.
- Availability: injuries, suspensions, likely XI, player status.
- Venue context: weather, altitude, travel distance, rest days, climate adaptation.
- Matchup: tactical friction, set pieces, transition risk, goalkeeper/defensive fragility.
- Market track: odds snapshot, source quality, divergence from model.
- Tianji overlay: match-local time, 40% entertainment track, never overriding facts.
- Scenario tree: base case, counter case, draw case, second-half leading/level/trailing branches.
- Score layer: scoreline distribution, clean-sheet probability, total-goals confidence.
- Adversarial review: what would break the pick.

## Data Source Roadmap

P0:

- Consume both `late_news` and legacy `news` in injury extraction.
- Keep result direction, exact score, and total-goals evaluation separate.
- Add canonical prediction registry per match to avoid evaluating stale artifacts.
- Add scoreline distribution and clean-sheet probability.
- Connect referee/card/penalty/set-piece risk to goal distribution.

P1:

- Stadium weather by venue and kickoff-local time.
- Final 24-hour news monitor with source tier, timestamp, team mapping, and freshness.
- Injury/suspension monitor from national FA pages, reputable media, manual entry, and optional APIs.
- Team travel distance to stadium, rest days, and travel direction.
- Home-country temperature baseline versus venue kickoff temperature.
- Player adaptation signals: club climate, altitude/heat exposure, heat/humidity history, recent travel.
- Confirmed lineup gate before confidence can move to high.
- Market quality gate so mock odds cannot upgrade confidence.

P2:

- Dashboard route per edition and date.
- Static export bundle for runtime agents.
- Optional A2A/MCP/OpenAI Agents SDK wrapper over the existing card/catalog.
- Automatic post-match review that creates root causes and corrective actions.

## Verification Gates

- `rg` should not find stale 85/15 or Beijing-time Tianji contract in active docs/scripts.
- JSON agent card and tool catalog must parse.
- `py_compile` must pass for changed scripts.
- Unit tests must pass.
- `worldcup_github_readiness_auditor.py write --edition 2026 --root .` must run after docs/tool updates.
- Dashboard artifact must be generated from locked prediction/evaluation files, not from hand-written copy.

## Public Copy Rule

Never say "predicted correctly" unless exact-score success is meant and proven. Use:

- direction hit
- exact score hit
- total-goals hit
- partial hit
- full hit

This prevents a direction-only hit from being promoted as a full prediction win.
