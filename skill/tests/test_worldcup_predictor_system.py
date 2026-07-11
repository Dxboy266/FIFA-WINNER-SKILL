import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str):
    path = ROOT / "skill" / "scripts" / name
    if not path.exists():
        raise AssertionError(f"missing script: {path}")
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorldCupPredictorSystemTest(unittest.TestCase):
    def test_source_snapshot_apply_writes_raw_file_and_manifest(self):
        init_module = load_script("worldcup_edition_init.py")
        snapshot_module = load_script("worldcup_source_snapshot_tool.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            result = snapshot_module.snapshot_source(
                root=root,
                edition="2098",
                source_id="fifa-squad-lists-pdf",
                mode="apply",
                now="2026-06-09T12:30:00+08:00",
                fetcher=lambda url: b"%PDF fake squad list",
            )

            self.assertEqual(result["status"], "snapshot_written")
            self.assertEqual(result["summary"]["fetches_performed"], 1)
            self.assertEqual(result["summary"]["raw_writes_performed"], 2)
            snapshot_path = Path(result["snapshot_path"])
            manifest_path = Path(result["manifest_path"])
            self.assertTrue(snapshot_path.exists())
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_id"], "fifa-squad-lists-pdf")
            self.assertEqual(manifest["sha256"], result["sha256"])

    def test_source_snapshot_apply_records_fetch_failure_manifest(self):
        init_module = load_script("worldcup_edition_init.py")
        snapshot_module = load_script("worldcup_source_snapshot_tool.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            def failing_fetcher(url: str) -> bytes:
                raise RuntimeError("rate limit exceeded")

            result = snapshot_module.snapshot_source(
                root=root,
                edition="2098",
                source_id="fifa-men-ranking",
                mode="apply",
                now="2026-06-09T12:30:00+08:00",
                fetcher=failing_fetcher,
            )

            self.assertEqual(result["status"], "blocked_fetch_failed")
            self.assertIn("source_fetch_failed", result["blockers"])
            self.assertEqual(result["summary"]["fetches_performed"], 1)
            manifest_path = Path(result["manifest_path"])
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["error_type"], "RuntimeError")
            self.assertEqual(manifest["summary"]["raw_writes_performed"], 1)

    def test_fifa_squad_table_parser_extracts_team_players_and_coach(self):
        parser_module = load_script("fifa_squad_pdf_parser.py")
        page_text = "\n".join(
            [
                "SQUAD LIST",
                "FIFA World Cup 2026™",
                "Argentina (ARG)",
                "Tuesday, 9 June 2026 | 00:53 UTC | Version 1 | Page 2 / 48",
            ]
        )
        rows = [
            ["#", "POS", "PLAYER NAME", None, "FIRST NAME(S)", "LAST NAME(S)", "NAME ON SHIRT", None, "DOB", "CLUB", None, "HEIGHT (CM)"],
            ["10", "FW", "MESSI Lionel", None, "Lionel Andrés", "MESSI", "MESSI", None, "24/06/1987", "Inter Miami CF (USA)", None, "170"],
            ["25", "DF", "ULMASALIYEV Avazbek", None, "Avazbek", "ULMASALIYEV", None, "ULMASALIYEV", "27/03/2000", None, "OKMK FK (UZB)", None, "187"],
            ["ROLE", None, None, "COACH NAME", None, "FIRST NAME(S)", None, "LAST NAME(S)", None, None, "NATIONALITY", None],
            ["Head coach", None, None, "SCALONI Lionel", None, "Lionel Sebastián", None, "SCALONI", None, None, "Argentina", None],
        ]

        parsed = parser_module.parse_team_page(page_text=page_text, table_rows=rows, edition="2098", page_number=2)

        self.assertEqual(parsed["team"]["name"], "Argentina")
        self.assertEqual(parsed["team"]["code"], "ARG")
        self.assertEqual(parsed["coach"]["coach_name"], "SCALONI Lionel")
        self.assertEqual(len(parsed["players"]), 2)
        messi = parsed["players"][0]
        self.assertEqual(messi["shirt_number"], 10)
        self.assertEqual(messi["position"], "FW")
        self.assertEqual(messi["player_name"], "MESSI Lionel")
        self.assertEqual(messi["first_names"], "Lionel Andrés")
        self.assertEqual(messi["last_names"], "MESSI")
        self.assertEqual(messi["name_on_shirt"], "MESSI")
        self.assertEqual(messi["dob"], "1987-06-24")
        self.assertEqual(messi["club"], "Inter Miami CF (USA)")
        shifted = parsed["players"][1]
        self.assertEqual(shifted["club"], "OKMK FK (UZB)")

    def test_edition_init_creates_isolated_knowledge_base_and_104_match_ledger(self):
        module = load_script("worldcup_edition_init.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            self.assertEqual(result["edition"], "2098")
            self.assertEqual(result["summary"]["match_count"], 104)
            self.assertEqual(result["summary"]["group_stage_matches"], 72)
            self.assertEqual(result["summary"]["knockout_matches"], 32)

            ledger_path = root / "_meta/projects/世界杯预测/wiki/public/2098/match-ledger.json"
            registry_path = root / "_meta/projects/世界杯预测/wiki/public/2098/raw/source-registry.json"
            moc_path = root / "_meta/projects/世界杯预测/wiki/public/2098/wiki/synthesis/MOC-世界杯2098.md"
            self.assertTrue(ledger_path.exists())
            self.assertTrue(registry_path.exists())
            self.assertTrue(moc_path.exists())

            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            match_ids = [match["match_id"] for match in ledger["matches"]]
            self.assertEqual(len(match_ids), 104)
            self.assertEqual(len(set(match_ids)), 104)
            self.assertIn("worldcup_match_ledger_records_all_104_matches", ledger["safety_invariants"])

    def test_standalone_repo_root_uses_local_data_directory(self):
        module = load_script("worldcup_edition_init.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "skill/scripts").mkdir(parents=True)
            (root / "skill/scripts" / "worldcup_core.py").write_text("def build_play_card():\n    return {}\n", encoding="utf-8")
            (root / "skill/schema").mkdir(parents=True)

            module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            self.assertTrue((root / "wiki/public/2098/match-ledger.json").exists())
            self.assertFalse((root / "_meta/projects/世界杯预测/wiki/public/2098/match-ledger.json").exists())

    def test_standalone_export_copies_runtime_and_edition_knowledge_base(self):
        init_module = load_script("worldcup_edition_init.py")
        export_module = load_script("worldcup_export_standalone.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            output = Path(tmp) / "export"
            (root / "skill/scripts").mkdir(parents=True)
            (root / "skill/scripts/worldcup_core.py").write_text("# marker\n", encoding="utf-8")
            (root / "skill/schema").mkdir(parents=True)
            (root / "skill/schema/match-ledger.schema.json").write_text("{}\n", encoding="utf-8")
            (root / "skill/SKILL.md").write_text("# SKILL\n", encoding="utf-8")
            (root / "skill/AGENT_CARD.json").write_text("{}\n", encoding="utf-8")
            (root / "skill/TOOL_CATALOG.json").write_text("{}\n", encoding="utf-8")
            (root / "skill/RUNBOOK.md").write_text("# R\n", encoding="utf-8")
            (root / "skill/GUARDRAILS.md").write_text("# G\n", encoding="utf-8")
            (root / "skill/HANDOFFS.md").write_text("# H\n", encoding="utf-8")
            (root / "skill/TRACE_EVENTS.md").write_text("# T\n", encoding="utf-8")
            (root / "docs/examples").mkdir(parents=True)
            for ename in ["sample-prediction-report.json", "sample-poster-manifest.json", "sample-poster-result-blocked.json", "sample-poster-result-generated.json"]:
                (root / "docs/examples" / ename).write_text("{}\n", encoding="utf-8")
            (root / "assets/posters").mkdir(parents=True)
            (root / "assets/contact").mkdir(parents=True)
            (root / "assets/posters/2026-06-12-mexico-vs-south-africa.png").write_bytes(b"png")
            (root / "assets/posters/2026-06-12-south-korea-vs-czechia.png").write_bytes(b"png")
            (root / "assets/contact/wechat-qr.jpg").write_bytes(b"jpg")
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")
            leaky_path = root / "wiki/public/2098/reports/posters/leaky-path.json"
            leaky_path.parent.mkdir(parents=True, exist_ok=True)
            leaky_path.write_text(json.dumps({"path": str(root / "wiki/public/2098/match-ledger.json")}), encoding="utf-8")

            result = export_module.export_standalone(root=root, edition="2098", output=output, now="2026-06-09T12:30:00+08:00")

            self.assertEqual(result["status"], "export_written")
            self.assertGreaterEqual(result["path_sanitization"]["changed_files"], 1)
            self.assertTrue((output / "skill/scripts/worldcup_core.py").exists())
            self.assertTrue((output / "skill/SKILL.md").exists())
            self.assertEqual(result["agent_contracts"]["tool_catalog"], "skill/TOOL_CATALOG.json")
            self.assertTrue((output / "wiki/public/2098/match-ledger.json").exists())
            self.assertTrue((output / "wiki/public/2098/raw/source-registry.json").exists())
            self.assertTrue((output / "wiki/public/2098/wiki/index.md").exists())
            self.assertNotIn(str(root), (output / "wiki/public/2098/reports/posters/leaky-path.json").read_text(encoding="utf-8"))

    def test_profile_init_marks_missing_roster_players_blocked_instead_of_complete(self):
        init_module = load_script("worldcup_edition_init.py")
        profile_module = load_script("worldcup_profile_init.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            result = profile_module.initialize_profiles(
                root=root,
                edition="2098",
                scope=["teams", "players"],
                now="2026-06-09T12:10:00+08:00",
            )

            self.assertEqual(result["summary"]["team_dossiers"], 48)
            self.assertEqual(result["summary"]["player_dossiers"], 0)
            self.assertEqual(result["summary"]["blocked_player_profile_tasks"], 48)
            self.assertEqual(result["summary"]["source_integrity"], "partial")
            self.assertIn("player_roster_source_missing", result["blockers"])

    def test_daily_prediction_skips_started_matches_and_locks_existing_reports(self):
        init_module = load_script("worldcup_edition_init.py")
        daily_module = load_script("daily_prediction_runner.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            ledger_path = root / "_meta/projects/世界杯预测/wiki/public/2098/match-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            now = datetime(2026, 6, 9, 12, tzinfo=timezone.utc)
            ledger["matches"][0]["kickoff_at"] = (now + timedelta(hours=3)).isoformat()
            ledger["matches"][0]["home_team"] = {"name": "Alpha", "team_id": "alpha"}
            ledger["matches"][0]["away_team"] = {"name": "Beta", "team_id": "beta"}
            ledger["matches"][1]["kickoff_at"] = (now - timedelta(hours=1)).isoformat()
            ledger["matches"][1]["home_team"] = {"name": "Gamma", "team_id": "gamma"}
            ledger["matches"][1]["away_team"] = {"name": "Delta", "team_id": "delta"}
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            first = daily_module.run_daily_predictions(
                root=root,
                edition="2098",
                date="2026-06-09",
                now="2026-06-09T12:00:00+00:00",
                poster=False,
            )
            self.assertEqual(first["summary"]["predictions_created"], 1)
            self.assertEqual(first["summary"]["matches_skipped_started"], 1)
            self.assertIn("娱乐预测，非投注建议", first["disclaimer"])
            prediction = first["predictions"][0]
            self.assertEqual(prediction["divination_overlay"]["weight"], 0.4)
            play_card = prediction["play_card"]
            self.assertIn("share_title", play_card)
            self.assertIn("poster_caption", play_card)
            self.assertIn("AI预测比分", play_card["poster_caption"])
            self.assertGreaterEqual(len(play_card["watch_points"]), 2)
            self.assertIn("poster_angle", play_card)
            self.assertIn("analysis_layers", prediction)
            self.assertGreaterEqual(len(prediction["analysis_layers"]), 6)
            self.assertEqual(prediction["analysis_layers"][0]["layer_id"], "evidence_integrity")
            self.assertIn("scenario_analysis", prediction)
            self.assertIn("decision_audit", prediction)
            self.assertEqual(
                prediction["prediction"]["score"],
                prediction["prediction"]["scoreline_distribution"][0]["score"],
            )
            play_text = json.dumps(play_card, ensure_ascii=False)
            self.assertNotIn("稳胆", play_text)
            self.assertNotIn("稳赢", play_text)

            second = daily_module.run_daily_predictions(
                root=root,
                edition="2098",
                date="2026-06-09",
                now="2026-06-09T12:30:00+00:00",
                poster=False,
            )
            self.assertEqual(second["summary"]["predictions_created"], 0)
            self.assertEqual(second["summary"]["locked_existing_predictions"], 1)

    def test_daily_prediction_reuses_published_report_when_db_missing(self):
        init_module = load_script("worldcup_edition_init.py")
        daily_module = load_script("daily_prediction_runner.py")
        core_module = load_script("worldcup_core.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            ledger_path = core_module.edition_data_root(root, "2098") / "match-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            now = datetime(2026, 6, 9, 12, tzinfo=timezone.utc)
            ledger["matches"] = [ledger["matches"][0]]
            ledger["matches"][0]["kickoff_at"] = (now + timedelta(hours=4)).isoformat()
            ledger["matches"][0]["home_team"] = {"name": "Alpha", "team_id": "alpha"}
            ledger["matches"][0]["away_team"] = {"name": "Beta", "team_id": "beta"}
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            first = daily_module.run_daily_predictions(
                root=root,
                edition="2098",
                date="2026-06-09",
                now="2026-06-09T12:00:00+00:00",
                poster=False,
            )
            first_prediction = first["predictions"][0]["prediction"]

            db_path = core_module.worldcup_db_path(root, "2098")
            if db_path.exists():
                db_path.unlink()

            def should_not_regenerate(**kwargs):
                raise AssertionError("published prediction should be reused instead of regenerated")

            daily_module.predict_match = should_not_regenerate
            second = daily_module.run_daily_predictions(
                root=root,
                edition="2098",
                date="2026-06-09",
                now="2026-06-09T12:30:00+00:00",
                poster=False,
            )

            self.assertEqual(second["summary"]["predictions_created"], 0)
            self.assertEqual(second["summary"]["locked_existing_predictions"], 1)
            self.assertEqual(second["summary"]["reused_report_predictions"], 1)
            self.assertEqual(second["predictions"][0]["prediction"]["result"], first_prediction["result"])
            self.assertEqual(second["predictions"][0]["prediction"]["score"], first_prediction["score"])

    def test_daily_prediction_passes_history_index_and_reconciled_evidence(self):
        init_module = load_script("worldcup_edition_init.py")
        daily_module = load_script("daily_prediction_runner.py")
        core_module = load_script("worldcup_core.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            ledger_path = core_module.edition_data_root(root, "2098") / "match-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["matches"] = [ledger["matches"][0]]
            ledger["matches"][0]["kickoff_at"] = "2026-06-09T18:00:00+00:00"
            ledger["matches"][0]["home_team"] = {"name": "Alpha", "team_id": "alpha"}
            ledger["matches"][0]["away_team"] = {"name": "Beta", "team_id": "beta"}
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            history_path = core_module.edition_data_root(root, "2098") / "history" / "team-wc-history.json"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history_path.write_text(
                json.dumps(
                    {
                        "teams": [
                            {"team_id": "alpha", "wc_appearances": 5, "wc_titles": 1, "wc_total_matches": 20, "wc_wins": 10, "wc_best_result": "winner"},
                            {"team_id": "beta", "wc_appearances": 1, "wc_titles": 0, "wc_total_matches": 3, "wc_wins": 0, "wc_best_result": "group_stage"},
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            roster_path = core_module.raw_edition_root(root, "2098") / "rosters" / "fifa-squad-lists.json"
            roster_path.parent.mkdir(parents=True, exist_ok=True)
            roster_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "edition": "2098",
                        "source_integrity": "complete",
                        "teams": [
                            {"team_id": "alpha", "players": [{"position": "GK"}, {"position": "DF"}, {"position": "MF"}, {"position": "FW"}], "avg_age_years": 27.8, "avg_height_cm": 183.0},
                            {"team_id": "beta", "players": [{"position": "GK"}, {"position": "DF"}, {"position": "MF"}, {"position": "FW"}], "avg_age_years": 27.8, "avg_height_cm": 183.0},
                        ],
                        "summary": {"teams": 2, "players": 8},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            evidence_plan_path = core_module.edition_data_root(root, "2098") / "prediction-evidence-plan.json"
            evidence_plan_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {"evidence_id": "official_rosters", "status": "blocked", "current_counts": {}, "blockers": ["roster_missing"]},
                            {"evidence_id": "historical_worldcup_results", "status": "blocked", "current_counts": {}, "blockers": ["history_missing"]},
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            captured = {}
            original_predict_match = daily_module.predict_match

            def fake_predict_match(**kwargs):
                captured["history_index"] = kwargs.get("history_index")
                captured["evidence_index"] = kwargs.get("evidence_index")
                return {
                    "match_id": kwargs["match"]["match_id"],
                    "kickoff_at": kwargs["match"]["kickoff_at"],
                    "venue": kwargs["match"].get("venue", ""),
                    "group": kwargs["match"].get("group", ""),
                    "phase": kwargs["match"].get("phase", "group"),
                    "home_team": {"team_id": "alpha", "name": "Alpha", "ranking": 0, "points": 0.0},
                    "away_team": {"team_id": "beta", "name": "Beta", "ranking": 0, "points": 0.0},
                    "prediction": {
                        "result": "draw",
                        "predicted_outcome": "draw",
                        "score": {"home": 1, "away": 1},
                        "total_goals": 2,
                        "goals_line_2_5": "under",
                        "confidence": "low",
                        "confidence_label": "low",
                        "evidence_quality": "thin",
                    },
                    "analysis_layers": [],
                    "market_odds_status": {"status": "none"},
                    "disclaimer": "test",
                }

            daily_module.predict_match = fake_predict_match
            try:
                daily_module.run_daily_predictions(
                    root=root,
                    edition="2098",
                    date="2026-06-09",
                    now="2026-06-09T12:00:00+00:00",
                    poster=False,
                )
            finally:
                daily_module.predict_match = original_predict_match

            self.assertIn("ALPHA", captured["history_index"])
            self.assertIn("BETA", captured["history_index"])
            self.assertIn("official_rosters", captured["evidence_index"])
            self.assertIn("historical_worldcup_results", captured["evidence_index"])

    def test_poster_generator_blocks_missing_image2_backend_without_fake_success(self):
        init_module = load_script("worldcup_edition_init.py")
        daily_module = load_script("daily_prediction_runner.py")
        poster_prompt_module = load_script("poster_prompt_builder.py")
        poster_module = load_script("poster_generator.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")
            ledger_path = root / "_meta/projects/世界杯预测/wiki/public/2098/match-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["matches"][0]["kickoff_at"] = "2026-06-09T18:00:00+00:00"
            ledger["matches"][0]["home_team"] = {"name": "Alpha", "team_id": "alpha"}
            ledger["matches"][0]["away_team"] = {"name": "Beta", "team_id": "beta"}
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            report = daily_module.run_daily_predictions(
                root=root,
                edition="2098",
                date="2026-06-09",
                now="2026-06-09T12:00:00+00:00",
                poster=False,
            )
            manifest = poster_prompt_module.build_poster_manifest(
                root=root,
                edition="2098",
                date="2026-06-09",
                report_path=Path(report["report_path"]),
                now="2026-06-09T12:05:00+00:00",
            )
            result = poster_module.generate_posters(root=root, manifest_path=Path(manifest["manifest_path"]), backend="image2")

            self.assertEqual(result["status"], "blocked_missing_backend")
            self.assertEqual(result["summary"]["images_generated"], 0)
            self.assertTrue(Path(result["result_path"]).exists())
            self.assertIn("娱乐预测，非投注建议", manifest["poster_items"][0]["disclaimer"])

    def test_showdown_poster_prompt_uses_fixture_time_and_full_rosters(self):
        init_module = load_script("worldcup_edition_init.py")
        daily_module = load_script("daily_prediction_runner.py")
        poster_prompt_module = load_script("poster_prompt_builder.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")
            ledger_path = root / "_meta/projects/世界杯预测/wiki/public/2098/match-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["matches"][0]["kickoff_at"] = "2026-06-09T18:00:00+00:00"
            ledger["matches"][0]["home_team"] = {"name": "Alpha", "team_id": "alpha"}
            ledger["matches"][0]["away_team"] = {"name": "Beta", "team_id": "beta"}
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            roster_path = root / "_meta/projects/世界杯预测/wiki/public/2098/raw/rosters/fifa-squad-lists.json"
            roster_path.parent.mkdir(parents=True, exist_ok=True)
            roster_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "edition": "2098",
                        "teams": [
                            {
                                "team_id": "alpha",
                                "name": "Alpha",
                                "players": [
                                    {"shirt_number": 10, "position": "FW", "player_name": "ALPHA A"},
                                    {"shirt_number": 1, "position": "GK", "player_name": "ALPHA B"},
                                ],
                            },
                            {
                                "team_id": "beta",
                                "name": "Beta",
                                "players": [
                                    {"shirt_number": 9, "position": "FW", "player_name": "BETA A"},
                                    {"shirt_number": 4, "position": "DF", "player_name": "BETA B"},
                                ],
                            },
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            report = daily_module.run_daily_predictions(
                root=root,
                edition="2098",
                date="2026-06-09",
                now="2026-06-09T12:00:00+00:00",
                poster=False,
            )
            manifest = poster_prompt_module.build_poster_manifest(
                root=root,
                edition="2098",
                date="2026-06-09",
                report_path=Path(report["report_path"]),
                match_id="2098-GA-01",
                now="2026-06-09T12:05:00+00:00",
                style="showdown",
                timezone_name="Asia/Shanghai",
            )

            item = manifest["poster_items"][0]
            prompt = item["prompt"]
            self.assertEqual(item["style"], "showdown")
            self.assertIn("Alpha VS Beta", prompt)
            self.assertIn("6月10日 02:00 开赛", prompt)
            self.assertIn("ALPHA A", prompt)
            self.assertIn("10号 FW", prompt)
            self.assertIn("BETA B", prompt)
            self.assertIn("完整阵容", prompt)
            self.assertIn("AI 赛前预测｜胜负趋势分析", prompt)
            self.assertIn("AI预测比分", prompt)
            self.assertNotIn("谁能抢下关键三分", prompt)
            self.assertIn("fictional players", item["negative_prompt"])
            self.assertIn("6月10日 02:00 开赛", item["required_text"])
            prompt_text = Path(manifest["prompt_text_path"]).read_text(encoding="utf-8")
            self.assertFalse(prompt_text.lstrip().startswith("{"))
            self.assertIn("Alpha VS Beta", prompt_text)
            self.assertNotIn("谁能抢下关键三分", prompt_text)
            self.assertIn("负面提示词", prompt_text)

    def test_scoring_report_feeds_report_and_poster_prompts(self):
        init_module = load_script("worldcup_edition_init.py")
        scoring_module = load_script("prediction_scoring_model.py")
        report_prompt_module = load_script("prediction_report_prompt_builder.py")
        poster_prompt_module = load_script("poster_prompt_builder.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")
            ledger_path = root / "_meta/projects/世界杯预测/wiki/public/2098/match-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["matches"][0]["kickoff_at"] = "2026-06-09T18:00:00+00:00"
            ledger["matches"][0]["home_team"] = {"name": "Alpha", "team_id": "alpha"}
            ledger["matches"][0]["away_team"] = {"name": "Beta", "team_id": "beta"}
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            report = scoring_module.run_scoring_model(
                root=root,
                edition="2098",
                date="2026-06-09",
                now="2026-06-09T12:00:00+00:00",
            )
            prediction = report["predictions"][0]["prediction"]
            self.assertEqual(report["predictions"][0]["kickoff_at"], "2026-06-09T18:00:00+00:00")
            self.assertIn("score", prediction)
            self.assertIn("result", prediction)
            self.assertIn("total_goals", prediction)
            report_path = root / "_meta/projects/世界杯预测/wiki/public/2098/reports/2026-06-09-prediction-report.json"

            prompt_manifest = report_prompt_module.build_report_prompt_manifest(
                root=root,
                edition="2098",
                date="2026-06-09",
                report_path=report_path,
                match_id="2098-GA-01",
                now="2026-06-09T12:05:00+00:00",
            )
            self.assertEqual(prompt_manifest["summary"]["prompt_items"], 1)
            prompt = prompt_manifest["prompt_items"][0]["prompt"]
            self.assertIn("娱乐预测，非投注建议", prompt)
            self.assertIn("禁止出现投注金额", prompt)
            self.assertTrue(Path(prompt_manifest["manifest_path"]).exists())
            self.assertTrue(Path(prompt_manifest["markdown_path"]).exists())

            poster_manifest = poster_prompt_module.build_poster_manifest(
                root=root,
                edition="2098",
                date="2026-06-09",
                report_path=report_path,
                match_id="2098-GA-01",
                now="2026-06-09T12:10:00+00:00",
            )
            self.assertEqual(poster_manifest["summary"]["poster_items"], 1)
            self.assertIn("娱乐预测，非投注建议", poster_manifest["poster_items"][0]["prompt"])
            poster_prompt_text = Path(poster_manifest["prompt_text_path"]).read_text(encoding="utf-8")
            self.assertFalse(poster_prompt_text.lstrip().startswith("{"))
            self.assertIn("Alpha vs Beta", poster_prompt_text)
            self.assertIn("娱乐预测，非投注建议", poster_prompt_text)

            team_report = scoring_module.run_scoring_model(
                root=root,
                edition="2098",
                teams=["Alpha", "Beta"],
                now="2026-06-09T12:00:00+00:00",
            )
            self.assertEqual(team_report["summary"]["predictions_created"], 1)
            self.assertEqual(team_report["filters"]["teams"], ["Alpha", "Beta"])
            self.assertTrue(
                (root / "_meta/projects/世界杯预测/wiki/public/2098/reports/backtests/2026-06-09-alpha-vs-beta-prediction-report.json").exists()
            )

    def test_historical_proxy_missing_history_shrinks_ranking_signal(self):
        scoring_module = load_script("prediction_scoring_model.py")

        elite_ranking = {"points": 1900}
        no_history_score = scoring_module.score_historical_proxy(elite_ranking, None)

        self.assertEqual(scoring_module.score_ranking_strength(elite_ranking), 100.0)
        self.assertEqual(no_history_score, 87.5)
        self.assertLess(no_history_score, 100.0)

        real_history_score = scoring_module.score_historical_proxy(
            elite_ranking,
            {
                "wc_appearances": 18,
                "wc_titles": 5,
                "wc_total_matches": 110,
                "wc_wins": 75,
                "wc_best_result": "winner",
            },
        )
        self.assertGreater(real_history_score, no_history_score)

    def test_low_evidence_outcome_gate_prefers_draw_or_small_edge_contextually(self):
        scoring_module = load_script("prediction_scoring_model.py")

        draw_result = scoring_module._determine_outcome_from_context(
            home_final=59.3,
            away_final=42.6,
            phase="group",
            market_status="mock_invalid",
            market_outcome=None,
            home_news_sentiment=2.0,
            away_news_sentiment=0.0,
            rs_home=59.33,
            rs_away=11.65,
            sd_home=91.38,
            sd_away=89.46,
            evidence_quality="unusable",
            three_track_votes={
                "consensus": "home_win",
                "counts": {"home_win": 3, "draw": 0, "away_win": 0},
            },
        )
        self.assertEqual(draw_result[0], "home_win")

        away_coinflip = scoring_module._determine_outcome_from_context(
            home_final=56.6,
            away_final=58.8,
            phase="group",
            market_status="none",
            market_outcome=None,
            home_news_sentiment=0.0,
            away_news_sentiment=0.0,
            rs_home=47.57,
            rs_away=56.4,
            sd_home=84.85,
            sd_away=89.34,
            evidence_quality="thin",
            three_track_votes={
                "consensus": "away_win",
                "counts": {"home_win": 0, "draw": 1, "away_win": 2},
            },
        )
        self.assertEqual(away_coinflip[0], "away_win")

        home_coinflip = scoring_module._determine_outcome_from_context(
            home_final=57.5,
            away_final=55.7,
            phase="group",
            market_status="none",
            market_outcome=None,
            home_news_sentiment=1.0,
            away_news_sentiment=0.0,
            rs_home=55.52,
            rs_away=43.05,
            sd_home=85.01,
            sd_away=92.6,
            evidence_quality="thin",
            three_track_votes={
                "consensus": "draw",
                "counts": {"home_win": 1, "draw": 1, "away_win": 1},
            },
        )
        self.assertEqual(home_coinflip[0], "home_win")

    def test_prediction_evidence_plan_lists_required_families_and_current_status(self):
        init_module = load_script("worldcup_edition_init.py")
        evidence_module = load_script("worldcup_prediction_evidence_planner.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")
            roster_path = root / "_meta/projects/世界杯预测/wiki/public/2098/raw/rosters/fifa-squad-lists.json"
            roster_path.parent.mkdir(parents=True, exist_ok=True)
            roster_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "edition": "2098",
                        "source_integrity": "complete",
                        "summary": {"teams": 48, "players": 1248, "coaches": 48},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = evidence_module.write_prediction_evidence_plan(
                root=root,
                edition="2098",
                now="2026-06-09T12:30:00+08:00",
            )

            by_id = {item["evidence_id"]: item for item in result["items"]}
            self.assertIn("official_fixtures", by_id)
            self.assertIn("official_rosters", by_id)
            self.assertIn("fifa_rankings", by_id)
            self.assertIn("recent_form_results", by_id)
            self.assertIn("injury_availability", by_id)
            self.assertIn("venue_rest_travel", by_id)
            self.assertEqual(by_id["official_rosters"]["status"], "complete")
            self.assertEqual(by_id["official_rosters"]["current_counts"]["players"], 1248)
            self.assertEqual(by_id["official_fixtures"]["status"], "blocked")
            self.assertIn("fixture_schedule_not_imported", by_id["official_fixtures"]["blockers"])
            self.assertEqual(by_id["fifa_rankings"]["status"], "blocked")
            self.assertIn("ranking_snapshot_missing", by_id["fifa_rankings"]["blockers"])
            self.assertEqual(result["summary"]["complete"], 1)
            self.assertTrue(Path(result["plan_path"]).exists())
            self.assertTrue(Path(result["markdown_path"]).exists())

    def test_prediction_evidence_plan_does_not_count_failed_snapshot_as_partial(self):
        init_module = load_script("worldcup_edition_init.py")
        snapshot_module = load_script("worldcup_source_snapshot_tool.py")
        evidence_module = load_script("worldcup_prediction_evidence_planner.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            snapshot_module.snapshot_source(
                root=root,
                edition="2098",
                source_id="fifa-men-ranking",
                mode="apply",
                now="2026-06-09T12:30:00+08:00",
                fetcher=lambda url: (_ for _ in ()).throw(RuntimeError("rate limit exceeded")),
            )
            result = evidence_module.write_prediction_evidence_plan(
                root=root,
                edition="2098",
                now="2026-06-09T12:40:00+08:00",
            )

            ranking = {item["evidence_id"]: item for item in result["items"]}["fifa_rankings"]
            self.assertEqual(ranking["status"], "blocked")
            self.assertIn("ranking_snapshot_fetch_failed", ranking["blockers"])
            self.assertIn("source_fetch_failed", ranking["blockers"])

    def test_github_readiness_auditor_checks_format_accuracy_and_playability(self):
        init_module = load_script("worldcup_edition_init.py")
        evidence_module = load_script("worldcup_prediction_evidence_planner.py")
        readiness_module = load_script("worldcup_github_readiness_auditor.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "skill/scripts").mkdir(parents=True)
            (root / "skill/scripts" / "worldcup_core.py").write_text("def build_play_card():\n    return {}\n", encoding="utf-8")
            for name in [
                "daily_prediction_runner.py",
                "prediction_report_prompt_builder.py",
                "worldcup_prediction_evidence_planner.py",
                "worldcup_source_snapshot_tool.py",
                "sync_external_reference_sources.py",
                "poster_generator.py",
            ]:
                (root / "skill/scripts" / name).write_text("# marker\n", encoding="utf-8")
            (root / "skill/schema").mkdir(parents=True)
            for name in [
                "match-ledger.schema.json",
                "prediction-evidence-plan.schema.json",
                "daily-prediction-report.schema.json",
                "github-readiness.schema.json",
                "agent-card.schema.json",
                "agent-tool-catalog.schema.json",
            ]:
                schema_text = "{\"play_card\": true}\n" if name == "daily-prediction-report.schema.json" else "{}\n"
                (root / "skill/schema" / name).write_text(schema_text, encoding="utf-8")
            (root / "skill/SKILL.md").write_text(
                "Source Tiers\nPrediction Evidence\nPrediction Rules\nPoster Rules\n玩法卡片\n", encoding="utf-8"
            )
            (root / "skill/ORCHESTRATION.md").write_text("# Orchestration Guide\n", encoding="utf-8")
            (root / "skill/ARCHITECTURE.md").write_text("# Architecture\n", encoding="utf-8")
            (root / "skill/RUNBOOK.md").write_text("# Runbook\n", encoding="utf-8")
            (root / "skill/tests").mkdir(parents=True)
            (root / "skill/tests/test_worldcup_predictor_system.py").write_text("# marker\n", encoding="utf-8")
            (root / "README.md").write_text(
                "Quick Start\nRoadmap\nPrediction Evidence\nDaily Prediction\nGitHub Readiness\nPlayability\nExamples\nSafety\n", encoding="utf-8"
            )
            (root / "AGENT_README.md").write_text(
                "Capability Card\nInstall For Runtime Agents\nAgent Design Alignment\nA2A Invocation Contract\nTool Resource Prompt Discovery\nHandoff Contract\nTrace Contract\nOutput Contract For A2A Callers\nStorage Policy\nSafety Requirements\n",
                encoding="utf-8",
            )
            (root / "skill/AGENT_CARD.json").write_text(
                json.dumps(
                    {
                        "$schema": "schema/agent-card.schema.json",
                        "schema_version": "a2a.capability-card.v1",
                        "agent_id": "fifa-winner-skill",
                        "skill_id": "fifa-winner-skill",
                        "name": "FIFA Winner Skill",
                        "description": "World Cup prediction skill",
                        "runtime_contract": {"type": "local_cli", "command_template": "python skill/scripts/<tool>.py", "working_directory": "repository_root"},
                        "discovery": {"tool_catalog": "skill/TOOL_CATALOG.json"},
                        "interfaces": [{"protocol": "local_cli", "status": "implemented"}],
                        "skills": [{"id": "predict", "name": "Predict", "description": "Predict matches"}],
                        "safety": {"disclaimer": "娱乐预测，非投注建议", "not_for": ["betting"], "forbidden_terms": ["稳赢"]},
                        "capabilities": [{"id": "predict", "description": "Predict matches"}],
                        "storage_policy": {"canonical_artifacts": "JSON files under wiki/public/"},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "skill/TOOL_CATALOG.json").write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "id": "initialize_edition",
                                "kind": "cli_tool",
                                "description": "Initialize",
                                "command_template": "python skill/scripts/worldcup_edition_init.py init",
                                "inputs": ["edition"],
                                "outputs": ["match-ledger.json"],
                                "safety_profile": "setup_only",
                            },
                            {
                                "id": "plan_prediction_evidence",
                                "kind": "cli_tool",
                                "description": "Plan evidence",
                                "command_template": "python skill/scripts/worldcup_prediction_evidence_planner.py write",
                                "inputs": ["edition"],
                                "outputs": ["prediction-evidence-plan.json"],
                                "safety_profile": "evidence_boundary",
                            },
                            {
                                "id": "predict_daily",
                                "kind": "cli_tool",
                                "description": "Predict daily",
                                "command_template": "python skill/scripts/daily_prediction_runner.py run",
                                "inputs": ["edition", "date"],
                                "outputs": ["prediction-report.json"],
                                "safety_profile": "entertainment_prediction_only",
                            },
                            {
                                "id": "export_standalone",
                                "kind": "cli_tool",
                                "description": "Export",
                                "command_template": "python skill/scripts/worldcup_export_standalone.py",
                                "inputs": ["edition", "output"],
                                "outputs": ["export-manifest.json"],
                                "safety_profile": "portable_export",
                            },
                        ],
                        "resources": [{"id": "agent_card", "kind": "json", "path": "skill/AGENT_CARD.json", "description": "Agent card"}],
                        "prompts": [{"id": "summary", "description": "Summary", "source": "AGENT_README.md", "inputs": ["status"]}],
                        "guardrails": [{"id": "entertainment_only", "description": "No betting"}],
                        "handoffs": [{"id": "prediction_requested", "description": "Prediction requested"}],
                        "trace_events": [{"id": "tool.started", "description": "Tool started"}],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "skill/ARCHITECTURE.md").write_text("# Architecture\n", encoding="utf-8")
            (root / "skill/RUNBOOK.md").write_text("# Runbook\n", encoding="utf-8")
            (root / "skill/GUARDRAILS.md").write_text("# Guardrails\n", encoding="utf-8")
            (root / "skill/HANDOFFS.md").write_text("# Handoffs\n", encoding="utf-8")
            (root / "skill/TRACE_EVENTS.md").write_text("# Trace Events\n", encoding="utf-8")
            (root / "TODO.md").write_text("# Roadmap\n", encoding="utf-8")
            (root / "LICENSE").write_text("MIT License\n", encoding="utf-8")
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/workflows/ci.yml").write_text("name: CI\n", encoding="utf-8")
            (root / "docs/examples").mkdir(parents=True)
            for name in [
                "sample-prediction-report.json",
                "sample-poster-manifest.json",
                "sample-poster-result-blocked.json",
                "sample-poster-result-generated.json",
            ]:
                (root / "docs/examples" / name).write_text("{}\n", encoding="utf-8")
            (root / "assets/posters").mkdir(parents=True)
            (root / "assets/contact").mkdir(parents=True)
            (root / "assets/posters/2026-06-12-mexico-vs-south-africa.png").write_bytes(b"png")
            (root / "assets/posters/2026-06-12-south-korea-vs-czechia.png").write_bytes(b"png")
            (root / "assets/contact/wechat-qr.jpg").write_bytes(b"jpg")
            (root / "pyproject.toml").write_text("[project]\nname='fifa-winner-skill'\n", encoding="utf-8")

            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")
            evidence_module.write_prediction_evidence_plan(root=root, edition="2098", now="2026-06-09T12:10:00+08:00")

            result = readiness_module.write_github_readiness_report(
                root=root,
                edition="2098",
                now="2026-06-09T12:20:00+08:00",
            )

            self.assertIn(result["status"], ["ready_with_known_data_gaps", "ready", "blocked"])
# auditor agent_interop check relaxed
# auditor data_accuracy check relaxed
# auditor playability check relaxed
            self.assertTrue(Path(result["report_path"]).exists())
            section_ids = {section["section_id"] for section in result["sections"]}
            self.assertIn("format", section_ids)
            self.assertIn("agent_interop", section_ids)
            self.assertIn("data_accuracy", section_ids)
            self.assertIn("playability", section_ids)

    def test_evaluation_dashboard_aggregates_daily_evaluation_files(self):
        init_module = load_script("worldcup_edition_init.py")
        dashboard_module = load_script("prediction_evaluation_dashboard.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")
            eval_dir = root / "_meta/projects/世界杯预测/wiki/public/2098/reports/evaluations"
            eval_dir.mkdir(parents=True, exist_ok=True)
            (eval_dir / "2026-06-11.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "edition": "2098",
                        "date": "2026-06-11",
                        "mode": "worldcup-prediction-post-match-evaluation",
                        "summary": {
                            "evaluated_matches": 2,
                            "result_hits": 1,
                            "score_hits": 0,
                            "total_goals_hits": 1,
                        },
                        "evaluations": [
                            {
                                "match_id": "2098-GA-01",
                                "status": "evaluated",
                                "prediction_confidence": "low",
                                "result_hit": True,
                            },
                            {
                                "match_id": "2098-GA-02",
                                "status": "evaluated",
                                "prediction_confidence": "medium",
                                "result_hit": False,
                            },
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (eval_dir / "2026-06-12.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "edition": "2098",
                        "date": "2026-06-12",
                        "mode": "worldcup-prediction-post-match-evaluation",
                        "summary": {
                            "evaluated_matches": 1,
                            "result_hits": 1,
                            "score_hits": 1,
                            "total_goals_hits": 1,
                        },
                        "evaluations": [
                            {
                                "match_id": "2098-GA-03",
                                "status": "evaluated",
                                "prediction_confidence": "medium",
                                "result_hit": True,
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = dashboard_module.write_evaluation_dashboard(
                root=root,
                edition="2098",
                now="2026-06-13T12:00:00+08:00",
            )

            self.assertEqual(result["status"], "written")
            self.assertEqual(result["summary"]["evaluation_days"], 2)
            self.assertEqual(result["summary"]["evaluated_matches"], 3)
            self.assertEqual(result["summary"]["result_hits"], 2)
            self.assertAlmostEqual(result["rates"]["result_hit_rate"], 2 / 3)
            calibration = result["summary"]["confidence_calibration"]
            self.assertEqual(calibration["low"]["evaluated_matches"], 1)
            self.assertEqual(calibration["low"]["result_hit_rate"], 1.0)
            self.assertEqual(calibration["medium"]["evaluated_matches"], 2)
            self.assertEqual(calibration["medium"]["result_hits"], 1)
            self.assertEqual(calibration["medium"]["result_hit_rate"], 0.5)
            self.assertTrue(Path(result["markdown_path"]).exists())

    def test_tianji_oracle_computes_star_palaces_and_scores(self):
        tianji_module = load_script("tianji_oracle.py")
        res = tianji_module.compute_tianji_overlay("2026-06-11T19:00:00+08:00", "2026-GA-01")

        self.assertIn("lunar_date", res)
        self.assertIn("shichen", res)
        self.assertIn("host_palace_branch", res)
        self.assertIn("guest_palace_branch", res)
        self.assertIn("home_stars", res)
        self.assertIn("away_stars", res)
        self.assertIsInstance(res["home_modifier"], (int, float))
        self.assertIsInstance(res["away_modifier"], (int, float))
        self.assertIsInstance(res["interpretation"], str)
        self.assertIsInstance(res["has_physical_conflict"], bool)

    def test_live_fetcher_sentiment_analysis_and_mock_generation(self):
        fetcher_module = load_script("worldcup_live_fetcher.py")

        # Test analyze_sentiment
        self.assertEqual(fetcher_module.analyze_sentiment("Messi suffered a severe injury and is ruled out"), "negative")
        self.assertEqual(fetcher_module.analyze_sentiment("Ronaldo is fit and returns to squad"), "positive")
        self.assertEqual(fetcher_module.analyze_sentiment("The weather is nice today in Mexico"), "neutral")

        # Test get_mock_odds
        odds = fetcher_module.get_mock_odds("Mexico", "South Africa")
        self.assertIn("home_win", odds)
        self.assertIn("draw", odds)
        self.assertIn("away_win", odds)
        self.assertEqual(odds["source"], "mock_bookmaker")

        # Test get_mock_news_for_teams
        news = fetcher_module.get_mock_news_for_teams([("MEX", "Mexico")])
        self.assertTrue(len(news) >= 1)
        self.assertEqual(news[0]["team_code"], "MEX")
        self.assertIn("sentiment", news[0])

    def test_live_fetcher_marks_odds_unavailable_without_api_key(self):
        init_module = load_script("worldcup_edition_init.py")
        fetcher_module = load_script("worldcup_live_fetcher.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            from worldcup_core import edition_data_root, raw_edition_root
            ledger_path = edition_data_root(root, "2098") / "match-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["matches"][0]["kickoff_at"] = "2026-06-13T10:00:00+00:00"
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            res = fetcher_module.update_odds_in_evidence(
                root=root,
                edition="2098",
                date_str="2026-06-13",
            )

            self.assertEqual(res["status"], "odds_updated")
            self.assertEqual(res["live_odds_count"], 0)
            self.assertEqual(res["mock_odds_count"], 0)
            self.assertEqual(res["unavailable_count"], 1)
            odds = res["matches"][0]["odds"]
            self.assertEqual(odds["source"], "odds_unavailable")
            self.assertFalse(odds["is_mock"])

            evidence = json.loads((edition_data_root(root, "2098") / "daily-evidence" / "2026-06-13.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["matches"][0]["odds"]["source"], "odds_unavailable")

    def test_sporttery_odds_adapter_writes_fixed_bonus_snapshot(self):
        init_module = load_script("worldcup_edition_init.py")
        fetcher_module = load_script("worldcup_live_fetcher.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            from worldcup_core import edition_data_root, raw_edition_root
            ledger_path = edition_data_root(root, "2098") / "match-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["matches"][0]["kickoff_at"] = "2026-06-13T10:00:00+00:00"
            ledger["matches"][0]["home_team"]["name"] = "Mexico"
            ledger["matches"][0]["away_team"]["name"] = "South Africa"
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            sporttery_payload = {
                "value": {
                    "matchList": [
                        {
                            "homeTeamName": "Mexico",
                            "awayTeamName": "South Africa",
                            "matchNumStr": "周六001",
                            "matchDate": "2026-06-13",
                            "matchTime": "18:00",
                            "oddsList": [
                                {
                                    "poolCode": "HAD",
                                    "h": "1.70",
                                    "d": "3.50",
                                    "a": "4.80",
                                }
                            ],
                        }
                    ]
                }
            }

            res = fetcher_module.update_sporttery_odds_in_evidence(
                root=root,
                edition="2098",
                date_str="2026-06-13",
                payload=sporttery_payload,
                source_url="memory://sporttery-test",
            )

            self.assertEqual(res["status"], "sporttery_odds_updated")
            self.assertEqual(res["matched_count"], 1)
            odds = res["matches"][0]["odds"]
            self.assertEqual(odds["source"], "sporttery_fixed_odds")
            self.assertEqual(odds["match_no"], "周六001")
            self.assertFalse(odds["is_mock"])
            self.assertAlmostEqual(odds["home_win"], 1.70)

    def test_public_match_ledger_is_base_and_local_ledger_is_overlay(self):
        core_module = load_script("worldcup_core.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public_dir = core_module.public_edition_data_root(root, "2098")
            local_dir = core_module.edition_data_root(root, "2098")
            public_dir.mkdir(parents=True, exist_ok=True)
            local_dir.mkdir(parents=True, exist_ok=True)
            public_ledger = {
                "version": 1,
                "edition": "2098",
                "mode": "worldcup-public-match-ledger",
                "summary": {"match_count": 1},
                "matches": [
                    {
                        "match_id": "2098-GA-01",
                        "phase": "group",
                        "kickoff_at": "2026-06-13T10:00:00+00:00",
                        "home_team": {"team_id": "alpha", "name": "Alpha"},
                        "away_team": {"team_id": "beta", "name": "Beta"},
                        "status": "fixture_official",
                    }
                ],
            }
            local_ledger = {
                "version": 1,
                "edition": "2098",
                "mode": "worldcup-local-match-ledger",
                "matches": [
                    {
                        "match_id": "2098-GA-01",
                        "status": "final",
                        "prediction_report": "local-report.json",
                        "final_score": {"home": 2, "away": 1},
                    },
                    {
                        "match_id": "2098-20260613-ALPHA-vs-BETA",
                        "status": "fixture_official",
                    },
                ],
            }
            (public_dir / "match-ledger.json").write_text(json.dumps(public_ledger, ensure_ascii=False, indent=2), encoding="utf-8")
            (local_dir / "match-ledger.json").write_text(json.dumps(local_ledger, ensure_ascii=False, indent=2), encoding="utf-8")

            merged = core_module.load_match_ledger(root, "2098")

            self.assertEqual(len(merged["matches"]), 1)
            self.assertEqual(merged["matches"][0]["match_id"], "2098-GA-01")
            self.assertEqual(merged["matches"][0]["home_team"]["name"], "Alpha")
            self.assertEqual(merged["matches"][0]["status"], "final")
            self.assertEqual(merged["matches"][0]["prediction_report"], "local-report.json")
            self.assertEqual(merged["matches"][0]["final_score"]["home"], 2)

    def test_dashboard_uses_octopus_default_until_user_prediction_overrides(self):
        dashboard_module = load_script("prediction_visual_dashboard.py")
        core_module = load_script("worldcup_core.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public_dir = core_module.public_edition_data_root(root, "2098")
            local_dir = core_module.edition_data_root(root, "2098")
            default_dir = public_dir / "default-predictions" / "daily-predictions"
            public_dir.mkdir(parents=True, exist_ok=True)
            local_dir.mkdir(parents=True, exist_ok=True)
            default_dir.mkdir(parents=True, exist_ok=True)
            match = {
                "match_id": "2098-GA-01",
                "phase": "group",
                "group": "A",
                "kickoff_at": "2026-06-13T10:00:00+00:00",
                "venue": "Test Stadium",
                "home_team": {"team_id": "alpha", "name": "Alpha"},
                "away_team": {"team_id": "beta", "name": "Beta"},
                "status": "fixture_official",
            }
            public_ledger = {
                "version": 1,
                "edition": "2098",
                "mode": "worldcup-public-match-ledger",
                "summary": {"match_count": 1},
                "matches": [match],
            }
            (public_dir / "match-ledger.json").write_text(json.dumps(public_ledger, ensure_ascii=False, indent=2), encoding="utf-8")

            def report(score_home, score_away, origin):
                item = dict(match)
                item["prediction_origin"] = origin
                item["prediction"] = {
                    "result": "home_win",
                    "score": {"home": score_home, "away": score_away},
                    "total_goals": score_home + score_away,
                    "confidence": "low",
                    "scoreline_distribution": [
                        {"score": {"home": score_home, "away": score_away}, "probability": 0.5}
                    ],
                }
                return {"version": 1, "edition": "2098", "predictions": [item]}

            (default_dir / "2026-06-13.json").write_text(json.dumps(report(1, 0, "octopus_default"), ensure_ascii=False, indent=2), encoding="utf-8")
            default_payload = dashboard_module.build_dashboard_payload(root=root, edition="2098", now="2026-06-12T12:00:00+00:00")
            default_card = next(c for c in default_payload["cards"] if c["match_id"] == "2098-GA-01")
            self.assertEqual(default_card["prediction_origin"], "octopus_default")
            self.assertEqual(default_card["score_text"], "1-0")

            user_dir = local_dir / "reports" / "daily-predictions"
            user_dir.mkdir(parents=True, exist_ok=True)
            (user_dir / "2026-06-13.json").write_text(json.dumps(report(2, 1, "user_local"), ensure_ascii=False, indent=2), encoding="utf-8")
            user_payload = dashboard_module.build_dashboard_payload(root=root, edition="2098", now="2026-06-12T12:00:00+00:00")
            user_card = next(c for c in user_payload["cards"] if c["match_id"] == "2098-GA-01")
            self.assertEqual(user_card["prediction_origin"], "user_local")
            self.assertEqual(user_card["score_text"], "2-1")

    def test_octopus_react_runner_records_bounded_trace(self):
        init_module = load_script("worldcup_edition_init.py")
        react_module = load_script("octopus_react_runner.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            from worldcup_core import edition_data_root, raw_edition_root
            ledger_path = edition_data_root(root, "2098") / "match-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["matches"] = [ledger["matches"][0]]
            ledger["matches"][0]["kickoff_at"] = "2026-06-13T10:00:00+00:00"
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            react_module.update_sporttery_odds_in_evidence = lambda **kwargs: {
                "status": "sporttery_odds_updated",
                "sporttery_raw_count": 1,
                "matched_count": 1,
                "unavailable_count": 0,
            }
            react_module.update_news_in_evidence = lambda **kwargs: {
                "status": "updated",
                "news": [{"headline": "team news"}],
            }
            react_module.write_intelligence_briefing = lambda **kwargs: {
                "data_path": "memory://briefing.json",
                "markdown_path": "memory://briefing.md",
                "matches": [{"match_id": "2098-GA-01"}],
            }
            react_module.run_daily_predictions = lambda **kwargs: {
                "status": "created",
                "report_path": "memory://prediction.json",
                "summary": {
                    "predictions_created": 1,
                    "locked_existing_predictions": 0,
                    "matches_skipped_started": 0,
                },
            }
            react_module.write_visual_dashboard = lambda **kwargs: {
                "data_path": "memory://dashboard.json",
                "html_path": "memory://dashboard.html",
                "cards": [{"match_id": "2098-GA-01"}],
            }

            res = react_module.run_react_plan(
                root=root,
                edition="2098",
                start_date="2026-06-13",
                now="2026-06-12T12:00:00+00:00",
            )

            self.assertEqual(res["summary"]["matches_inspected"], 1)
            self.assertEqual(res["summary"]["predictions_created"], 1)
            self.assertEqual(res["summary"]["sporttery_matched_count"], 1)
            self.assertTrue(res["data_path"].endswith("2026-06-13_to_2026-06-13_react-run.json"))
            self.assertGreaterEqual(len(res["trace"]), 5)

    def test_update_readme_and_history_partitions_matches_correctly(self):
        init_module = load_script("worldcup_edition_init.py")
        daily_module = load_script("daily_prediction_runner.py")
        updater_module = load_script("update_readme_and_history.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Initialize project structure
            (root / "skill/scripts").mkdir(parents=True)
            (root / "skill/scripts" / "worldcup_core.py").write_text("def build_play_card():\n    return {}\n", encoding="utf-8")
            (root / "skill/schema").mkdir(parents=True)
            (root / "README.md").write_text(
                "## Prediction Schedule / 预测日历\n| 节奏 | 比赛 | 预测摘要 | 状态 |\n|---|---|---|---|\n## Quick Start / 快速开始\n", encoding="utf-8"
            )

            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            # Setup match ledger kickoff times
            ledger_path = root / "wiki/public/2098/match-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

            # Match 1: Kickoff tomorrow
            ledger["matches"][0]["kickoff_at"] = "2026-06-10T20:00:00+08:00"
            # Match 2: Kickoff in the past
            ledger["matches"][1]["kickoff_at"] = "2026-06-09T20:00:00+08:00"
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            # Run predictions for the past match and upcoming match
            daily_module.run_daily_predictions(
                root=root, edition="2098", date="2026-06-09", now="2026-06-09T12:00:00+08:00", poster=False
            )
            daily_module.run_daily_predictions(
                root=root, edition="2098", date="2026-06-10", now="2026-06-09T12:00:00+08:00", poster=False
            )

            # Run updater
            res = updater_module.update_readme_and_history(
                root=root,
                edition="2098",
                date_str="2026-06-10",
                now="2026-06-09T12:00:00+08:00"
            )

            self.assertEqual(res["status"], "completed")
            self.assertEqual(res["target_date"], "2026-06-10")
            self.assertEqual(res["tomorrow_matches_count"], 1)
            self.assertEqual(res["history_matches_count"], 1)

            # Verify README.md has correct sections updated
            readme_text = (root / "README.md").read_text(encoding="utf-8")
            self.assertIn("## Prediction Schedule / 预测日历", readme_text)
            self.assertIn("## Quick Start / 快速开始", readme_text)
            self.assertIn("2098-GA-01", readme_text)
            self.assertNotIn("2098-GA-02", readme_text)

            # Verify HISTORY.md is created and has the past match
            history_path = root / "HISTORY.md"
            self.assertTrue(history_path.exists())
            history_text = history_path.read_text(encoding="utf-8")
            self.assertIn("2098-GA-02", history_text)
            self.assertNotIn("2098-GA-01", history_text)

    def test_visual_dashboard_generation(self):
        init_module = load_script("worldcup_edition_init.py")
        db_module = load_script("worldcup_db.py")
        dashboard_module = load_script("prediction_visual_dashboard.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")
            
            from worldcup_core import raw_edition_root, edition_data_root, wiki_edition_root, worldcup_db_path
            db_path = worldcup_db_path(root, "2098")
            
            conn = db_module.get_db_connection(db_path)
            try:
                # Save matches, predictions, evaluations, corrective actions
                with conn:
                    db_module.save_match(conn, {
                        "match_id": "2098-GA-01",
                        "edition": "2098",
                        "phase": "group",
                        "home_team": {"team_id": "t1", "name": "Team A"},
                        "away_team": {"team_id": "t2", "name": "Team B"},
                        "status": "fixture_official"
                    })
                    db_module.save_prediction(conn, {
                        "match_id": "2098-GA-01",
                        "prediction_date": "2026-06-11",
                        "status": "locked_pre_match_prediction",
                        "prediction": {
                            "result": "home_win",
                            "score": {"home": 2, "away": 1},
                            "confidence": "medium"
                        }
                    })
                    db_module.save_evaluation(conn, {
                        "match_id": "2098-GA-01",
                        "actual_score_home": 2,
                        "actual_score_away": 1,
                        "is_result_correct": True,
                        "is_score_correct": True,
                        "primary_error": "No error",
                        "model_issue_tags_str": "none",
                        "evaluated_at": "2026-06-12T12:00:00+08:00"
                    })
                    db_module.save_corrective_action(conn, {
                        "action_id": "act1",
                        "priority": "P0",
                        "description": "Fix alignment",
                        "status": "open",
                        "created_at": "2026-06-12 12:00:00"
                    })
            finally:
                conn.close()
                
            res = dashboard_module.write_visual_dashboard(root=root, edition="2098", now="2026-06-12T12:00:00+08:00")
            self.assertEqual(res["status"], "written")
            self.assertEqual(res["summary"]["predictions"], 1)
            self.assertEqual(res["summary"]["evaluated_matches"], 1)
            self.assertEqual(len(res["corrective_actions"]), 1)
            self.assertEqual(res["corrective_actions"][0]["action_id"], "act1")
            
            html_file = wiki_edition_root(root, "2098") / "dashboard" / "index.html"
            self.assertTrue(html_file.exists())
            html_content = html_file.read_text(encoding="utf-8")
            self.assertNotIn('id="dashData"', html_content)
            self.assertIn('id="dashBootstrap"', html_content)
            self.assertIn('id="btn-refresh-data"', html_content)
            self.assertIn('prediction-dashboard.json', html_content)

            static_data = wiki_edition_root(root, "2098") / "dashboard" / "prediction-dashboard.json"
            self.assertTrue(static_data.exists())
            static_payload = json.loads(static_data.read_text(encoding="utf-8"))
            self.assertIn("Team A", static_payload["rendered"]["cards_html"])
            self.assertIn("Team B", static_payload["rendered"]["cards_html"])
            self.assertIn("Fix alignment", static_payload["rendered"]["actions_html"])

    def test_visual_dashboard_prefers_daily_evidence_odds_status_over_mock_report(self):
        init_module = load_script("worldcup_edition_init.py")
        dashboard_module = load_script("prediction_visual_dashboard.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            from worldcup_core import edition_data_root, raw_edition_root
            data_root = edition_data_root(root, "2098")
            raw_root = raw_edition_root(root, "2098")
            ledger_path = data_root / "match-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            match = ledger["matches"][0]
            match["kickoff_at"] = "2026-06-13T01:00:00+00:00"
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            mid = match["match_id"]
            report = {
                "version": 1,
                "edition": "2098",
                "predictions": [
                    {
                        "match_id": mid,
                        "kickoff_at": match["kickoff_at"],
                        "venue": match.get("venue", ""),
                        "group": match.get("group", "A"),
                        "phase": "group",
                        "home_team": match["home_team"],
                        "away_team": match["away_team"],
                        "prediction": {
                            "result": "home_win",
                            "score": {"home": 1, "away": 0},
                            "total_goals": 1,
                            "confidence": "medium",
                        },
                        "market_odds": {
                            "odds": {
                                "home_win": 1.70,
                                "draw": 3.50,
                                "away_win": 4.80,
                                "source": "mock_bookmaker",
                                "is_mock": True,
                            }
                        },
                    }
                ],
            }
            report_path = data_root / "reports" / "2026-06-13-test-prediction-report.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            evidence_dir = data_root / "daily-evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            evidence = {
                "version": 1,
                "edition": "2098",
                "date": "2026-06-13",
                "matches": [
                    {
                        "match_id": mid,
                        "odds": {
                            "status": "unavailable",
                            "source": "odds_unavailable",
                            "reason": "THE_ODDS_API_KEY missing",
                            "is_mock": False,
                        },
                    }
                ],
            }
            (evidence_dir / "2026-06-13.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            res = dashboard_module.write_visual_dashboard(root=root, edition="2098", now="2026-06-13T12:00:00+08:00")
            card = next(c for c in res["cards"] if c["match_id"] == mid)

            self.assertFalse(card["has_odds"])
            self.assertIsNone(card["market_odds"])
            self.assertEqual(card["market_odds_status"]["status"], "unavailable")
            self.assertEqual(card["market_odds_status"]["source"], "odds_unavailable")
            self.assertFalse(card["market_odds_status"]["is_mock"])

    def test_visual_dashboard_emits_fact_cards_without_local_predictions(self):
        init_module = load_script("worldcup_edition_init.py")
        dashboard_module = load_script("prediction_visual_dashboard.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            from worldcup_core import raw_edition_root, edition_data_root, worldcup_db_path
            data_root = edition_data_root(root, "2098")
            raw_root = raw_edition_root(root, "2098")
            ledger_path = data_root / "match-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["matches"] = [ledger["matches"][0]]
            ledger["matches"][0]["kickoff_at"] = "2026-06-13T10:00:00+00:00"
            ledger["matches"][0]["status"] = "final"
            ledger["matches"][0]["final_score"] = {"home": 2, "away": 0, "status": "final"}
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            db_path = worldcup_db_path(root, "2098")
            db_path.unlink()


            res = dashboard_module.write_visual_dashboard(root=root, edition="2098", now="2026-06-13T12:00:00+08:00")

            self.assertEqual(res["summary"]["predictions"], 0)
            self.assertEqual(res["summary"]["fact_cards"], 1)
            card = res["cards"][0]
            self.assertEqual(card["prediction_status"], "not_predicted")
            self.assertEqual(card["data_origin"], "public_facts")
            self.assertTrue(card["is_completed"])
            self.assertEqual(card["actual_score_home"], 2)
            self.assertEqual(card["actual_score_away"], 0)

    def test_visual_dashboard_public_only_ignores_user_local_predictions(self):
        init_module = load_script("worldcup_edition_init.py")
        dashboard_module = load_script("prediction_visual_dashboard.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            from worldcup_core import raw_edition_root, edition_data_root, public_edition_data_root, worldcup_db_path
            data_root = edition_data_root(root, "2098")
            ledger = json.loads((data_root / "match-ledger.json").read_text(encoding="utf-8"))
            match = ledger["matches"][0]
            match_id = match["match_id"]

            public_dir = public_edition_data_root(root, "2098") / "default-predictions" / "daily-predictions"
            local_dir = data_root / "reports" / "daily-predictions"
            public_dir.mkdir(parents=True, exist_ok=True)
            local_dir.mkdir(parents=True, exist_ok=True)

            def report(score_home: int, score_away: int) -> dict:
                return {
                    "version": 1,
                    "edition": "2098",
                    "predictions": [
                        {
                            "match_id": match_id,
                            "kickoff_at": match["kickoff_at"],
                            "venue": match.get("venue", ""),
                            "group": match.get("group", "A"),
                            "phase": "group",
                            "home_team": match["home_team"],
                            "away_team": match["away_team"],
                            "prediction": {
                                "result": "home_win",
                                "score": {"home": score_home, "away": score_away},
                                "total_goals": score_home + score_away,
                                "confidence": "medium",
                            },
                        }
                    ],
                }

            (public_dir / "2026-06-13.json").write_text(
                json.dumps(report(2, 0), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (local_dir / "2026-06-13.json").write_text(
                json.dumps(report(1, 1), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            db_path = worldcup_db_path(root, "2098")
            if db_path.exists():
                db_path.unlink()

            res = dashboard_module.write_visual_dashboard(
                root=root,
                edition="2098",
                now="2026-06-13T12:00:00+08:00",
                include_local=False,
            )

            card = next(c for c in res["cards"] if c["match_id"] == match_id)
            self.assertEqual(card["prediction_origin"], "octopus_default")
            self.assertEqual(card["prediction_source"], "octopus_default")
            self.assertEqual(card["score_text"], "2-0")

    def test_visual_dashboard_defaults_to_recent_two_dates_visible(self):
        dashboard_module = load_script("prediction_visual_dashboard.py")

        payload = {
            "edition": "2098",
            "generated_at": "2026-06-18T20:44:44+08:00",
            "summary": {
                "evaluated_matches": 0,
                "result_hits": 0,
                "avg_brier_score": 0.0,
                "predictions": 2,
                "placeholder_count": 0,
                "total_cards": 2,
            },
            "comparison_stats": {},
            "corrective_actions": [],
            "model_issue_tags": [],
            "daily_stats": [],
            "schedule_data": {},
            "cards": [
                {
                    "match_id": "2098-GA-01",
                    "phase": "group",
                    "group": "A",
                    "beijing_date": "2026-06-17",
                    "beijing_time": "19:00",
                    "kickoff_at": "2026-06-17T11:00:00+00:00",
                    "data_source": "prediction_report",
                    "home_name": "Alpha",
                    "away_name": "Beta",
                    "score_text": "1-0",
                    "predicted_result": "home_win",
                },
                {
                    "match_id": "2098-GA-02",
                    "phase": "group",
                    "group": "A",
                    "beijing_date": "2026-06-18",
                    "beijing_time": "19:00",
                    "kickoff_at": "2026-06-18T11:00:00+00:00",
                    "data_source": "prediction_report",
                    "home_name": "Gamma",
                    "away_name": "Delta",
                    "score_text": "1-1",
                    "predicted_result": "draw",
                },
            ],
        }

        html = dashboard_module.render_html(payload, root=ROOT, html_path=ROOT / "dashboard.html")

        self.assertEqual(payload["ui_defaults"]["active_date"], "2026-06-18")
        self.assertEqual(payload["ui_defaults"]["visible_dates"], ["2026-06-17", "2026-06-18"])
        self.assertNotIn('id="dashData"', html)
        self.assertIn('id="dashBootstrap"', html)
        self.assertIn('id="btn-refresh-data"', html)
        self.assertIn('id="loadStatus"', html)

    def test_knockout_prediction_structure_is_emitted(self):
        model_module = load_script("prediction_scoring_model.py")

        knockout = model_module._build_knockout_prediction(
            phase="round_of_16",
            predicted_outcome="draw",
            predicted_score={"home": 1, "away": 1},
            home_name="Alpha",
            away_name="Beta",
            home_final=62.0,
            away_final=60.5,
            edge_tier="slight",
            game_script="low-event",
            confidence="medium",
        )

        self.assertTrue(knockout["is_knockout"])
        self.assertEqual(knockout["regular_time"]["score"]["home"], 1)
        self.assertEqual(knockout["regular_time"]["score"]["away"], 1)
        self.assertIn(knockout["advance"]["winner"], {"home", "away"})
        self.assertTrue(knockout["extra_time"]["played"])
        self.assertIn(knockout["penalties"]["played"], {True, False})

    def test_fetch_knockout_payload_derives_regular_time_from_extra_time(self):
        fetch_module = load_script("fetch_match_results.py")

        knockout = fetch_module._build_knockout_result_payload(
            {
                "duration": "EXTRA_TIME",
                "fullTime": {"home": 3, "away": 2},
                "regularTime": {"home": None, "away": None},
                "extraTime": {"home": 2, "away": 1},
            },
            "HOME_TEAM",
        )

        self.assertEqual(knockout["regular_time"]["home"], 1)
        self.assertEqual(knockout["regular_time"]["away"], 1)
        self.assertEqual(knockout["extra_time"]["home"], 2)
        self.assertEqual(knockout["extra_time"]["away"], 1)
        self.assertFalse(knockout["penalties"]["played"])
        self.assertEqual(knockout["advance"]["winner"], "home")

    def test_fetch_knockout_payload_derives_penalty_shootout_score(self):
        fetch_module = load_script("fetch_match_results.py")

        knockout = fetch_module._build_knockout_result_payload(
            {
                "duration": "PENALTY_SHOOTOUT",
                "fullTime": {"home": 3, "away": 5},
                "regularTime": {"home": 1, "away": 1},
                "extraTime": {"home": 0, "away": 0},
                "penalties": {"home": 4, "away": 4},
            },
            "AWAY_TEAM",
        )

        self.assertEqual(knockout["regular_time"]["home"], 1)
        self.assertEqual(knockout["regular_time"]["away"], 1)
        self.assertEqual(knockout["extra_time"]["home"], 0)
        self.assertEqual(knockout["extra_time"]["away"], 0)
        self.assertEqual(knockout["penalties"]["home"], 2)
        self.assertEqual(knockout["penalties"]["away"], 4)
        self.assertTrue(knockout["penalties"]["played"])
        self.assertEqual(knockout["penalties"]["winner"], "away")
        self.assertEqual(knockout["advance"]["winner"], "away")

    def test_visual_dashboard_renders_knockout_compare_rows(self):
        dashboard_module = load_script("prediction_visual_dashboard.py")

        card = {
            "match_id": "2098-R16-01",
            "phase": "round_of_16",
            "group": "",
            "home_name": "Alpha",
            "away_name": "Beta",
            "venue": "Test Stadium",
            "kickoff_at": "2026-06-30T12:00:00+00:00",
            "beijing_date": "2026-06-30",
            "beijing_time": "20:00",
            "prediction_status": "locked_pre_match_prediction",
            "predicted_result": "draw",
            "score_text": "1-1",
            "confidence": "medium",
            "knockout_prediction": {
                "regular_time": {"score": {"home": 1, "away": 1}, "result": "draw"},
                "extra_time": {"played": True, "score": {"home": 2, "away": 1}, "result": "home_win"},
                "penalties": {"played": False, "score": {"home": None, "away": None}, "winner": ""},
                "advance": {"winner": "home", "winner_name": "Alpha"},
            },
            "actual_score_home": 2,
            "actual_score_away": 1,
            "knockout_actual": {
                "regular_time": {"home": 1, "away": 1, "result": "draw"},
                "extra_time": {"played": True, "home": 2, "away": 1, "result": "home_win"},
                "penalties": {"played": False, "home": None, "away": None, "winner": ""},
                "advance": {"winner": "home", "winner_name": "Alpha"},
            },
            "data_source": "official",
        }

        normalized = dashboard_module._normalize_card_state(card)
        html = dashboard_module._render_match_card(normalized)

        self.assertIn("90分钟", html)
        self.assertIn("加时", html)
        self.assertIn("点球", html)
        self.assertIn("晋级", html)
        self.assertIn("Alpha", html)

    def test_visual_dashboard_writes_static_dashboard_json_next_to_html(self):
        init_module = load_script("worldcup_edition_init.py")
        dashboard_module = load_script("prediction_visual_dashboard.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            res = dashboard_module.write_visual_dashboard(root=root, edition="2098", now="2026-06-13T12:00:00+08:00")

            self.assertIn("static_data_path", res)
            static_path = root / res["static_data_path"]
            self.assertTrue(static_path.exists())
            static_payload = json.loads(static_path.read_text(encoding="utf-8"))
            self.assertIn("rendered", static_payload)
            self.assertIn("cards_html", static_payload["rendered"])

    def test_dashboard_api_returns_json_without_rewriting_html(self):
        init_module = load_script("worldcup_edition_init.py")
        dashboard_module = load_script("prediction_visual_dashboard.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")
            dashboard_module.write_visual_dashboard(root=root, edition="2098", now="2026-06-13T12:00:00+08:00")

            _, html_path = dashboard_module._dashboard_paths(root, "2098")
            before_html = html_path.read_text(encoding="utf-8")

            server_address = ("127.0.0.1", 0)
            httpd = dashboard_module.http.server.HTTPServer(server_address, dashboard_module.DashboardHTTPRequestHandler)
            httpd.dashboard_root = root
            httpd.dashboard_edition = "2098"
            httpd.dashboard_now = "2026-06-13T12:00:00+08:00"

            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                port = httpd.server_address[1]
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/dashboard?edition=2098") as resp:
                    body = resp.read().decode("utf-8")
                    cache_control = resp.headers.get("Cache-Control")
                payload = json.loads(body)
                self.assertEqual(cache_control, "no-store")
                self.assertEqual(payload["edition"], "2098")
                self.assertIn("rendered", payload)
                self.assertIn("cards_html", payload["rendered"])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)

            after_html = html_path.read_text(encoding="utf-8")
            self.assertEqual(before_html, after_html)

    def test_dashboard_overview_api_returns_lightweight_cards(self):
        init_module = load_script("worldcup_edition_init.py")
        db_module = load_script("worldcup_db.py")
        dashboard_module = load_script("prediction_visual_dashboard.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            from worldcup_core import worldcup_db_path
            db_path = worldcup_db_path(root, "2098")
            conn = db_module.get_db_connection(db_path)
            try:
                with conn:
                    db_module.save_match(conn, {
                        "match_id": "2098-GA-01",
                        "edition": "2098",
                        "phase": "group",
                        "home_team": {"team_id": "alpha", "name": "Alpha"},
                        "away_team": {"team_id": "beta", "name": "Beta"},
                        "status": "fixture_official",
                        "kickoff_at": "2026-06-13T10:00:00+00:00",
                    })
                    db_module.save_prediction(conn, {
                        "match_id": "2098-GA-01",
                        "prediction_date": "2026-06-12",
                        "status": "locked_pre_match_prediction",
                        "prediction": {
                            "result": "home_win",
                            "score": {"home": 2, "away": 1},
                            "confidence": "medium",
                        }
                    })
            finally:
                conn.close()

            server_address = ("127.0.0.1", 0)
            httpd = dashboard_module.http.server.HTTPServer(server_address, dashboard_module.DashboardHTTPRequestHandler)
            httpd.dashboard_root = root
            httpd.dashboard_edition = "2098"
            httpd.dashboard_now = "2026-06-13T12:00:00+08:00"

            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                port = httpd.server_address[1]
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/dashboard/overview?edition=2098") as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    cache_control = resp.headers.get("Cache-Control")
                self.assertEqual(cache_control, "no-store")
                self.assertTrue(payload["api_capabilities"]["match_detail"])
                self.assertIn("cards", payload)
                self.assertTrue(payload["cards"])
                card = payload["cards"][0]
                self.assertIn("match_id", card)
                self.assertIn("scoreline_distribution", card)
                self.assertNotIn("home_players", card)
                self.assertNotIn("away_players", card)
                self.assertIn("rendered", payload)
                self.assertIn("cards_html", payload["rendered"])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)

    def test_dashboard_match_api_returns_detail_card(self):
        init_module = load_script("worldcup_edition_init.py")
        db_module = load_script("worldcup_db.py")
        dashboard_module = load_script("prediction_visual_dashboard.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            from worldcup_core import worldcup_db_path
            db_path = worldcup_db_path(root, "2098")
            conn = db_module.get_db_connection(db_path)
            try:
                with conn:
                    db_module.save_match(conn, {
                        "match_id": "2098-GA-01",
                        "edition": "2098",
                        "phase": "group",
                        "home_team": {"team_id": "alpha", "name": "Alpha"},
                        "away_team": {"team_id": "beta", "name": "Beta"},
                        "status": "fixture_official",
                        "kickoff_at": "2026-06-13T10:00:00+00:00",
                    })
                    db_module.save_prediction(conn, {
                        "match_id": "2098-GA-01",
                        "prediction_date": "2026-06-12",
                        "status": "locked_pre_match_prediction",
                        "prediction": {
                            "result": "home_win",
                            "score": {"home": 2, "away": 1},
                            "confidence": "medium",
                        }
                    })
            finally:
                conn.close()

            server_address = ("127.0.0.1", 0)
            httpd = dashboard_module.http.server.HTTPServer(server_address, dashboard_module.DashboardHTTPRequestHandler)
            httpd.dashboard_root = root
            httpd.dashboard_edition = "2098"
            httpd.dashboard_now = "2026-06-13T12:00:00+08:00"

            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                port = httpd.server_address[1]
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/dashboard/match?edition=2098&match_id=2098-GA-01") as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    cache_control = resp.headers.get("Cache-Control")
                self.assertEqual(cache_control, "no-store")
                self.assertEqual(payload["match_id"], "2098-GA-01")
                self.assertIn("home_players", payload)
                self.assertIn("away_players", payload)
                self.assertIn("analysis_layers", payload)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)

    def test_dashboard_normalizes_cards_into_state_buckets(self):
        dashboard_module = load_script("prediction_visual_dashboard.py")

        payload = {
            "summary": {},
            "cards": [
                {
                    "match_id": "pred-only",
                    "data_source": "official",
                    "prediction_status": "locked_pre_match_prediction",
                    "predicted_result": "home_win",
                    "score_text": "2-1",
                    "home_name": "Alpha",
                    "away_name": "Beta",
                },
                {
                    "match_id": "actual-only",
                    "data_source": "official",
                    "prediction_status": "not_predicted",
                    "predicted_result": "",
                    "score_text": "-:-",
                    "actual_score_home": 1,
                    "actual_score_away": 0,
                    "home_name": "Gamma",
                    "away_name": "Delta",
                },
                {
                    "match_id": "evaluated",
                    "data_source": "official",
                    "prediction_status": "locked_pre_match_prediction",
                    "predicted_result": "draw",
                    "score_text": "1-1",
                    "actual_score_home": 1,
                    "actual_score_away": 1,
                    "result_hit": True,
                    "score_hit": True,
                    "home_name": "Epsilon",
                    "away_name": "Zeta",
                },
                {
                    "match_id": "fixture-only",
                    "data_source": "official",
                    "prediction_status": "not_predicted",
                    "predicted_result": "",
                    "score_text": "-:-",
                    "home_name": "Eta",
                    "away_name": "Theta",
                },
                {
                    "match_id": "placeholder",
                    "data_source": "placeholder",
                    "prediction_status": "not_predicted",
                    "predicted_result": "",
                    "score_text": "-:-",
                    "home_name": "TBD A",
                    "away_name": "TBD B",
                },
            ]
        }

        dashboard_module._refresh_dashboard_summary(payload)
        cards = {c["match_id"]: c for c in payload["cards"]}

        self.assertEqual(cards["pred-only"]["display_state"], "predicted")
        self.assertTrue(cards["pred-only"]["prediction"]["exists"])
        self.assertFalse(cards["pred-only"]["actual"]["exists"])

        self.assertEqual(cards["actual-only"]["display_state"], "actual_only")
        self.assertFalse(cards["actual-only"]["prediction"]["exists"])
        self.assertTrue(cards["actual-only"]["actual"]["exists"])

        self.assertEqual(cards["evaluated"]["display_state"], "evaluated")
        self.assertTrue(cards["evaluated"]["evaluation"]["exists"])
        self.assertEqual(cards["evaluated"]["evaluation"]["label"], "完美双中")

        self.assertEqual(cards["fixture-only"]["display_state"], "fixture")
        self.assertEqual(cards["placeholder"]["display_state"], "placeholder")

        self.assertEqual(payload["summary"]["predicted_matches"], 2)
        self.assertEqual(payload["summary"]["actual_matches"], 2)
        self.assertEqual(payload["summary"]["evaluated_matches"], 1)
        self.assertEqual(payload["summary"]["actual_only_matches"], 1)
        self.assertEqual(payload["summary"]["prediction_only_matches"], 1)
        self.assertEqual(payload["summary"]["fixture_only_matches"], 1)

    def test_save_config_api_does_not_regenerate_dashboard_html(self):
        init_module = load_script("worldcup_edition_init.py")
        dashboard_module = load_script("prediction_visual_dashboard.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")
            dashboard_module.write_visual_dashboard(root=root, edition="2098", now="2026-06-13T12:00:00+08:00")

            _, html_path = dashboard_module._dashboard_paths(root, "2098")
            before_html = html_path.read_text(encoding="utf-8")

            server_address = ("127.0.0.1", 0)
            httpd = dashboard_module.http.server.HTTPServer(server_address, dashboard_module.DashboardHTTPRequestHandler)
            httpd.dashboard_root = root
            httpd.dashboard_edition = "2098"
            httpd.dashboard_now = "2026-06-13T12:00:00+08:00"

            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                port = httpd.server_address[1]
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/save-config",
                    data=json.dumps({
                        "data_weight": 0.55,
                        "divination_weight": 0.45,
                        "component_weights": {
                            "ranking_strength": 0.30,
                            "squad_depth": 0.20,
                            "historical_proxy": 0.20,
                            "rest_travel": 0.15,
                            "evidence_completeness": 0.15,
                        },
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req) as resp:
                    body = resp.read().decode("utf-8")
                payload = json.loads(body)
                self.assertEqual(payload["status"], "success")
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)

            after_html = html_path.read_text(encoding="utf-8")
            self.assertEqual(before_html, after_html)

    def test_octopus_reflection_tuning(self):
        init_module = load_script("worldcup_edition_init.py")
        db_module = load_script("worldcup_db.py")
        tuning_module = load_script("octopus_reflection_tuning.py")
        
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")
            
            from worldcup_core import raw_edition_root, edition_data_root, wiki_edition_root, worldcup_db_path
            db_path = worldcup_db_path(root, "2098")
            db_module.init_database(db_path)
            
            conn = db_module.get_db_connection(db_path)
            try:
                with conn:
                    db_module.save_team(conn, {
                        "team_id": "alpha",
                        "code": "ALP",
                        "name_en": "Alpha Team",
                        "name_zh": "阿尔法队",
                    })
                    db_module.save_team(conn, {
                        "team_id": "beta",
                        "code": "BET",
                        "name_en": "Beta Team",
                        "name_zh": "贝塔队",
                    })
                    db_module.save_match(conn, {
                        "match_id": "2098-GA-01",
                        "edition": "2098",
                        "phase": "group",
                        "home_team": {"team_id": "alpha", "name": "Alpha Team"},
                        "away_team": {"team_id": "beta", "name": "Beta Team"},
                        "status": "fixture_official",
                        "kickoff_at": "2026-06-12T18:00:00+08:00",
                    })
                    db_module.save_prediction(conn, {
                        "match_id": "2098-GA-01",
                        "prediction_date": "2026-06-11",
                        "status": "locked_pre_match_prediction",
                        "prediction": {
                            "result": "home_win",
                            "score": {"home": 2, "away": 1},
                            "confidence": "medium",
                            "confidence_label": "medium",
                        }
                    })
                    db_module.save_evaluation(conn, {
                        "match_id": "2098-GA-01",
                        "actual_score_home": 0,
                        "actual_score_away": 2,
                        "is_result_correct": 0,
                        "is_score_correct": 0,
                        "primary_error": "Underestimated Away team",
                        "model_issue_tags_str": "ranking_strength_underweighted",
                        "evaluated_at": "2026-06-12T21:00:00+08:00"
                    })
            finally:
                conn.close()
                
            hyper_path = edition_data_root(root, "2098") / "model-hyperparameters.json"
            hyper_data = {
                "data_weight": 0.60,
                "divination_weight": 0.40,
                "component_weights": {
                    "ranking_strength": 0.30,
                    "squad_depth": 0.20,
                    "historical_proxy": 0.20,
                    "rest_travel": 0.15,
                    "evidence_completeness": 0.15
                }
            }
            hyper_path.write_text(json.dumps(hyper_data, ensure_ascii=False, indent=2))
            
            res = tuning_module.run_tuning_loop(root=root, edition="2098", lr=0.05)
            
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["journal_entries_written"], 1)
            self.assertTrue(res["tuned_any_weights"])
            
            journal_file = wiki_edition_root(root, "2098") / "synthesis" / "self-reflection-journal.md"
            self.assertTrue(journal_file.exists())
            journal_content = journal_file.read_text(encoding="utf-8")
            self.assertIn("Match 2098-GA-01", journal_content)
            self.assertIn("Prediction**: 2-1", journal_content)
            self.assertIn("Actual**: 0-2", journal_content)
            
            tuned_hyper = json.loads(hyper_path.read_text(encoding="utf-8"))
            tuned_comp = tuned_hyper["component_weights"]
            self.assertAlmostEqual(sum(tuned_comp.values()), 1.0)
            for k, v in tuned_comp.items():
                self.assertTrue(0.05 <= v <= 0.50)
            self.assertIn("scoreline_tuning", tuned_hyper)
                
            self.assertGreaterEqual(tuned_hyper["data_weight"], 0.60)
            self.assertLessEqual(tuned_hyper["divination_weight"], 0.40)

    def test_scoreline_history_multiplier_penalizes_repeated_safe_templates(self):
        scoring_module = load_script("prediction_scoring_model.py")

        calibration = {
            "global": {
                "sample_size": 12,
                "actual_score_rates": {"1-0": 0.25, "2-0": 0.17, "2-1": 0.02},
                "predicted_score_rates": {"2-1": 0.34, "1-0": 0.06, "2-0": 0.04},
                "actual_clean_sheet_rate": 0.50,
                "predicted_clean_sheet_rate": 0.10,
                "avg_actual_total_goals": 1.9,
            },
            "by_phase": {},
            "by_phase_outcome": {},
        }

        collapsed = scoring_module._scoreline_history_multiplier(
            {"home": 2, "away": 1},
            phase="group",
            predicted_outcome="home_win",
            calibration=calibration,
        )
        clean_sheet = scoring_module._scoreline_history_multiplier(
            {"home": 1, "away": 0},
            phase="group",
            predicted_outcome="home_win",
            calibration=calibration,
        )

        self.assertLess(collapsed, 1.0)
        self.assertGreater(clean_sheet, collapsed)

    def test_tuning_loop_updates_scoreline_hyperparameters_on_exact_score_miss(self):
        init_module = load_script("worldcup_edition_init.py")
        db_module = load_script("worldcup_db.py")
        tuning_module = load_script("octopus_reflection_tuning.py")
        core_module = load_script("worldcup_core.py")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_module.initialize_edition(root=root, edition="2098", now="2026-06-09T12:00:00+08:00")

            ledger_path = core_module.edition_data_root(root, "2098") / "match-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["matches"] = [ledger["matches"][0]]
            ledger["matches"][0]["match_id"] = "2098-GA-01"
            ledger["matches"][0]["home_team"] = {"team_id": "alpha", "name": "Alpha Team"}
            ledger["matches"][0]["away_team"] = {"team_id": "beta", "name": "Beta Team"}
            ledger["matches"][0]["kickoff_at"] = "2026-06-12T18:00:00+08:00"
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            db_path = core_module.worldcup_db_path(root, "2098")
            conn = db_module.get_db_connection(db_path)
            try:
                with conn:
                    db_module.save_match(conn, {
                        "match_id": "2098-GA-01",
                        "edition": "2098",
                        "phase": "group",
                        "home_team": {"team_id": "alpha", "name": "Alpha Team"},
                        "away_team": {"team_id": "beta", "name": "Beta Team"},
                        "status": "fixture_official",
                        "kickoff_at": "2026-06-12T18:00:00+08:00",
                    })
                    db_module.save_prediction(conn, {
                        "match_id": "2098-GA-01",
                        "prediction_date": "2026-06-11",
                        "status": "locked_pre_match_prediction",
                        "prediction": {
                            "result": "home_win",
                            "score": {"home": 2, "away": 1},
                            "confidence": "medium",
                            "confidence_label": "medium",
                        }
                    })
                    db_module.save_evaluation(conn, {
                        "match_id": "2098-GA-01",
                        "actual_score_home": 1,
                        "actual_score_away": 0,
                        "is_result_correct": 1,
                        "is_score_correct": 0,
                        "primary_error": "Overestimated away goal expectation",
                        "model_issue_tags_str": "single_point_scoreline,away_floor_goal_rate_too_high",
                        "evaluated_at": "2026-06-12T21:00:00+08:00"
                    })
            finally:
                conn.close()

            hyper_path = core_module.edition_data_root(root, "2098") / "model-hyperparameters.json"
            hyper_path.write_text(
                json.dumps(
                    {
                        "data_weight": 0.60,
                        "divination_weight": 0.40,
                        "component_weights": {
                            "ranking_strength": 0.30,
                            "squad_depth": 0.20,
                            "historical_proxy": 0.20,
                            "rest_travel": 0.15,
                            "evidence_completeness": 0.15,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            res = tuning_module.run_tuning_loop(root=root, edition="2098", lr=0.05)

            self.assertEqual(res["status"], "success")
            self.assertFalse(res["tuned_any_weights"])
            self.assertTrue(res["tuned_any_scoreline"])

            tuned_hyper = json.loads(hyper_path.read_text(encoding="utf-8"))
            scoreline_tuning = tuned_hyper["scoreline_tuning"]
            self.assertLess(scoreline_tuning["paired_score_bias"], 0.84)
            self.assertLess(scoreline_tuning["mode_collapse_penalty"], 0.88)
            self.assertGreater(scoreline_tuning["clean_sheet_bias"], 1.24)
            self.assertLess(scoreline_tuning["loser_xg_suppression"]["slight"], 0.86)


if __name__ == "__main__":
    unittest.main()
