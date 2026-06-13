#!/usr/bin/env python3
"""
伤停数据采集脚本 - API-Football 版本
从 API-Football 获取实时伤停和停赛数据
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: requests module not installed. Run: pip install requests")
    sys.exit(1)


# FIFA 国家队代码到 API-Football Team ID 的映射
TEAM_ID_MAP = {
    # 2026 世界杯参赛队伍（示例，需要根据实际情况补充）
    "BRA": 9825,   # Brazil
    "ARG": 9769,   # Argentina
    "FRA": 9772,   # France
    "GER": 9771,   # Germany
    "ESP": 9763,   # Spain
    "ENG": 1208,   # England
    "POR": 9803,   # Portugal
    "BEL": 9778,   # Belgium
    "NED": 9801,   # Netherlands
    "URU": 9848,   # Uruguay
    "MEX": 9800,   # Mexico
    "USA": 9829,   # USA
    "CAN": 9837,   # Canada
    "RSA": 9807,   # South Africa
    "KOR": 9798,   # South Korea
    "JPN": 9797,   # Japan
    "CZE": 9781,   # Czechia
    # 更多国家队需要添加...
}


class InjuryFetcher:
    """伤停数据获取器"""

    def __init__(self, api_key=None, root_path="."):
        """
        初始化

        Args:
            api_key: API-Football API Key (从环境变量或参数获取)
            root_path: 项目根路径
        """
        self.api_key = api_key or os.getenv("API_FOOTBALL_KEY")
        if not self.api_key:
            raise ValueError(
                "API_FOOTBALL_KEY not found. "
                "Set environment variable or pass as argument.\n"
                "Get your key from: https://www.api-football.com/"
            )

        self.root_path = Path(root_path)
        self.base_url = "https://v3.football.api-sports.io"
        self.headers = {
            "x-rapidapi-key": self.api_key,
            "x-rapidapi-host": "v3.football.api-sports.io"
        }

    def fetch_team_injuries(self, team_code, season=2026):
        """
        获取单个球队的伤停数据

        Args:
            team_code: FIFA 国家队代码 (e.g., "BRA", "ARG")
            season: 赛季年份

        Returns:
            dict: 伤停数据
        """
        team_id = TEAM_ID_MAP.get(team_code)
        if not team_id:
            print(f"Warning: Team code '{team_code}' not found in TEAM_ID_MAP")
            return None

        url = f"{self.base_url}/injuries"
        params = {
            "team": team_id,
            "season": season
        }

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get("errors"):
                print(f"API Error for {team_code}: {data['errors']}")
                return None

            return self._parse_injuries(data, team_code)

        except requests.exceptions.RequestException as e:
            print(f"Network error fetching injuries for {team_code}: {e}")
            return None

    def _parse_injuries(self, raw_data, team_code):
        """
        解析 API 响应数据

        Args:
            raw_data: API 原始响应
            team_code: 国家队代码

        Returns:
            dict: 标准化的伤停数据
        """
        injuries = []
        suspensions = []

        for item in raw_data.get("response", []):
            player = item.get("player", {})
            player_name = player.get("name", "Unknown")
            player_id = player.get("id")

            injury_type = item.get("player", {}).get("type", "")
            reason = item.get("player", {}).get("reason", "")

            injury_data = {
                "player_id": player_id,
                "player_name": player_name,
                "type": injury_type,
                "reason": reason,
                "start_date": None,
                "end_date": None,
                "status": "out",  # out | doubtful | expected_return
                "severity": self._assess_severity(injury_type, reason),
                "source": "api-football",
                "updated_at": datetime.now(timezone.utc).isoformat() + "Z"
            }

            # 区分伤停和停赛
            if "suspension" in reason.lower() or "banned" in reason.lower():
                suspensions.append({
                    "player_id": player_id,
                    "player_name": player_name,
                    "reason": reason,
                    "matches_remaining": None,  # API 不提供，需要手动标注
                    "source": "api-football"
                })
            else:
                injuries.append(injury_data)

        return {
            "team_code": team_code,
            "team_name": self._get_team_name(team_code),
            "injuries": injuries,
            "suspensions": suspensions,
            "total_count": len(injuries) + len(suspensions),
            "fetched_at": datetime.now(timezone.utc).isoformat() + "Z"
        }

    def _assess_severity(self, injury_type, reason):
        """
        评估伤病严重程度

        Args:
            injury_type: 伤病类型
            reason: 伤病原因

        Returns:
            str: high | medium | low
        """
        high_keywords = ["fracture", "rupture", "torn", "surgery", "acl", "mcl"]
        medium_keywords = ["strain", "sprain", "contusion", "knock"]

        text = (injury_type + " " + reason).lower()

        for keyword in high_keywords:
            if keyword in text:
                return "high"

        for keyword in medium_keywords:
            if keyword in text:
                return "medium"

        return "low"

    def _get_team_name(self, team_code):
        """获取国家队全名"""
        team_names = {
            "BRA": "Brazil", "ARG": "Argentina", "FRA": "France",
            "GER": "Germany", "ESP": "Spain", "ENG": "England",
            "POR": "Portugal", "BEL": "Belgium", "NED": "Netherlands",
            "URU": "Uruguay", "MEX": "Mexico", "USA": "United States",
            "CAN": "Canada", "RSA": "South Africa", "KOR": "South Korea",
            "JPN": "Japan", "CZE": "Czechia"
        }
        return team_names.get(team_code, team_code)

    def fetch_all_injuries(self, edition, date, team_codes=None):
        """
        批量获取多个球队的伤停数据

        Args:
            edition: 届次 (e.g., "2026")
            date: 日期 (e.g., "2026-06-11")
            team_codes: 国家队代码列表，None 则获取所有

        Returns:
            dict: 所有球队的伤停数据
        """
        if team_codes is None:
            team_codes = list(TEAM_ID_MAP.keys())

        all_injuries = {
            "edition": edition,
            "date": date,
            "teams": {},
            "summary": {
                "total_teams": len(team_codes),
                "teams_with_injuries": 0,
                "total_injuries": 0,
                "total_suspensions": 0
            }
        }

        print(f"Fetching injuries for {len(team_codes)} teams...")

        for i, team_code in enumerate(team_codes, 1):
            print(f"  [{i}/{len(team_codes)}] Fetching {team_code}...", end=" ")

            team_data = self.fetch_team_injuries(team_code)

            if team_data:
                all_injuries["teams"][team_code] = team_data

                if team_data["total_count"] > 0:
                    all_injuries["summary"]["teams_with_injuries"] += 1
                    all_injuries["summary"]["total_injuries"] += len(team_data["injuries"])
                    all_injuries["summary"]["total_suspensions"] += len(team_data["suspensions"])

                print(f"✓ ({team_data['total_count']} issues)")
            else:
                print("✗ Failed")

        return all_injuries

    def save_to_daily_evidence(self, edition, date, injuries_data):
        """
        保存到每日证据文件

        Args:
            edition: 届次
            date: 日期
            injuries_data: 伤停数据
        """
        evidence_dir = self.root_path / "knowledge-base" / edition / "data" / "daily-evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        evidence_file = evidence_dir / f"{date}.json"

        # 读取现有文件（如果存在）
        if evidence_file.exists():
            with open(evidence_file, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
        else:
            existing_data = {
                "date": date,
                "edition": edition,
                "data_sources": []
            }

        # 添加伤停数据
        existing_data["injuries"] = injuries_data
        existing_data["data_sources"].append({
            "type": "injuries",
            "source": "api-football",
            "fetched_at": datetime.now(timezone.utc).isoformat() + "Z"
        })

        # 保存
        with open(evidence_file, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f, indent=2, ensure_ascii=False)

        print(f"\n✓ Saved to: {evidence_file}")
        print(f"  - Teams with injuries: {injuries_data['summary']['teams_with_injuries']}")
        print(f"  - Total injuries: {injuries_data['summary']['total_injuries']}")
        print(f"  - Total suspensions: {injuries_data['summary']['total_suspensions']}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch injury and suspension data from API-Football"
    )
    parser.add_argument(
        "--edition",
        required=True,
        help="World Cup edition (e.g., 2026)"
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Date in YYYY-MM-DD format"
    )
    parser.add_argument(
        "--teams",
        help="Comma-separated team codes (e.g., BRA,ARG,FRA). If not provided, fetch all teams."
    )
    parser.add_argument(
        "--api-key",
        help="API-Football API Key (or set API_FOOTBALL_KEY env variable)"
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root path"
    )

    args = parser.parse_args()

    # 解析球队列表
    team_codes = None
    if args.teams:
        team_codes = [t.strip().upper() for t in args.teams.split(",")]

    # 创建 fetcher
    try:
        fetcher = InjuryFetcher(api_key=args.api_key, root_path=args.root)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # 获取伤停数据
    injuries_data = fetcher.fetch_all_injuries(args.edition, args.date, team_codes)

    # 保存到每日证据文件
    fetcher.save_to_daily_evidence(args.edition, args.date, injuries_data)


if __name__ == "__main__":
    main()
