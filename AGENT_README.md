# AI Octopus Paul Agent Runtime Guide

This file is written for other runtime agents, such as Codex, Claude Code, Cursor agents, CI agents, or custom A2A orchestrators. Read this before calling the project scripts.

The short version: this repository exposes a World Cup entertainment-prediction agent. Use JSON reports as the canonical audit artifacts. Use SQLite only as a query/index layer. Never present the output as betting advice.

## Capability Card

| Field | Value |
|---|---|
| Agent name | AI Octopus Paul Predictor |
| Package / skill | `fifa-winner-skill` |
| Primary role | Build a World Cup knowledge base, collect evidence, generate pre-match entertainment predictions, produce report/poster prompts, and evaluate predictions after matches |
| Best callers | Codex, Claude Code, local CLI agents, CI jobs, A2A orchestrators |
| Canonical interface | Python CLI scripts in `scripts/` |
| Canonical state | `knowledge-base/<edition>/data/*.json` and report artifacts |
| Query index | `knowledge-base/<edition>/data/worldcup_<edition>.db` SQLite database |
| Machine-readable card | `knowledge-base/agent/AGENT_CARD.json` |
| Tool catalog | `knowledge-base/agent/TOOL_CATALOG.json` |
| Guardrails / handoffs / traces | `knowledge-base/agent/GUARDRAILS.md`, `knowledge-base/agent/HANDOFFS.md`, `knowledge-base/agent/TRACE_EVENTS.md` |
| Safety boundary | Entertainment only. Never betting, stake sizing, gambling advice, or guaranteed language |

## When To Use This Agent

Use this agent when the user asks for:

- World Cup edition initialization or knowledge-base setup.
- Official fixture, roster, ranking, or historical source ingestion.
- Daily pre-match entertainment predictions.
- Multi-layer match analysis using ranking, squad depth, rest/travel, evidence completeness, market signal, late news, referee context, and Tianji entertainment overlay.
- Shareable play cards, report prompts, or poster prompts.
- Post-match evaluation and confidence calibration.
- Portable export of the whole skill for another runtime.

Do not use this agent for:

- Betting recommendations.
- Stake sizing or bankroll decisions.
- "Guaranteed", "lock", "sure win", "稳赢", "稳胆", or similar claims.
- Scraping sources beyond their allowed use or rate limits.

## Install For Runtime Agents

### Standalone Repo

```bash
git clone <repo-url> FIFA-WINNER-SKILL
cd FIFA-WINNER-SKILL
python -m pytest -q
```

Use `--root .` on commands.

### Codex Skill Install

```bash
bash install_as_skill.sh
```

This installs the repository into:

```text
${CODEX_HOME:-$HOME/.codex}/skills/fifa-winner-skill
```

After install, Codex should read:

- `skills/fifa-winner-skill/SKILL.md`
- `AGENT_README.md`
- `knowledge-base/agent/AGENT_CARD.json`

### Claude Code / Generic Agent

Claude Code or another runtime can use this repo directly as a CLI tool. The recommended first read order is:

1. `AGENT_README.md`
2. `knowledge-base/agent/AGENT_CARD.json`
3. `knowledge-base/agent/TOOL_CATALOG.json`
4. `knowledge-base/agent/RUNBOOK.md`
5. `docs/runtime-agent-user-data-overlay-guide.md`
6. `knowledge-base/agent/GUARDRAILS.md`
7. `skills/fifa-winner-skill/SKILL.md`
8. `schema/daily-prediction-report.schema.json`

## Agent Design Alignment

This mini agent is intentionally aligned with common agent ecosystems without requiring a runtime server yet:

- A2A-style discovery: `AGENT_CARD.json` exposes identity, capabilities, skills, task states, handoff docs, and safety boundaries.
- MCP-style discovery: `TOOL_CATALOG.json` separates tools, resources, prompts, guardrails, handoffs, and trace events.
- OpenAI Agents SDK-style wrapper contract: tools map to CLI commands, handoffs map to task payloads, guardrails map to refusal and evidence rules, and traces map to lightweight events.
- Codex / Claude Code ergonomics: `RUNBOOK.md` gives an install-and-call path that does not require importing project internals.

Runtime server work is deferred. The contract is static and file-based today so another agent can install the repo, inspect the card/catalog, run CLI tools, and summarize artifacts safely.

## A2A Invocation Contract

All commands should be run from the repository root unless the caller intentionally sets another root.

```bash
python scripts/<tool>.py <command> --edition <edition> --root .
```

Required caller behavior:

1. Check whether `knowledge-base/<edition>/data/match-ledger.json` exists.
2. If missing, initialize the edition.
3. Check source readiness and prediction evidence status before making strong claims.
4. Sync external reference-source alignment when the caller asks whether this agent has been compared with other World Cup agents.
5. Add or refresh daily evidence before daily predictions when possible.
6. Generate predictions only for matches that have not started.
7. Treat pre-match reports as locked after generation.
8. Evaluate only after final scores are recorded.

## Tool Resource Prompt Discovery

Machine callers should read `knowledge-base/agent/TOOL_CATALOG.json` before invoking scripts. It contains:

- `tools`: CLI commands, inputs, outputs, idempotency, and safety profile.
- `resources`: canonical JSON/Markdown/SQLite paths that can be read after a run.
- `prompts`: report, poster, and compact-response prompt contracts.
- `guardrails`: safety and evidence rules that must be enforced by the caller.
- `handoffs`: task handoff types for prediction, evidence refresh, poster, and evaluation.
- `trace_events`: recommended event names for future runtime wrappers.

Schemas:

- `schema/agent-card.schema.json`
- `schema/agent-tool-catalog.schema.json`
- `schema/daily-prediction-report.schema.json`

## Handoff Contract

Use `knowledge-base/agent/HANDOFFS.md` when another runtime agent wants to delegate work. The shared states are:

```text
submitted | working | input_required | blocked | completed | failed | canceled
```

The main handoff types are:

- `prediction_requested`
- `evidence_refresh_needed`
- `poster_requested`
- `evaluation_requested`

The caller owns user conversation and final summarization. This repo owns artifacts, safety invariants, and deterministic report generation.

## Trace Contract

Use `knowledge-base/agent/TRACE_EVENTS.md` when wrapping the CLI in a long-running runtime. Recommended events include:

- `task.accepted`
- `tool.started`
- `tool.finished`
- `artifact.written`
- `guardrail.triggered`
- `prediction.locked`

Do not log secrets. Log artifact paths, blocker ids, evidence gaps, status, and safety-disclaimer state.

## Common Workflows

### 1. Initialize An Edition

```bash
python scripts/worldcup_edition_init.py init --edition 2026 --root .
```

Main outputs:

- `knowledge-base/2026/data/match-ledger.json`
- `knowledge-base/2026/raw/source-registry.json`
- `knowledge-base/2026/wiki/index.md`

### 2. Audit Evidence Readiness

```bash
python scripts/worldcup_source_readiness_auditor.py write --edition 2026 --root .
python scripts/worldcup_prediction_evidence_planner.py write --edition 2026 --root .
python scripts/sync_external_reference_sources.py write --edition 2026 --root .
```

Read:

- `knowledge-base/2026/data/source-readiness.json`
- `knowledge-base/2026/data/prediction-evidence-plan.json`
- `knowledge-base/2026/data/external-reference-sources.json`

If evidence is `partial` or `blocked`, keep that uncertainty visible in the final answer.

External reference alignment currently covers:

- `ZhangCraigXG/work-cup-2026`: useful as a coach-view skill workflow and Chinese source-lead map for schedule, group, team, player-status and rules checks. Treat it as T3 reference/design material, not an official match-fact source.
- `Crain99/worldcut-2026`: useful for Sporttery fixed-bonus source discovery, SQLite cache patterns, odds snapshots, prediction-history storage, and multi-tool intelligence design. Treat its static predictions as cross-check material only.

Do not import third-party project code into this agent. Register source leads, verify terms, and snapshot or fetch through this repo's own tools.

### 3. Add Matchday Evidence

```bash
python scripts/daily_evidence_input.py init --edition 2026 --date 2026-06-11 --root .
python scripts/worldcup_live_fetcher.py fetch-odds --edition 2026 --date 2026-06-11 --root .
python scripts/worldcup_live_fetcher.py fetch-news --edition 2026 --date 2026-06-11 --root .
```

Manual evidence tools are also available:

```bash
python scripts/daily_evidence_input.py add-injury --edition 2026 --date 2026-06-11 --team-code MEX --player-name "Player Name" --status doubtful --root .
python scripts/daily_evidence_input.py add-referee --edition 2026 --date 2026-06-11 --match-id 2026-GA-01 --name "Referee Name" --strictness high --root .
python scripts/daily_evidence_input.py add-odds --edition 2026 --date 2026-06-11 --match-id 2026-GA-01 --home-win 1.85 --draw 3.4 --away-win 4.5 --root .
```

### 4. Generate Predictions

Preferred daily runner:

```bash
python scripts/daily_prediction_runner.py run --edition 2026 --date 2026-06-11 --root .
```

Scoring model direct call:

```bash
python scripts/prediction_scoring_model.py predict --edition 2026 --date 2026-06-11 --root .
python scripts/prediction_scoring_model.py predict --edition 2026 --match-id 2026-GA-01 --root .
python scripts/prediction_scoring_model.py predict --edition 2026 --teams "Mexico,South Africa" --root .
```

Main output:

```text
knowledge-base/2026/data/reports/daily-predictions/<date>.json
```

Important fields for other agents:

- `predictions[].prediction`
- `predictions[].prediction.scoreline_distribution`
- `predictions[].prediction.clean_sheet_probability`
- `predictions[].prediction.venue_adaptation_context`
- `predictions[].prediction.result_confidence`
- `predictions[].prediction.score_confidence`
- `predictions[].prediction.total_goals_confidence`
- `predictions[].venue_adaptation_context`
- `predictions[].data_score`
- `predictions[].analysis_layers`
- `predictions[].scenario_analysis`
- `predictions[].decision_audit`
- `predictions[].play_card`
- `predictions[].disclaimer`

Prediction reading rules:

- Result direction confidence is not exact-score confidence.
- Summarize `scoreline_distribution` and `clean_sheet_probability` instead of treating `prediction.score` as certain.
- Treat `venue_adaptation_context` as static baseline evidence, not live weather or confirmed travel routing.

### 5. Build A Human Report Prompt

```bash
python scripts/prediction_report_prompt_builder.py build --edition 2026 --date 2026-06-11 --report-path knowledge-base/2026/data/reports/daily-predictions/2026-06-11.json --match-id 2026-GA-01 --root .
```

Use the generated prompt artifact instead of improvising from memory.

### 6. Build Poster Prompts

Only do this when the user explicitly asks for poster material.

```bash
python scripts/poster_prompt_builder.py build --edition 2026 --date 2026-06-11 --style showdown --match-id 2026-GA-01 --root .
```

If an image backend is configured:

```bash
python scripts/poster_generator.py generate --manifest <poster-manifest.json> --backend image2 --root .
```

If backend is missing, return the blocked result honestly.

### 7. Evaluate After The Match

Record final scores in the match ledger first, then run:

```bash
python scripts/prediction_evaluator.py write --edition 2026 --date 2026-06-11 --root .
python scripts/prediction_evaluation_dashboard.py write --edition 2026 --root .
```

Main outputs:

- `knowledge-base/2026/data/reports/evaluations/<date>.json`
- `knowledge-base/2026/data/reports/evaluations/aggregate-dashboard.json`

## Output Contract For A2A Callers

When another agent answers the user after invoking this repo, prefer this compact response shape:

```text
Status: created | locked_existing_report | blocked | no_matches_found
Report: <path>
Matches: <count>
Main pick: <home/draw/away plus score>
Confidence: <low/medium/high>
Evidence gaps: <list or none>
Key layers: <2-3 analysis_layers summaries>
Safety: 娱乐预测，非投注建议；不得作为投注、购彩或资金决策依据。
```

Do not paste full JSON unless the user asks for it.

## Multi-Layer Analysis Contract

Each prediction should include a structured analysis stack:

```json
{
  "analysis_layers": [
    {
      "layer_id": "evidence_integrity",
      "title": "证据完整度层",
      "verdict": "usable_evidence",
      "confidence": "medium",
      "summary": "...",
      "key_drivers": [],
      "counter_signals": [],
      "missing_context": [],
      "watch_triggers": []
    }
  ],
  "scenario_analysis": {
    "base_case": "...",
    "upset_case": "...",
    "draw_case": "...",
    "watch_triggers": []
  },
  "decision_audit": {
    "risk_level": "controlled",
    "why_this_pick": [],
    "what_would_change_the_pick": [],
    "thin_evidence_warnings": []
  }
}
```

A runtime agent should use these fields when writing summaries, not only the final score.

## Storage Policy

JSON is the canonical artifact layer:

- source snapshots
- daily evidence files
- prediction reports
- poster manifests
- evaluation reports

SQLite is the query/index layer:

- matches
- teams
- players
- predictions
- evaluations
- prediction analysis layers

If JSON and SQLite disagree, prefer the locked JSON report and flag the mismatch.

## Public Defaults And User Local Data

This repository now separates reusable public knowledge from user-owned local state.
Runtime agents must preserve this split.

```text
knowledge-base/public/<edition>/
  match-ledger.json
  teams.json
  rankings/
  rosters/
  history/
  default-predictions/
    manifest.json
    daily-predictions/*.json

knowledge-base/<edition>/data/
  match-overrides.json
  worldcup_<edition>.db
  daily-evidence/
  reports/daily-predictions/*.json
  reports/dashboard/prediction-dashboard.json
```

Meaning:

- Public knowledge is shared, deterministic, and reusable: schedule, teams, rankings, rosters, history, public facts, and bundled AI Octopus default predictions.
- User local data is user-owned: predictions generated by the user, local evidence, manual overrides, evaluations, run traces, dashboard output, and SQLite cache/index files.
- The dashboard is the merge view: public facts + default AI Octopus predictions + user local predictions.

Prediction precedence by `match_id`:

1. `user_local`: `knowledge-base/<edition>/data/reports/daily-predictions/*.json`
2. `octopus_default`: `knowledge-base/public/<edition>/default-predictions/daily-predictions/*.json`
3. `none`: public fact card with no prediction

If a user-generated prediction exists for a match, it replaces the default prediction on the dashboard. If no user prediction exists, the dashboard can still show AI Octopus default predictions. If neither exists, the dashboard shows only fixture/final-score facts and sets `prediction_status: "not_predicted"`.

Dashboard cards expose the data source:

```json
{
  "prediction_origin": "user_local | octopus_default | none",
  "prediction_source": "user_local | octopus_default | none",
  "prediction_source_path": "path/to/source.json",
  "data_origin": "user_local | octopus_default | public_facts"
}
```

Build or refresh the merge view with:

```bash
python scripts/prediction_visual_dashboard.py write --edition 2026 --root .
```

Read:

```text
knowledge-base/2026/data/reports/dashboard/prediction-dashboard.json
knowledge-base/2026/wiki/dashboard/index.html
```

Runtime agents should read `prediction_origin` before summarizing. Do not describe an `octopus_default` card as a user prediction, and do not describe a `none` card as predicted.

## Error Handling

If a command returns a blocked status or missing artifact:

1. Report the blocker plainly.
2. Do not fabricate missing source evidence.
3. Suggest the next command that would resolve the blocker.
4. Keep the safety disclaimer attached to any prediction-like output.

Common blockers:

- missing edition initialization
- missing official fixtures
- missing roster/ranking data
- missing daily evidence
- match already started
- image backend unavailable

## Safety Requirements

Required disclaimer:

```text
娱乐预测，非投注建议；不得作为投注、购彩或资金决策依据。
```

Forbidden output patterns:

- stake sizing
- odds advice
- bankroll management
- guaranteed win language
- lottery advice
- "稳赢", "稳胆", "必赚", "梭哈"

Allowed framing:

- entertainment prediction
- evidence-based uncertainty
- watch points
- scenario analysis
- post-match evaluation
