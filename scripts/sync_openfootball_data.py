#!/usr/bin/env python3
"""
从 OpenFootball 同步世界杯赛程数据
完全免费，无需 API Key
"""

import json
import sys
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("Error: requests not installed. Run: pip install requests")
    sys.exit(1)


class OpenFootballSync:
    """OpenFootball 数据同步器"""

    BASE_URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master"

    def __init__(self, root_path="."):
        self.root_path = Path(root_path)

    def fetch_worldcup_data(self, edition):
        """
        获取世界杯数据

        Args:
            edition: 年份 (e.g., "2026")

        Returns:
            dict: 世界杯数据
        """
        url = f"{self.BASE_URL}/{edition}/worldcup.json"

        try:
            print(f"Fetching World Cup {edition} data from OpenFootball...")
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            print(f"[OK] Fetched {len(data.get('matches', []))} matches")

            return data

        except requests.exceptions.RequestException as e:
            print(f"[FAIL] Failed to fetch data: {e}")
            return None

    def save_to_match_ledger(self, edition, worldcup_data):
        """
        保存到 match-ledger.json

        Args:
            edition: 年份
            worldcup_data: OpenFootball 数据
        """
        ledger_path = self.root_path / "knowledge-base" / edition / "data" / "match-ledger.json"

        # 读取现有 ledger
        if ledger_path.exists():
            with open(ledger_path, 'r', encoding='utf-8') as f:
                ledger = json.load(f)
        else:
            ledger = {
                "edition": edition,
                "matches": []
            }

        # 转换 OpenFootball 格式到内部格式
        for match in worldcup_data.get("matches", []):
            match_id = self._generate_match_id(edition, match)

            # 检查是否已存在
            existing_match = next(
                (m for m in ledger["matches"] if m.get("match_id") == match_id),
                None
            )

            if existing_match:
                # 更新比分（如果有）
                if "score" in match:
                    existing_match["home_score"] = match["score"]["ft"][0]
                    existing_match["away_score"] = match["score"]["ft"][1]
                    existing_match["status"] = "completed"
            else:
                # 添加新比赛
                ledger["matches"].append({
                    "match_id": match_id,
                    "date": match["date"],
                    "time": match.get("time", ""),
                    "home_team": match["team1"],
                    "away_team": match["team2"],
                    "group": match.get("group", ""),
                    "round": match.get("round", ""),
                    "venue": match.get("ground", ""),
                    "home_score": match.get("score", {}).get("ft", [None, None])[0] if "score" in match else None,
                    "away_score": match.get("score", {}).get("ft", [None, None])[1] if "score" in match else None,
                    "status": "completed" if "score" in match else "scheduled",
                    "source": "openfootball"
                })

        # 保存
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger_path, 'w', encoding='utf-8') as f:
            json.dump(ledger, f, indent=2, ensure_ascii=False)

        print(f"[OK] Saved to {ledger_path}")
        print(f"  Total matches: {len(ledger['matches'])}")

    def _generate_match_id(self, edition, match):
        """生成比赛 ID"""
        date = match["date"].replace("-", "")
        team1 = match["team1"].replace(" ", "-")[:3].upper()
        team2 = match["team2"].replace(" ", "-")[:3].upper()
        return f"{edition}-{date}-{team1}-vs-{team2}"


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Sync World Cup data from OpenFootball (Free & Open Source)"
    )
    parser.add_argument(
        "--edition",
        required=True,
        help="World Cup edition (e.g., 2026)"
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root path"
    )

    args = parser.parse_args()

    syncer = OpenFootballSync(root_path=args.root)

    # 获取数据
    worldcup_data = syncer.fetch_worldcup_data(args.edition)

    if worldcup_data:
        # 保存到 match-ledger
        syncer.save_to_match_ledger(args.edition, worldcup_data)
        print("\n[OK] Sync completed successfully!")
    else:
        print("\n[FAIL] Sync failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
