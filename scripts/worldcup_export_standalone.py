#!/usr/bin/env python3
"""Export the World Cup predictor as a standalone GitHub-ready directory."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import PROJECT_REL, edition_data_root, iso_now, raw_edition_root, wiki_edition_root, write_json  # noqa: E402


def copy_path(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def project_source_root(root: Path) -> Path:
    standalone = root / "scripts" / "worldcup_core.py"
    if standalone.exists():
        return root
    kb_project = root / PROJECT_REL
    if (kb_project / "scripts" / "worldcup_core.py").exists():
        return kb_project
    return SCRIPT_DIR.parents[0]


def _sanitize_export_text(text: str, *, root: Path, src_project: Path) -> str:
    sanitized = text
    replacements = []
    for base in [src_project, src_project.resolve(), root, root.resolve()]:
        val_str = str(base)
        if not val_str:
            continue
        # Add basic slashes/backslashes
        for sep in ["/", "\\"]:
            term = val_str + sep
            if (term, "") not in replacements:
                replacements.append((term, ""))
        # Add escaped backslashes for JSON
        val_escaped = val_str.replace("\\", "\\\\")
        for sep in ["/", "\\\\"]:
            term = val_escaped + sep
            if (term, "") not in replacements:
                replacements.append((term, ""))

    # Sort by length descending to match longer patterns first
    replacements.sort(key=lambda x: len(x[0]), reverse=True)
    for old, new in replacements:
        sanitized = sanitized.replace(old, new)
    return sanitized


def sanitize_exported_paths(*, root: Path, src_project: Path, output: Path) -> dict:
    scanned = 0
    changed = 0
    for path in output.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".txt"}:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8")
        sanitized = _sanitize_export_text(text, root=root, src_project=src_project)
        if sanitized != text:
            path.write_text(sanitized, encoding="utf-8")
            changed += 1
    return {"scanned_files": scanned, "changed_files": changed}


def export_standalone(*, root: Path, edition: str, output: Path, now: str | None = None) -> dict:
    generated_at = iso_now(now)
    src_project = project_source_root(root)
    output.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    missing: list[str] = []
    for rel in [
        "scripts",
        "schema",
        "skills",
        "tests",
        "examples",
        "assets",
        ".github",
        "knowledge-base/agent",
        "AGENT_README.md",
        "README.md",
        "TODO.md",
        "LICENSE",
        "install_as_skill.sh",
        "pyproject.toml",
        ".gitignore",
        ".env.example",
    ]:
        src = src_project / rel
        dst = output / rel
        if copy_path(src, dst):
            copied.append(rel)
        else:
            missing.append(rel)

    edition_data_src = edition_data_root(root, edition)
    raw_src = raw_edition_root(root, edition)
    wiki_src = wiki_edition_root(root, edition)
    for src, dst_rel in [
        (edition_data_src, Path("knowledge-base") / edition / "data"),
        (raw_src, Path("knowledge-base") / edition / "raw"),
        (wiki_src, Path("knowledge-base") / edition / "wiki"),
    ]:
        if copy_path(src, output / dst_rel):
            copied.append(dst_rel.as_posix())
        else:
            missing.append(dst_rel.as_posix())

    sanitization = sanitize_exported_paths(root=root, src_project=src_project, output=output)

    manifest = {
        "version": 1,
        "edition": edition,
        "generated_at": generated_at,
        "mode": "worldcup-standalone-export",
        "status": "export_written",
        "output": str(output),
        "copied": copied,
        "missing_optional": missing,
        "path_sanitization": sanitization,
        "agent_contracts": {
            "human_readme": "AGENT_README.md",
            "agent_card": "knowledge-base/agent/AGENT_CARD.json",
            "tool_catalog": "knowledge-base/agent/TOOL_CATALOG.json",
            "runbook": "knowledge-base/agent/RUNBOOK.md",
            "guardrails": "knowledge-base/agent/GUARDRAILS.md",
            "handoffs": "knowledge-base/agent/HANDOFFS.md",
            "trace_events": "knowledge-base/agent/TRACE_EVENTS.md",
        },
        "next_steps": [
            "cd exported directory",
            "read AGENT_README.md and knowledge-base/agent/TOOL_CATALOG.json",
            "python3 -m unittest tests/test_worldcup_predictor_system.py",
            "python3 scripts/worldcup_edition_init.py init --edition <edition>",
        ],
        "safety_invariants": [
            "standalone_export_keeps_runtime_code_schema_skill_tests",
            "standalone_export_keeps_selected_edition_raw_wiki_and_data",
            "standalone_export_keeps_agent_card_tool_catalog_guardrails_handoffs_and_trace_contracts",
            "standalone_export_removes_local_absolute_paths_from_text_artifacts",
        ],
    }
    write_json(output / "export-manifest.json", manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--now")
    parser.add_argument("--root", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = export_standalone(
        root=Path(args.root).resolve(),
        edition=args.edition,
        output=Path(args.output).resolve(),
        now=args.now,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
