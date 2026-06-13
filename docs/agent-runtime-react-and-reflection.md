# Agent Runtime ReAct And Reflection

## Decision

The two roadmap items are still necessary for the goal of making scoreline
predictions more reliable, but they should be implemented as bounded,
auditable automation instead of an unconstrained autonomous betting agent.

This project now treats them as:

1. `octopus_react_runner.py`: a bounded ReAct-style matchday runner.
2. `octopus_reflection_tuning.py`: the existing post-match reflection and
   weight-tuning loop.

The output is for entertainment and research reference only. It is not betting,
lottery, or financial advice.

## ReAct Runner

Command:

```powershell
python scripts\octopus_react_runner.py run --edition 2026 --start-date 2026-06-13 --weekend --root .
```

What it does per date:

1. Inspect canonical schedule only.
2. Fetch Sporttery fixed bonus odds.
3. Fetch latest news evidence.
4. Write the matchday intelligence briefing.
5. Run locked pre-match predictions.
6. Rebuild the visual dashboard.
7. Write a JSON and Markdown trace under:

```text
knowledge-base/{edition}/data/reports/agent-runs/
knowledge-base/{edition}/wiki/reports/agent-runs/
```

## Reflection Loop

Command:

```powershell
python scripts\octopus_reflection_tuning.py tune --edition 2026 --root .
```

What it does:

1. Reads locked predictions and post-match evaluations.
2. Writes reflection journal entries.
3. Tunes component weights within safety bounds.
4. Keeps data weight at or above 0.60 and entertainment overlay at or below 0.40.

## Runtime Agent Usage

Codex, Claude Code, or another runtime agent can call:

```powershell
python scripts\octopus_react_runner.py run --edition <edition> --start-date <yyyy-mm-dd> --end-date <yyyy-mm-dd> --root .
python scripts\prediction_visual_dashboard.py write --edition <edition> --root .
python scripts\octopus_reflection_tuning.py tune --edition <edition> --root .
```

The runner records trace events so another agent can inspect what happened
without guessing which tools were called.

## Reliability Notes

- Odds must be sourced and matched to the canonical fixture before they count as
  usable market evidence.
- `mock_bookmaker` is not valid market evidence.
- External reference schedules must not enter the public canonical ledger until
  they pass the 104-match invariant.
- Existing locked daily prediction reports are not overwritten.
