---
name: fifa-winner-skill
description: Use when the user asks to initialize a FIFA World Cup knowledge base, collect team or player profiles, record matches, generate daily pre-match entertainment predictions, evaluate prediction accuracy, or create World Cup prediction posters. Not for betting or gambling advice.
---

# FIFA-WINNER-SKILL

FIFA-WINNER-SKILL is a reusable World Cup edition workflow. It keeps raw sources, compiled wiki notes, structured data, predictions, posters and post-match evaluations tied to the same edition and match ledger.

## Agent-to-Agent Entry

Runtime agents should read `AGENT_README.md` before invoking commands. The machine-readable capability card is `skill/AGENT_CARD.json`; the tool/resource/prompt catalog is `skill/TOOL_CATALOG.json`; the quick operator guide is `skill/RUNBOOK.md`.

Use JSON reports as canonical audit artifacts. Use SQLite only as a query/index layer. If the two disagree, prefer the locked JSON report and report the mismatch.

## Safety First

Every prediction is entertainment only.

Required disclaimer:

```text
娱乐预测，非投注建议；不得作为投注、购彩或资金决策依据。
```

Never output stake sizing, odds advice, guaranteed wins, lottery advice, 稳赢, 稳胆, or similar gambling-oriented language.

## Command Roots

If running as a standalone GitHub repository, use commands from the repo root with `--root .`.

If running inside the `dxboy` knowledge base, use the project path `_meta/projects/世界杯预测/` and keep edition data isolated under:

- `raw/体育/世界杯/<edition>/`
- `wiki/体育/世界杯/<edition>/`
- `_meta/projects/世界杯预测/data/editions/<edition>/`

## Quick Routes (Tool Layer CLI Command Reference)

- **Initialize Edition & Structure**:
  - `python3 skill/scripts/worldcup_edition_init.py init --edition <edition> --root .`
- **Source Config & Plan Audits**:
  - `python3 skill/scripts/worldcup_source_readiness_auditor.py write --edition <edition> --root .`
  - `python3 skill/scripts/worldcup_prediction_evidence_planner.py write --edition <edition> --root .`
- **T0/T1 Web Source Snapshotting**:
  - `python3 skill/scripts/worldcup_source_snapshot_tool.py plan --edition <edition> --source-id <source-id> --root .`
  - `python3 skill/scripts/worldcup_source_snapshot_tool.py apply --edition <edition> --source-id <source-id> --root .`
- **Official Squad PDF Parser**:
  - `python3 skill/scripts/fifa_squad_pdf_parser.py parse --edition <edition> --pdf <path/to/pdf> --update-edition-teams --root .`
- **Initialize Team Profiles & Player Dossiers**:
  - `python3 skill/scripts/worldcup_profile_init.py init --edition <edition> --scope [teams|players|all] --root .`
- **FIFA Fixtures & Schedule Parsers**:
  - `python3 skill/scripts/worldcup_fixture_parser.py parse --edition <edition> --schedule-json <path/to/json> --root .`
- **FIFA Official Men's Ranking Parser**:
  - `python3 skill/scripts/worldcup_ranking_parser.py parse --edition <edition> --ranking-json <ranking.json> --snapshot-manifest <manifest.json> --root .`
- **Squad Depth & Features Aggregator**:
  - `python3 skill/scripts/worldcup_squad_depth_compiler.py build --edition <edition> --root .`
- **Roster Alignment compiler**:
  - `python3 skill/scripts/worldcup_roster_compiler.py compile --edition <edition> --root .`
- **Adjust Daily Context Evidences (Weather, Injuries, Referee)**:
  - `python3 skill/scripts/daily_evidence_input.py init --edition <edition> --date YYYY-MM-DD --root .`
  - `python3 skill/scripts/daily_evidence_input.py status --edition <edition> --date YYYY-MM-DD --root .`
- **Live Odds & News Sentiment Web Fetchers**:
  - `python3 skill/scripts/worldcup_live_fetcher.py fetch-odds --edition <edition> --date YYYY-MM-DD --root .`
  - `python3 skill/scripts/worldcup_live_fetcher.py fetch-news --edition <edition> --date YYYY-MM-DD --root .`
- **Historical Results Fetcher**:
  - `python3 skill/scripts/worldcup_history_fetcher.py fetch --edition <edition> --root .`
- **Physics Prediction Model (ELo, Rankings, Rest, Travel)**:
  - `python3 skill/scripts/prediction_scoring_model.py predict --edition <edition> --date YYYY-MM-DD --root .`
  - By teams: `python3 skill/scripts/prediction_scoring_model.py predict --edition <edition> --teams "TeamA,TeamB" --root .`
  - By match ID: `python3 skill/scripts/prediction_scoring_model.py predict --edition <edition> --match-id <match_id> --root .`
- **Daily Prediction Runner (E2E daily runner)**:
  - `python3 skill/scripts/daily_prediction_runner.py run --edition <edition> --date YYYY-MM-DD [--now ISO-time] [--poster] --root .`
- **Unified Agent Entrypoint (Octopus Paul Agent)**:
  - `python3 skill/scripts/octopus_paul_agent.py fetch-schedule --edition <edition> --root .`
  - `python3 skill/scripts/octopus_paul_agent.py predict --edition <edition> [--phase <phase> | --group <group> | --teams <teams> | --all] [--now ISO-time] --root .`
- **Prediction Report Prompt Builder**:
  - `python3 skill/scripts/prediction_report_prompt_builder.py build --edition <edition> --date YYYY-MM-DD --report-path <report.json> --match-id <match_id> --root .`
- **Poster Prompts Builder (Chinese Showdown Template)**:
  - `python3 skill/scripts/poster_prompt_builder.py build --edition <edition> --date YYYY-MM-DD --style [prediction|showdown] [--match-id <match_id>] --root .`
- **Poster Generator & Rendering**:
  - `python3 skill/scripts/poster_generator.py generate --manifest <manifest.json> --backend image2 --root .`
- **Post-Match Predictions Evaluator**:
  - `python3 skill/scripts/prediction_evaluator.py write --edition <edition> --date YYYY-MM-DD --root .`
- **Prediction Accuracy Dashboard Compiler**:
  - `python3 skill/scripts/prediction_evaluation_dashboard.py write --edition <edition> --root .`
- **README & Calendar History Compiler**:
  - `python3 skill/scripts/update_readme_and_history.py --edition <edition> --date YYYY-MM-DD --now <now> --root .`
- **GitHub Public Readiness Auditor**:
  - `python3 skill/scripts/worldcup_github_readiness_auditor.py write --edition <edition> --root .`
- **Standalone Portable Export Tool**:
  - `python3 skill/scripts/worldcup_export_standalone.py --edition <edition> --output <target_dir> --root .`

## Workflow

1. Initialize the edition if directories or match ledger are missing.
2. Run source readiness before claiming sources are usable.
3. Snapshot T0/T1 sources before parsing them; every snapshot needs URL, tier, hash and allowed-use metadata.
4. Parse official fixtures, rosters and rankings before stronger prediction claims.
5. Run prediction evidence planning before daily predictions.
6. Mark missing evidence as `partial` or `blocked`; never pretend it is complete.
7. Only predict matches that have not kicked off. Prefer `prediction_scoring_model.py` for official reports.
8. Lock pre-match reports. Do not overwrite them after kickoff.
9. Build report prompts from structured prediction reports, not memory. Build poster prompts only when requested by the user.
10. After matches, append evaluation and update the aggregate dashboard.

## Source Tiers

- T0: FIFA official schedule, FIFA official squad PDF, FIFA rankings, national FA official sites.
- T1: Wikidata, Wikipedia, OpenFootball and similar structured open sources.
- T2: football-data.org, API-Football, TheSportsDB; only after key, rate limit and license boundaries are recorded.
- T3: FBref, StatBunker, Transfermarkt, ESPN and similar references; cross-check only, no unauthorized bulk scraping.

## Prediction Evidence

Check these before daily predictions: official fixtures, official rosters, FIFA rankings, historical World Cup results, recent form, squad depth, injury availability, venue/rest/travel, head-to-head and player identity enrichment.

Statuses must be `complete`, `partial` or `blocked`.

## 玩法卡片

Every daily prediction should include `play_card` with share title, match hook, watch points, risk flags, poster angle, confidence meter and gameplay tags. Keep it fun and shareable, but never gambling-oriented.

## Prediction Rules

- 数据模型权重 (基本面 + 市场)：60%。
- 天纪气运娱乐层权重：上限 40%。
- Missing roster, injury, lineup or recent-form evidence must downgrade confidence.
- Reports must keep the entertainment disclaimer.

- Exact score must come from a scoreline distribution, not from a single default template.
- Encode scoreline failure modes explicitly: repeated `2-1`, `1-2`, and `1-1` overuse is a model issue, not a harmless style choice.
- Treat `result correct + score wrong` as a separate learning signal; do not only tune winner/loser direction.
- If evaluated matches show more clean sheets than predictions, increase shutout branches and suppress automatic loser-goal assumptions.
- Keep scoreline heuristics only when post-match evidence supports them; validation beats intuition.

## Poster Rules

- `image2` is a configurable backend alias.
- User-facing `image2` prompts must be plain `.txt`, not JSON.
- Do not build poster prompts unless the user explicitly asks for poster material.
- Missing backend must return `blocked_missing_backend`.
- Poster manifests must keep prompt, source report, backend, output path and provenance.
