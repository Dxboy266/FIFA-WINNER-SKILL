# FIFA Winner Skill — Runtime Agent Guide

This file is written for host agents (Codex, Claude Code, Cursor, CI, A2A orchestrators) that want to install and use this Skill.

## Quick Summary

This is a **World Cup entertainment-prediction Skill** (not an Agent). It provides 34 CLI tools, public facts for an edition, and a local runtime. The host agent provides the reasoning loop.

**Safety boundary**: entertainment only. Never betting, stake sizing, gambling advice, or "guaranteed" language.

---

## Architecture

```
FIFA-WINNER-SKILL/
├── skill/                    ← 📦 Pull when SKILL version changes
│   ├── version.json          # 🔑 Skill toolset version
│   ├── scripts/              # 34 CLI tools
│   ├── tests/                # Self-checks
│   ├── schema/               # JSON validation contracts
│   └── SKILL.md, AGENT_CARD.json...
│
├── wiki/                     ← 📊 Pull when PUBLIC version changes
│   ├── public/<edition>/     # 🌐 Public facts (read-only, git-managed)
│   │   └── version.json      # 🔑 Public data version
│   ├── person/<edition>/     # ✏️ YOUR data (NOT in git — .gitignored)
│   └── cache/<edition>/      # 🔄 Rebuildable local cache
│
├── docs/                     # Developer docs (not runtime)
├── assets/                   # Posters, QR codes (not runtime)
├── AGENT_README.md           # ← This file
├── README.md, LICENSE...
└── pyproject.toml, uv.lock
```

---

## Version System

Two independent version files control what to pull:

### 1. Skill version (`skill/version.json`)

```json
{
  "skill_version": "2.0.0",
  "release_date": "2026-06-14",
  "compatibility": { "python": ">=3.10" }
}
```

- **Changes when**: tools are added/modified, contracts change, scripts update.
- **Agent action**: re-pull `skill/` entirely.

### 2. Public data version (`wiki/public/<edition>/version.json`)

```json
{
  "data_hash": "sha256:be10fa8267165a29",
  "match_count": 104,
  "updated_at": "2026-06-14T13:44:23"
}
```

- **Changes when**: fixtures, rankings, rosters, or wiki cards are updated.
- **Agent action**: re-pull `wiki/public/<edition>/` entirely.

---

## Installation (First Time)

### Step 1 — Download

```bash
# Clone or download the repository
git clone https://github.com/Dxboy266/FIFA-WINNER-SKILL.git
cd FIFA-WINNER-SKILL
```

You only need two directories initially:
```
skill/                       # Toolset
wiki/public/<edition>/       # Public facts for your edition (e.g. 2026)
```

### Step 2 — Verify

```bash
python -m pytest skill/tests/ -q   # Should pass: 28 passed
```

### Step 3 — Save versions

Record these so you know when to update later:

```bash
cat skill/version.json                  # → save skill_version
cat wiki/public/2026/version.json       # → save data_hash
```

Store them in `.agent_state.json` at your workspace root:

```json
{
  "skill_version": "2.0.0",
  "public_hash": "sha256:be10fa8267165a29",
  "edition": "2026"
}
```

### Step 4 — Initialize edition

```bash
python skill/scripts/worldcup_edition_init.py init --edition 2026 --root .
```

### Step 5 — Run predictions

```bash
python skill/scripts/octopus_paul_agent.py predict --edition 2026 --all
```

**Storage policy is split in two layers**:

- `wiki/public/<edition>/` = public facts and reusable packaged defaults
- `wiki/person/<edition>/` = your private predictions, evidence, overlays, evaluations and dashboards

The dashboard is built by overlaying `public facts + packaged default predictions + user-local predictions`.
If a user-local prediction exists, it replaces the packaged default for that match.
If no user-local prediction exists, the dashboard still shows the factual match card and any packaged default prediction.

**Do NOT commit `wiki/person/` to git** — it's `.gitignored` and contains your private data.

### Canonical Prediction Freeze

Canonical pre-match predictions are immutable after they are issued:

- before kickoff: status `locked_pre_match`
- after kickoff: status `kickoff_locked`
- after final result: status `result_locked`

Host agents must treat this as a hard rule:

- never regenerate a canonical prediction if one already exists
- never overwrite a match after kickoff
- never overwrite a match after a final result exists
- use `run_type=experiment` / backtest outputs for model experiments and historical replay

In conflicts, prefer the locked canonical report over any later experiment result.

---

## Update (Subsequent Runs)

On every startup, compare local state with remote:

```
1. Read .agent_state.json → stored_skill_version, stored_public_hash
2. Read skill/version.json → current_skill_version
3. Read wiki/public/<edition>/version.json → current_public_hash

IF stored_skill_version != current_skill_version:
    re-pull skill/
    re-run python -m pytest skill/tests/ -q

IF stored_public_hash != current_public_hash:
    re-pull wiki/public/<edition>/

IF both unchanged:
    skip pull, run normally
```

### Daily Run Semantics

`daily_prediction_runner.py run` is `missing_only` by default:

- reuse existing canonical predictions first
- reuse published reports if the DB is missing
- only generate predictions for matches that have not started and still lack a canonical prediction
- refresh evidence only; do not rewrite historical predictions

### Pseudo-code

```python
def check_updates():
    state = load_json(".agent_state.json")
    skill_ver = load_json("skill/version.json")["skill_version"]
    public_hash = load_json("wiki/public/2026/version.json")["data_hash"]

    if state.get("skill_version") != skill_ver:
        print("skill/ has changed — re-pull required")
        # git pull or re-download skill/
        state["skill_version"] = skill_ver

    if state.get("public_hash") != public_hash:
        print("wiki/public/ has changed — re-pull required")
        # git pull or re-download wiki/public/
        state["public_hash"] = public_hash

    save_json(".agent_state.json", state)
```

---

## Upgrading From v1 (Old Installation)

If you installed this project before June 2026, you may have the old v1 structure:

```
FIFA-WINNER-SKILL/       ← v1 structure (OLD)
├── scripts/             # OLD — tools were at root
├── schema/              # OLD
├── skills/              # OLD
├── knowledge-base/      # OLD — now wiki/
├── data/                # OLD
├── docs/                # OLD — now under root
├── assets/              # OLD
└── tests/               # OLD — now skill/tests/
```

### Upgrade Steps

1. **Detect v1**: check if `knowledge-base/` or `scripts/` exist at root level.
2. **Backup your data**:
   ```bash
   # Your predictions and evidence
   cp -r knowledge-base/<edition>/data/reports/ ./backup-person/
   ```
3. **Remove old installation**:
   ```bash
   rm -rf knowledge-base/ scripts/ schema/ skills/ data/ tests/ docs/ assets/ .agent_state.json
   ```
4. **Install v2** (follow Installation steps above).
5. **Restore your data**:
   ```bash
   cp -r ./backup-person/* wiki/person/<edition>/
   ```
6. **Rebuild cache**:
   ```bash
   python skill/scripts/prediction_visual_dashboard.py write --edition 2026 --root .
   ```

---

## What NOT To Commit

These directories contain local/private/rebuildable data:

| Directory | Why |
|---|---|
| `wiki/person/` | Your predictions, evidence, insights — private |
| `wiki/cache/` | Rebuildable from public + person data |
| `.agent_state.json` | Your local version tracking file |

They are listed in `.gitignore`.

---

## Capability Card

| Field | Value |
|---|---|
| Skill name | FIFA Winner Skill |
| Current version | See `skill/version.json` |
| Type | **Skill** (not Agent) |
| Tools | 34 CLI scripts |
| Canonical interface | `python skill/scripts/<tool>.py <command> --edition <edition> --root .` |
| Public data | `wiki/public/<edition>/` (version-controlled by `version.json`) |
| User data | `wiki/person/<edition>/` (`.gitignored`) |
| Cache | `wiki/cache/<edition>/` (rebuildable) |
| Self-tests | `python -m pytest skill/tests/ -q` |

## First Read Order

1. `skill/SKILL.md` — mental model & CLI reference
2. `skill/AGENT_CARD.json` — machine-readable capability card
3. `skill/TOOL_CATALOG.json` — tools, resources, prompts
4. `skill/ORCHESTRATION.md` — recommended workflows
5. `skill/GUARDRAILS.md` — safety boundaries

## Safety

```text
娱乐预测，非投注建议；不得作为投注、购彩或资金决策依据。
```

Forbidden: stake sizing, odds advice, bankroll management, "稳赢", "稳胆", "必赚", "梭哈".
