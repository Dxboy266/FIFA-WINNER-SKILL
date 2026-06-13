# Runtime Agent User Data Overlay Guide

This guide is for Codex, Claude Code, Cursor Agent, CI agents, or any A2A wrapper that needs to use AI Octopus Paul data safely.

## Core Rule

Public knowledge and user-local data are separate.

- Public knowledge: official/reusable facts and bundled AI Octopus defaults.
- User-local data: predictions, evidence, evaluations, manual overrides, run traces, dashboard output, and SQLite cache generated on the user's machine.
- Dashboard: public facts + default predictions + user-local predictions merged by `match_id`.

User-local data wins. Never edit public default predictions to represent a user's own run.

## Paths

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

## Source Precedence

For every dashboard card, read `prediction_origin`.

```text
user_local      User generated this prediction locally. It replaces the default.
octopus_default Bundled AI Octopus default prediction. Use only when no user prediction exists.
none            Public fact card only. Do not claim a prediction exists.
```

The merge key is always `match_id`.

## Fresh Install Flow

Use this when the user has not generated predictions yet.

```powershell
python scripts\prediction_visual_dashboard.py write --edition 2026 --root .
```

Then read:

```text
knowledge-base/2026/data/reports/dashboard/prediction-dashboard.json
```

Expected behavior:

- Cards can be populated from `octopus_default`.
- Completed public-only matches can show actual scores.
- Cards with no prediction show `prediction_origin: "none"` and `prediction_status: "not_predicted"`.

## Pulling Latest Code Versus Latest Local Data

Pulling the latest repository code gives the user the latest committed public/default data that ships with the repo. It does not magically include another user's uncommitted local predictions, SQLite cache, manual evidence, or post-match overrides.

After a fresh clone or pull:

- If public default predictions are committed, the dashboard can show `octopus_default`.
- If the user's local `knowledge-base/<edition>/data/reports/daily-predictions/*.json` exists, those cards show `user_local` and replace defaults.
- If the user has no local predictions for a match, the dashboard falls back to `octopus_default` or `none`.
- If real final scores or new official facts were not committed into `knowledge-base/public/<edition>/`, the user must run the update/evaluation tools locally or pull a commit that contains those public fact updates.

For a public release, commit only reusable public/default artifacts and docs. Treat `worldcup_<edition>.db`, local run traces, and user-specific predictions as local state unless the release intentionally publishes a default snapshot.

## User Prediction Flow

Use this when the user asks to run new predictions.

```powershell
python scripts\daily_evidence_input.py init --edition 2026 --date 2026-06-13 --root .
python scripts\worldcup_live_fetcher.py fetch-sporttery-odds --edition 2026 --date 2026-06-13 --root .
python scripts\worldcup_live_fetcher.py fetch-news --edition 2026 --date 2026-06-13 --root .
python scripts\daily_prediction_runner.py run --edition 2026 --date 2026-06-13 --root .
python scripts\prediction_visual_dashboard.py write --edition 2026 --root .
```

Then read:

```text
knowledge-base/2026/data/reports/daily-predictions/2026-06-13.json
knowledge-base/2026/data/reports/dashboard/prediction-dashboard.json
```

The dashboard card for the predicted match should now show:

```json
{
  "prediction_origin": "user_local",
  "prediction_source": "user_local",
  "data_origin": "user_local"
}
```

## Reading A Dashboard Card

Minimum fields another agent should inspect before summarizing:

```json
{
  "match_id": "2026-GA-01",
  "prediction_origin": "user_local",
  "prediction_source_path": "knowledge-base/2026/data/reports/daily-predictions/2026-06-11.json",
  "home_name": "Mexico",
  "away_name": "South Africa",
  "predicted_result_label": "home win",
  "score_text": "2-1",
  "confidence": "medium",
  "scoreline_distribution": [],
  "analysis_layers": [],
  "evidence_gaps": []
}
```

Rules:

- If `prediction_origin` is `user_local`, describe it as the user's local prediction result.
- If `prediction_origin` is `octopus_default`, describe it as bundled AI Octopus default data.
- If `prediction_origin` is `none`, show fixture/final-score facts only.
- Do not treat exact score as certain. Summarize `scoreline_distribution` and confidence separately.
- Keep the entertainment-only safety disclaimer attached to prediction-like output.

## Post-Match Flow

After final scores are recorded in the match ledger:

```powershell
python scripts\prediction_evaluator.py write --edition 2026 --date 2026-06-13 --root .
python scripts\prediction_evaluation_dashboard.py write --edition 2026 --root .
python scripts\prediction_visual_dashboard.py write --edition 2026 --root .
```

Read:

```text
knowledge-base/2026/data/reports/evaluations/2026-06-13.json
knowledge-base/2026/data/reports/evaluations/aggregate-dashboard.json
knowledge-base/2026/data/reports/dashboard/prediction-dashboard.json
```

Report result direction, exact score, and total-goals hits separately.

## A2A Response Template

```text
Status: created | ready | blocked | no_matches_found
Source layer: user_local | octopus_default | none
Report: <path>
Dashboard: knowledge-base/<edition>/data/reports/dashboard/prediction-dashboard.json
Matches: <count>
Main pick: <result plus score>
Confidence: <result confidence / score confidence>
Evidence gaps: <list or none>
Safety: entertainment prediction only; not betting, lottery, stake, or financial advice.
```

## Guardrails

- Do not output stake sizing, bankroll guidance, or guaranteed-win language.
- Do not upgrade confidence because odds are missing or mocked.
- Do not mutate locked pre-match predictions after kickoff.
- Do not import unverified fixtures from reference projects into the canonical 104-match public ledger.
- SQLite is a query/cache layer. Locked JSON reports remain the accountable artifact.
