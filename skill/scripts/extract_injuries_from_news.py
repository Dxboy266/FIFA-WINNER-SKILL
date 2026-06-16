#!/usr/bin/env python3
"""
从新闻中提取伤停信息（NLP 方法）
完全免费，不需要任何 API
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone


class InjuryExtractor:
    """从新闻文本中提取伤停信息"""

    # 伤停关键词模式（多语言）
    INJURY_PATTERNS = [
        # 英语
        r"(\w+\s+\w+)\s+(?:is|has been|will be)\s+(?:injured|ruled out|sidelined)",
        r"(\w+\s+\w+)\s+(?:out|doubtful|questionable)\s+(?:for|with|due to)",
        r"(\w+\s+\w+)\s+(?:suffers?|sustained?)\s+(?:an?)?\s*(?:injury|knock)",
        r"injury\s+(?:to|concern for)\s+(\w+\s+\w+)",
        r"(\w+\s+\w+)\s+will miss",

        # 西班牙语
        r"(\w+\s+\w+)\s+(?:lesionado|fuera|baja)",
        r"lesión de\s+(\w+\s+\w+)",

        # 葡萄牙语
        r"(\w+\s+\w+)\s+(?:lesionado|machucado|fora)",
        r"lesão de\s+(\w+\s+\w+)",
    ]

    # 停赛关键词模式
    SUSPENSION_PATTERNS = [
        r"(\w+\s+\w+)\s+(?:suspended|banned|red card)",
        r"(\w+\s+\w+)\s+will serve\s+(?:a\s+)?(?:suspension|ban)",
        r"(\w+\s+\w+)\s+(?:suspendido|sancionado)",
    ]

    # 严重程度关键词
    SEVERITY_HIGH = ["fracture", "torn", "rupture", "surgery", "acl", "mcl", "season"]
    SEVERITY_MEDIUM = ["strain", "sprain", "knock", "bruise"]

    # 状态关键词
    STATUS_OUT = ["ruled out", "out", "will miss", "sidelined", "unavailable"]
    STATUS_DOUBTFUL = ["doubtful", "questionable", "fitness test", "race against time"]

    def __init__(self, root_path="."):
        self.root_path = Path(root_path)

    def extract_from_news(self, edition, date):
        """
        从每日新闻中提取伤停信息

        Args:
            edition: 届次
            date: 日期

        Returns:
            dict: 提取的伤停信息
        """
        evidence_file = self.root_path / "knowledge-base" / edition / "data" / "daily-evidence" / f"{date}.json"

        if not evidence_file.exists():
            print(f"ERROR: Evidence file not found: {evidence_file}")
            return None

        # 读取每日证据
        with open(evidence_file, 'r', encoding='utf-8') as f:
            evidence = json.load(f)

        news_list = self._news_items(evidence)

        if not news_list:
            print("ERROR: No news found in evidence file")
            return None

        print(f"Analyzing {len(news_list)} news articles...")

        # 提取伤停信息
        extracted_injuries = {}

        for news_item in news_list:
            title = news_item.get("title") or news_item.get("headline") or ""
            summary = news_item.get("summary") or news_item.get("detail") or ""
            text = f"{title} {summary}"

            # 提取伤病
            injuries = self._extract_injuries(text)
            # 提取停赛
            suspensions = self._extract_suspensions(text)

            # 按球队分组（简单启发式：文本中出现的国家名）
            team = self._guess_team_from_text(text)

            if team:
                if team not in extracted_injuries:
                    extracted_injuries[team] = {
                        "team_code": team,
                        "team_name": self._get_team_name(team),
                        "injuries": [],
                        "suspensions": []
                    }

                for player_name, injury_type, status, severity in injuries:
                    extracted_injuries[team]["injuries"].append({
                        "player_name": player_name,
                        "type": injury_type,
                        "status": status,
                        "severity": severity,
                        "source": "news_extraction",
                        "source_url": news_item.get("url", ""),
                        "extracted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "confidence": "low"  # NLP 提取的置信度较低
                    })

                for player_name in suspensions:
                    extracted_injuries[team]["suspensions"].append({
                        "player_name": player_name,
                        "reason": "Suspension (extracted from news)",
                        "source": "news_extraction",
                        "source_url": news_item.get("url", "")
                    })

        return {
            "edition": edition,
            "date": date,
            "teams": extracted_injuries,
            "summary": {
                "total_teams": len(extracted_injuries),
                "total_injuries": sum(len(t["injuries"]) for t in extracted_injuries.values()),
                "total_suspensions": sum(len(t["suspensions"]) for t in extracted_injuries.values())
            },
            "extraction_method": "nlp_from_news",
            "confidence": "low"
        }

    def _news_items(self, evidence):
        """Return both legacy `news` and current `late_news` items."""
        items = []
        for key in ("late_news", "news"):
            value = evidence.get(key, [])
            if isinstance(value, list):
                items.extend(item for item in value if isinstance(item, dict))
        return items

    def _extract_injuries(self, text):
        """提取伤病信息"""
        injuries = []
        seen = set()

        for pattern in self.INJURY_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                player_name = match if isinstance(match, str) else match[0]
                player_name = self._clean_player_name(player_name)
                if not self._looks_like_player_name(player_name):
                    continue

                # 评估严重程度
                severity = self._assess_severity(text)
                # 评估状态
                status = self._assess_status(text)
                # 伤病类型
                injury_type = self._extract_injury_type(text)

                key = (player_name.casefold(), injury_type, status, severity)
                if key in seen:
                    continue
                seen.add(key)
                injuries.append((player_name, injury_type, status, severity))

        return injuries

    def _extract_suspensions(self, text):
        """提取停赛信息"""
        suspensions = []
        seen = set()

        for pattern in self.SUSPENSION_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                player_name = match if isinstance(match, str) else match[0]
                player_name = self._clean_player_name(player_name)
                if not self._looks_like_player_name(player_name):
                    continue
                key = player_name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                suspensions.append(player_name)

        return suspensions

    def _clean_player_name(self, name):
        """清理球员名字"""
        # 移除多余的空格
        name = re.sub(r'\s+', ' ', name).strip()
        # 首字母大写
        name = name.title()
        return name

    def _looks_like_player_name(self, name):
        """Filter obvious regex over-matches such as `Is Ruled`."""
        if not name:
            return False
        blocked_tokens = {
            "is",
            "has",
            "had",
            "been",
            "will",
            "ruled",
            "out",
            "with",
            "due",
            "for",
        }
        tokens = [token.casefold() for token in name.split()]
        return len(tokens) >= 2 and not any(token in blocked_tokens for token in tokens)

    def _assess_severity(self, text):
        """评估伤病严重程度"""
        text_lower = text.lower()

        for keyword in self.SEVERITY_HIGH:
            if keyword in text_lower:
                return "high"

        for keyword in self.SEVERITY_MEDIUM:
            if keyword in text_lower:
                return "medium"

        return "low"

    def _assess_status(self, text):
        """评估球员状态"""
        text_lower = text.lower()

        for keyword in self.STATUS_OUT:
            if keyword in text_lower:
                return "out"

        for keyword in self.STATUS_DOUBTFUL:
            if keyword in text_lower:
                return "doubtful"

        return "unknown"

    def _extract_injury_type(self, text):
        """提取伤病类型"""
        injury_types = {
            "ankle": ["ankle"],
            "knee": ["knee", "acl", "mcl"],
            "hamstring": ["hamstring", "thigh"],
            "muscle": ["muscle", "strain"],
            "head": ["head", "concussion"],
        }

        text_lower = text.lower()

        for injury_type, keywords in injury_types.items():
            for keyword in keywords:
                if keyword in text_lower:
                    return injury_type

        return "unknown"

    def _guess_team_from_text(self, text):
        """从文本中猜测球队（简单启发式）"""
        team_keywords = {
            "BRA": ["Brazil", "Brazilian", "Seleção"],
            "ARG": ["Argentina", "Argentine"],
            "FRA": ["France", "French"],
            "GER": ["Germany", "German"],
            "ESP": ["Spain", "Spanish"],
            "ENG": ["England", "English"],
            "POR": ["Portugal", "Portuguese"],
            "MEX": ["Mexico", "Mexican"],
            "USA": ["USA", "United States", "American"],
            # 更多国家...
        }

        for team_code, keywords in team_keywords.items():
            for keyword in keywords:
                if keyword in text:
                    return team_code

        return None

    def _get_team_name(self, team_code):
        """获取球队全名"""
        team_names = {
            "BRA": "Brazil", "ARG": "Argentina", "FRA": "France",
            "GER": "Germany", "ESP": "Spain", "ENG": "England",
            "POR": "Portugal", "MEX": "Mexico", "USA": "United States"
        }
        return team_names.get(team_code, team_code)

    def save_to_daily_evidence(self, edition, date, extracted_injuries):
        """保存到每日证据文件并写入 SQLite"""
        evidence_file = self.root_path / "wiki" / "public" / edition / "daily-evidence" / f"{date}.json"

        # 读取现有文件
        with open(evidence_file, 'r', encoding='utf-8') as f:
            evidence = json.load(f)

        # 添加提取的伤停数据
        evidence["injuries_extracted"] = extracted_injuries

        # 保存 JSON
        with open(evidence_file, 'w', encoding='utf-8') as f:
            json.dump(evidence, f, indent=2, ensure_ascii=False)

        # 写入 SQLite
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from worldcup_db import get_db_connection, init_database, save_injury
            db_path = self.root_path / "wiki" / "cache" / edition / f"worldcup_{edition}.db"
            init_database(db_path)
            conn = get_db_connection(db_path)
            try:
                with conn:
                    for team_code, team_data in extracted_injuries.get("teams", {}).items():
                        for inj in team_data.get("injuries", []):
                            injury_id = f"nlp-{team_code}-{inj.get('player_name', 'unknown')}-{date}".lower().replace(" ", "-")
                            save_injury(conn, {
                                "injury_id": injury_id,
                                "team_id": team_code.lower(),
                                "player_name": inj.get("player_name"),
                                "player_id": None,
                                "injury_type": inj.get("type", "injury"),
                                "reason": inj.get("reason"),
                                "severity": inj.get("severity"),
                                "status": inj.get("status", "active"),
                                "start_date": None,
                                "expected_end_date": None,
                                "source": inj.get("source", "nlp-extraction"),
                                "source_url": inj.get("source_url"),
                                "confidence": "low",
                                "recorded_at": inj.get("extracted_at") or datetime.now(timezone.utc).isoformat(),
                            })
                        for sus in team_data.get("suspensions", []):
                            injury_id = f"nlp-{team_code}-{sus.get('player_name', 'unknown')}-sus-{date}".lower().replace(" ", "-")
                            save_injury(conn, {
                                "injury_id": injury_id,
                                "team_id": team_code.lower(),
                                "player_name": sus.get("player_name"),
                                "player_id": None,
                                "injury_type": "suspension",
                                "reason": sus.get("reason"),
                                "severity": "moderate",
                                "status": "active",
                                "start_date": date,
                                "expected_end_date": None,
                                "source": sus.get("source", "nlp-extraction"),
                                "source_url": sus.get("source_url"),
                                "confidence": "low",
                                "recorded_at": datetime.now(timezone.utc).isoformat(),
                            })
            finally:
                conn.close()
        except Exception as e:
            print(f"Warning: SQLite NLP injury write failed: {e}", file=sys.stderr)

        print(f"\nOK: Saved extracted injuries to: {evidence_file}")
        print(f"  - Teams with injuries: {extracted_injuries['summary']['total_teams']}")
        print(f"  - Total injuries: {extracted_injuries['summary']['total_injuries']}")
        print(f"  - Total suspensions: {extracted_injuries['summary']['total_suspensions']}")
        print("  - Confidence: LOW (NLP extraction, needs manual verification)")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract injury information from news using NLP (Free Method)"
    )
    parser.add_argument("--edition", required=True, help="World Cup edition")
    parser.add_argument("--date", required=True, help="Date in YYYY-MM-DD format")
    parser.add_argument("--root", default=".", help="Project root path")

    args = parser.parse_args()

    extractor = InjuryExtractor(root_path=args.root)

    # 提取伤停信息
    extracted = extractor.extract_from_news(args.edition, args.date)

    if extracted:
        # 保存到证据文件
        extractor.save_to_daily_evidence(args.edition, args.date, extracted)
    else:
        print("ERROR: Extraction failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
