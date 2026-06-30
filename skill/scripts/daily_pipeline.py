#!/usr/bin/env python3
"""Daily pipeline orchestrator for World Cup predictions.

Runs the full daily pipeline:
1. Fetch match results
2. Fetch injuries (web scraping - ESPN RSS NLP + roster)
3. Extract injuries from news (NLP)
4. Fetch odds and news sentiment
5. Run predictions for today's matches
6. Run post-match evaluation pipeline
7. Render profiles
8. Generate visual dashboard

Usage:
    python daily_pipeline.py run --edition 2026 --root .
    python daily_pipeline.py run --edition 2026 --root . --date 2026-06-15
    python daily_pipeline.py run --edition 2026 --root . --skip-fetch --skip-predict
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Fix Windows GBK encoding for emoji output
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Load .env from project root (for local development)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import iso_now  # noqa: E402


def run_step(name: str, cmd: list[str], cwd: Path, skip: bool = False) -> dict:
    """Run a pipeline step and return result."""
    if skip:
        print(f"\n⏭️  Skipping: {name}")
        return {"step": name, "status": "skipped"}

    print(f"\n{'='*60}")
    print(f"▶️  Running: {name}")
    print(f"   Command: {' '.join(cmd)}")
    print(f"{'='*60}")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout per step
        )

        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(f"STDERR: {result.stderr}", file=sys.stderr)

        if result.returncode == 0:
            print(f"✅ {name} completed successfully")
            return {"step": name, "status": "success"}
        else:
            print(f"❌ {name} failed with exit code {result.returncode}")
            return {"step": name, "status": "failed", "returncode": result.returncode, "error": result.stderr}

    except subprocess.TimeoutExpired:
        print(f"⏱️  {name} timed out after 300 seconds")
        return {"step": name, "status": "timeout"}
    except Exception as e:
        print(f"💥 {name} raised exception: {e}")
        traceback.print_exc()
        return {"step": name, "status": "error", "error": str(e)}


def run_daily_pipeline(
    *,
    root: Path,
    edition: str,
    date: str | None = None,
    skip_fetch: bool = False,
    skip_predict: bool = False,
    skip_eval: bool = False,
    skip_profiles: bool = False,
) -> dict:
    """Run the full daily pipeline."""
    start_time = datetime.now(timezone.utc)
    today = date or (datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d"))

    print(f"🚀 Daily Pipeline Started")
    print(f"   Edition: {edition}")
    print(f"   Date: {today}")
    print(f"   Root: {root}")

    results = []
    script = str(SCRIPT_DIR)

    # Step 1: Fetch match results (web + football-data.org API fallback)
    results.append(run_step(
        "Fetch Match Results",
        [sys.executable, f"{script}/fetch_match_results.py", "fetch",
         "--edition", edition, "--from", today, "--to", today, "--root", str(root)],
        root,
        skip=skip_fetch,
    ))

    # Step 2: Fetch injuries via web scraping (ESPN news NLP + roster, no API key)
    results.append(run_step(
        "Fetch Injuries (Web)",
        [sys.executable, f"{script}/fetch_injuries_api_football.py",
         "--edition", edition, "--date", today, "--root", str(root)],
        root,
        skip=skip_fetch,
    ))

    # Step 3: Extract injuries from news (NLP)
    results.append(run_step(
        "Extract Injuries from News (NLP)",
        [sys.executable, f"{script}/extract_injuries_from_news.py",
         "--edition", edition, "--date", today, "--root", str(root)],
        root,
        skip=skip_fetch,
    ))

    # Step 4: Fetch odds and news sentiment
    results.append(run_step(
        "Fetch Odds",
        [sys.executable, f"{script}/worldcup_live_fetcher.py", "fetch-odds",
         "--edition", edition, "--date", today, "--root", str(root), "--allow-mock"],
        root,
        skip=skip_fetch,
    ))

    results.append(run_step(
        "Fetch News Sentiment",
        [sys.executable, f"{script}/worldcup_live_fetcher.py", "fetch-news",
         "--edition", edition, "--date", today, "--root", str(root)],
        root,
        skip=skip_fetch,
    ))

    # Step 5: Run predictions for today's matches
    results.append(run_step(
        "Run Daily Predictions",
        [sys.executable, f"{script}/daily_prediction_runner.py", "run",
         "--edition", edition, "--date", today, "--root", str(root)],
        root,
        skip=skip_predict,
    ))

    # Step 6: Post-match evaluation pipeline
    results.append(run_step(
        "Post-Match Evaluation Pipeline",
        [sys.executable, f"{script}/daily_postmatch_pipeline.py",
         "--edition", edition, "--root", str(root)],
        root,
        skip=skip_eval,
    ))

    # Step 7: Render profiles
    results.append(run_step(
        "Render Profiles",
        [sys.executable, f"{script}/worldcup_profile_renderer.py",
         "--edition", edition, "--root", str(root)],
        root,
        skip=skip_profiles,
    ))

    # Summary
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    summary = {
        "status": "completed",
        "edition": edition,
        "date": today,
        "started_at": start_time.isoformat(),
        "completed_at": end_time.isoformat(),
        "duration_seconds": duration,
        "steps": results,
        "summary": {
            "total": len(results),
            "success": sum(1 for r in results if r.get("status") == "success"),
            "failed": sum(1 for r in results if r.get("status") == "failed"),
            "skipped": sum(1 for r in results if r.get("status") == "skipped"),
        }
    }

    print(f"\n{'='*60}")
    print(f"🏁 Pipeline Complete")
    print(f"   Duration: {duration:.1f}s")
    print(f"   Success: {summary['summary']['success']}")
    print(f"   Failed: {summary['summary']['failed']}")
    print(f"   Skipped: {summary['summary']['skipped']}")
    print(f"{'='*60}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Daily pipeline orchestrator")
    parser.add_argument("command", choices=["run"], help="Command to run")
    parser.add_argument("--edition", required=True, help="World Cup edition")
    parser.add_argument("--root", default=".", help="Project root path")
    parser.add_argument("--date", help="Date to process (YYYY-MM-DD), defaults to today")
    parser.add_argument("--skip-fetch", action="store_true", help="Skip data fetching steps")
    parser.add_argument("--skip-predict", action="store_true", help="Skip prediction step")
    parser.add_argument("--skip-eval", action="store_true", help="Skip evaluation step")
    parser.add_argument("--skip-profiles", action="store_true", help="Skip profile rendering")

    args = parser.parse_args()

    if args.command == "run":
        result = run_daily_pipeline(
            root=Path(args.root),
            edition=args.edition,
            date=args.date,
            skip_fetch=args.skip_fetch,
            skip_predict=args.skip_predict,
            skip_eval=args.skip_eval,
            skip_profiles=args.skip_profiles,
        )
        # Exit with error if any critical step failed
        if any(r.get("status") == "failed" for r in result.get("steps", [])):
            sys.exit(1)


if __name__ == "__main__":
    main()
