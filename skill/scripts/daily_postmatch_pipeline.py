#!/usr/bin/env python3
"""Daily post-match pipeline: evaluate -> reflect -> dashboard -> profiles -> banner -> version.

Usage:
    python skill/scripts/daily_postmatch_pipeline.py --edition 2026 --root .
"""
import subprocess, sys, argparse
from pathlib import Path


def run(cmd: str, cwd: Path) -> bool:
    print(f"\n>>> {cmd}")
    r = subprocess.run(cmd, shell=True, cwd=str(cwd))
    return r.returncode == 0


def main():
    p = argparse.ArgumentParser(description="Post-match evaluation pipeline")
    p.add_argument("--edition", default="2026")
    p.add_argument("--root", default=".")
    p.add_argument("--skip-tune", action="store_true")
    p.add_argument("--skip-profiles", action="store_true")
    args = p.parse_args()

    root = Path(args.root).resolve()
    edition = args.edition

    sys.path.insert(0, str(root / "skill" / "scripts"))
    from worldcup_core import load_match_ledger, iso_now, bump_public_version, wiki_edition_root

    now = iso_now()
    today = now[:10]
    print(f"=== Post-Match Pipeline [{today}] ===\n")

    # Find dates with final scores
    ledger = load_match_ledger(root, edition)
    scored_dates = sorted({m.get("kickoff_at", "")[:10] for m in ledger["matches"] if (m.get("final_score") or {}).get("home") is not None})
    print(f"Scored dates: {scored_dates}")

    # Step 1: Evaluate each date
    for d in scored_dates:
        ev = root / "wiki" / "person" / edition / "reports" / "evaluations" / f"{d}.json"
        if ev.exists():
            print(f"  [{d}] already evaluated")
        else:
            run(f'python skill/scripts/prediction_evaluator.py write --edition {edition} --date {d} --root .', root)

    # Step 2: Aggregate evaluation dashboard
    run(f'python skill/scripts/prediction_evaluation_dashboard.py write --edition {edition} --root .', root)

    # Step 3: Reflection tuning
    if not args.skip_tune:
        run(f'python skill/scripts/octopus_reflection_tuning.py tune --edition {edition} --root .', root)

    # Step 4: Visual dashboard
    run(f'python skill/scripts/prediction_visual_dashboard.py write --edition {edition} --root .', root)

    # Step 5: Render team/player profiles from DB
    if not args.skip_profiles:
        run(f'python skill/scripts/worldcup_profile_renderer.py --edition {edition} --root .', root)

    # Step 6: Banner injection (skip if template already has hero-banner)
    dash = wiki_edition_root(root, edition) / 'dashboard' / 'index.html'
    if dash.exists():
        html = dash.read_text('utf-8')
        if 'hero-banner' not in html:
            banner = 'https://github.com/Dxboy266/FIFA-WINNER-SKILL/raw/main/assets/readme-hero-preview.png'
            tag = f'<img src="{banner}" alt="AI Octopus" style="max-width:720px;width:100%;display:block;margin:0 auto 24px;border-radius:12px;">'
            html = html.replace('<body>', f'<body>\n<div class="hero-banner">{tag}</div>', 1)
            html = html.replace('</style>', '.hero-banner{text-align:center;padding:8px 0 12px 0}\n</style>', 1)
            dash.write_text(html, 'utf-8')
            print("  banner injected")
        else:
            print("  banner already in template")

    # Step 7: Bump version
    ver = bump_public_version(root, edition, fixture_update=today)
    print(f"  version: {ver['data_hash']}")

    # Step 8: Update README + HISTORY
    run(f'python skill/scripts/update_readme_and_history.py --edition {edition} --date {today} --now "{now}" --root .', root)

    print(f"\n=== Pipeline complete ===")


if __name__ == "__main__":
    main()
