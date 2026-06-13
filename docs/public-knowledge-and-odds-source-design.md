# Public Knowledge And Odds Source Design

## Goal

The dashboard should never be empty just because a user has not run local predictions.

AI Octopus Paul now treats match data as two layers:

- `public facts`: shared, reusable, mostly deterministic football facts.
- `local user layer`: local predictions, manual evidence, evaluations, and run history.

The dashboard merges both layers. Local data wins when both exist.

## Storage Layout

```text
knowledge-base/public/{edition}/
  match-ledger.json
  teams.json
  rankings/
  rosters/
  history/
  results/
  source-manifest.json

knowledge-base/{edition}/data/
  match-overrides.json
  match-ledger.json        # compatibility view only; public ledger remains authoritative
  worldcup_{edition}.db
  daily-evidence/
  reports/
  dashboard/
```

## Merge Rules

1. Local prediction data has highest priority.
2. Public match facts fill missing fixtures, final scores, teams, venue, and schedule.
3. Dashboard emits `prediction_status: "not_predicted"` for public-only match cards.
4. Public-only cards may show actual scores, but never fake predictions.
5. Odds are valid only when source, timestamp, and non-mock status are present.
6. External reference schedules are review inputs. They must not be promoted into the canonical public ledger unless their match IDs and fixtures pass the 104-match World Cup invariant.
7. `load_match_ledger()` reads the public ledger first, then overlays local match state from `match-overrides.json` and the legacy local `match-ledger.json`.
8. Ranking, roster, history, team and squad-depth reads should prefer local files only when present; otherwise they fall back to `knowledge-base/public/{edition}/`.

## Default Prediction Overlay

AI Octopus default predictions are bundled as public reusable knowledge:

```text
knowledge-base/public/{edition}/default-predictions/
  manifest.json
  daily-predictions/*.json
```

Dashboard prediction precedence is:

1. `user_local`: user-generated daily predictions in `knowledge-base/{edition}/data/reports/daily-predictions/*.json`.
2. `octopus_default`: bundled AI Octopus default predictions in `knowledge-base/public/{edition}/default-predictions/daily-predictions/*.json`.
3. `none`: public fact card with no prediction.

The merge key is always `match_id`. If a user predicts the same match, the local item replaces the default item. Dashboard cards expose:

```json
{
  "prediction_origin": "user_local | octopus_default | none",
  "prediction_source": "user_local | octopus_default | none",
  "prediction_source_path": "path/to/source.json",
  "data_origin": "user_local | octopus_default | public_facts"
}
```

This lets frontends show default predictions for a fresh install while still making user-owned predictions authoritative once generated.

Canonical public match IDs currently follow:

```text
{edition}-G{A-L}-{01-06}
{edition}-R32-{01-16}
{edition}-R16-{01-08}
{edition}-QF-{01-04}
{edition}-SF-{01-02}
{edition}-TP-01
{edition}-F-01
```

Reference-project IDs such as `{edition}-20260613-...` stay outside the main dashboard until reviewed.

## Local Overlay Shape

```json
{
  "mode": "worldcup-local-match-overrides",
  "matches": [
    {
      "match_id": "2026-GC-01",
      "prediction_report": "knowledge-base/2026/data/reports/daily-predictions/2026-06-13.json",
      "prediction_status": "locked_pre_match_prediction",
      "final_score": {"home": 1, "away": 2},
      "evaluation": {}
    }
  ]
}
```

Local overlays may annotate a public match. They must not add fixtures to the canonical schedule.

## Card States

```json
{
  "prediction_status": "not_predicted",
  "data_origin": "public_facts",
  "score_text": "-:-",
  "actual_score_home": 2,
  "actual_score_away": 1,
  "is_completed": true
}
```

Frontend guidance:

- `not_predicted + is_completed`: show actual score and badge `未预测`.
- `not_predicted + !is_completed`: show fixture facts and badge `未预测`.
- predicted cards: show `预测 vs 实际` when final score exists.

## Odds Sources

Preferred source order:

1. `manual`: user-entered odds in local daily evidence.
2. `sporttery_fixed_odds`: China Sporttery fixed bonus snapshot.
3. bookmaker API source, for example The Odds API.
4. `odds_unavailable`.
5. `mock_bookmaker`, only when explicitly allowed for local tests.

Sporttery source:

```text
source_id: sporttery_fixed_odds
name: 中国体育彩票竞彩网固定奖金
url: https://webapi.sporttery.cn/gateway/uniform/football/getMatchListV1.qry?clientCode=3001
market_type: had
```

Machine-readable odds shape:

```json
{
  "home_win": 2.1,
  "draw": 3.2,
  "away_win": 2.85,
  "source": "sporttery_fixed_odds",
  "source_name": "中国体育彩票竞彩网固定奖金",
  "source_url": "https://webapi.sporttery.cn/gateway/uniform/football/getMatchListV1.qry?clientCode=3001",
  "market_type": "had",
  "captured_at": "2026-06-13T16:50:00+08:00",
  "match_no": "周六001",
  "is_mock": false
}
```

If Sporttery cannot be reached or cannot match the fixture, write:

```json
{
  "status": "unavailable",
  "source": "odds_unavailable",
  "reason": "sporttery odds not matched",
  "is_mock": false
}
```

## Commands

```powershell
python scripts\worldcup_live_fetcher.py fetch-sporttery-odds --edition 2026 --date 2026-06-13 --root .
python scripts\prediction_visual_dashboard.py write --edition 2026 --root .
```

When direct Sporttery access is blocked, configure:

```powershell
$env:SPORTTERY_PROXY_URL="https://example-proxy/?url={url}"
python scripts\worldcup_live_fetcher.py fetch-sporttery-odds --edition 2026 --date 2026-06-13 --root .
```
