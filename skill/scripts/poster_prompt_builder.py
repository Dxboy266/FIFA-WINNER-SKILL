#!/usr/bin/env python3
"""Build image-generation prompt manifests from daily prediction reports."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from worldcup_core import raw_edition_root, DISCLAIMER, edition_data_root, iso_now, load_json, load_match_ledger, poster_manifest_path, write_json, write_text  # noqa: E402


NEGATIVE_PROMPT_SHOWDOWN = (
    "fictional players, generic footballers, fake faces, missing squad members, only 5 players, only 10 players, "
    "duplicate players, wrong team colors, wrong jersey numbers, wrong flag, fake logo, unreadable Chinese text, "
    "misspelled text, incorrect date, incorrect kickoff time, distorted faces, deformed hands, extra limbs, "
    "plastic skin, cartoon, anime, video game render, fantasy armor, low quality"
)

TEAM_TRANSLATIONS = {
    "Algeria": "阿尔及利亚",
    "Argentina": "阿根廷",
    "Australia": "澳大利亚",
    "Austria": "奥地利",
    "Belgium": "比利时",
    "Bosnia and Herzegovina": "波黑",
    "Bosnia And Herzegovina": "波黑",
    "Brazil": "巴西",
    "Cabo Verde": "佛得角",
    "Canada": "加拿大",
    "Colombia": "哥伦比亚",
    "Congo DR": "民主刚果",
    "Côte D'Ivoire": "科特迪瓦",
    "Croatia": "克罗地亚",
    "Curaçao": "库拉索",
    "Czechia": "捷克",
    "Czech Republic": "捷克",
    "Ecuador": "厄瓜多尔",
    "Egypt": "埃及",
    "England": "英格兰",
    "France": "法国",
    "Germany": "德国",
    "Ghana": "加纳",
    "Haiti": "海地",
    "IR Iran": "伊朗",
    "Iran": "伊朗",
    "Iraq": "伊拉克",
    "Japan": "日本",
    "Jordan": "约旦",
    "Korea Republic": "韩国",
    "South Korea": "韩国",
    "Mexico": "墨西哥",
    "Morocco": "摩洛哥",
    "Netherlands": "荷兰",
    "New Zealand": "新西兰",
    "Nigeria": "尼日利亚",
    "Panama": "巴拿马",
    "Peru": "秘鲁",
    "Poland": "波兰",
    "Portugal": "葡萄牙",
    "Qatar": "卡塔尔",
    "Saudi Arabia": "沙特",
    "Senegal": "塞内加尔",
    "Serbia": "塞尔维亚",
    "South Africa": "南非",
    "Spain": "西班牙",
    "Sweden": "瑞典",
    "Switzerland": "瑞士",
    "Tunisia": "突尼斯",
    "Turkey": "土耳其",
    "Ukraine": "乌克兰",
    "Uruguay": "乌拉圭",
    "United States": "美国",
    "USA": "美国",
    "Venezuela": "委内瑞拉",
    "Wales": "威尔士"
}

TEAM_META = {
    "South Korea": {
        "name_zh": "韩国",
        "colors": "红色主色球衣",
        "glow_color": "红色",
        "stars": "Son Heungmin, Kim Minjae, Lee Kangin, Hwang Heechan",
        "adjective": "亚洲锋刃"
    },
    "Korea Republic": {
        "name_zh": "韩国",
        "colors": "红色主色球衣",
        "glow_color": "红色",
        "stars": "Son Heungmin, Kim Minjae, Lee Kangin, Hwang Heechan",
        "adjective": "亚洲锋刃"
    },
    "Czechia": {
        "name_zh": "捷克",
        "colors": "白红或红蓝主色球衣",
        "glow_color": "蓝白色",
        "stars": "Patrik Schick, Tomas Soucek, Pavel Sulc, Adam Hlozek",
        "adjective": "欧洲铁阵"
    },
    "Canada": {
        "name_zh": "加拿大",
        "colors": "红色或白色主色球衣",
        "glow_color": "红白色",
        "stars": "Alphonso Davies, Jonathan David, Stephen Eustaquio, Tajon Buchanan",
        "adjective": "北美枫刃"
    },
    "Bosnia and Herzegovina": {
        "name_zh": "波黑",
        "colors": "蓝色或白色主色球衣",
        "glow_color": "蓝黄色",
        "stars": "Edin Dzeko, Sead Kolasinac, Miralem Pjanic, Rade Krunic",
        "adjective": "巴尔干铁骑"
    },
    "Mexico": {
        "name_zh": "墨西哥",
        "colors": "绿色或白色主色球衣",
        "glow_color": "绿白色",
        "stars": "Santiago Gimenez, Edson Alvarez, Raul Jimenez, Guillermo Ochoa",
        "adjective": "高原雄鹰"
    },
    "Argentina": {
        "name_zh": "阿根廷",
        "colors": "蓝白相间条纹球衣",
        "glow_color": "蓝白色",
        "stars": "Lionel Messi, Lautaro Martinez, Enzo Fernandez, Rodrigo De Paul",
        "adjective": "潘帕斯雄鹰"
    },
    "Brazil": {
        "name_zh": "巴西",
        "colors": "经典黄色主色、绿色点缀球衣",
        "glow_color": "黄绿色",
        "stars": "Vinicius Junior, Neymar Jr, Rodrygo, Bruno Guimaraes",
        "adjective": "桑巴军团"
    },
    "Germany": {
        "name_zh": "德国",
        "colors": "白色主色球衣",
        "glow_color": "黑红金色",
        "stars": "Florian Wirtz, Jamal Musiala, Kai Havertz, Joshua Kimmich",
        "adjective": "德意志战车"
    },
    "France": {
        "name_zh": "法国",
        "colors": "深蓝色主色球衣",
        "glow_color": "蓝白红色",
        "stars": "Kylian Mbappe, Antoine Griezmann, William Saliba, Ousmane Dembele",
        "adjective": "高卢雄鸡"
    },
    "Spain": {
        "name_zh": "西班牙",
        "colors": "红色主色、黄色点缀球衣",
        "glow_color": "红黄色",
        "stars": "Lamine Yamal, Rodri, Pedri, Nico Williams",
        "adjective": "斗牛狂飙"
    },
    "England": {
        "name_zh": "英格兰",
        "colors": "白色主色球衣",
        "glow_color": "红白色",
        "stars": "Harry Kane, Jude Bellingham, Bukayo Saka, Phil Foden",
        "adjective": "三狮军旗"
    },
    "Italy": {
        "name_zh": "意大利",
        "colors": "蓝色主色球衣",
        "glow_color": "蔚蓝色",
        "stars": "Nicolo Barella, Federico Chiesa, Alessandro Bastoni, Gianluigi Donnarumma",
        "adjective": "钢防蓝翼"
    },
    "Portugal": {
        "name_zh": "葡萄牙",
        "colors": "红绿相间球衣",
        "glow_color": "红绿色",
        "stars": "Cristiano Ronaldo, Bruno Fernandes, Rafael Leao, Bernardo Silva",
        "adjective": "航海家军团"
    },
    "Netherlands": {
        "name_zh": "荷兰",
        "colors": "橙色主色球衣",
        "glow_color": "橙黑色",
        "stars": "Virgil van Dijk, Cody Gakpo, Frenkie de Jong, Xavi Simons",
        "adjective": "橙色风暴"
    },
    "Japan": {
        "name_zh": "日本",
        "colors": "深蓝色主色球衣",
        "glow_color": "蓝白色",
        "stars": "Kaoru Mitoma, Takefusa Kubo, Wataru Endo, Takumi Minamino",
        "adjective": "东瀛刀锋"
    },
    "USA": {
        "name_zh": "美国",
        "colors": "白色主色球衣",
        "glow_color": "红蓝双色",
        "stars": "Christian Pulisic, Weston McKennie, Folarin Balogun, Timothy Weah",
        "adjective": "星条战力"
    },
    "United States": {
        "name_zh": "美国",
        "colors": "白色主色球衣",
        "glow_color": "红蓝双色",
        "stars": "Christian Pulisic, Weston McKennie, Folarin Balogun, Timothy Weah",
        "adjective": "星条战力"
    }
}


def _team_name(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("team_id") or "Unknown Team")
    return str(value or "Unknown Team")


def _team_id(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("team_id") or "").lower()
    return ""


def _prediction_team(value: object, match_team: object) -> object:
    if isinstance(value, dict) and value.get("team_id"):
        return value
    if isinstance(match_team, dict) and match_team.get("team_id"):
        return match_team
    return value


def _manifest_path(root: Path, edition: str, date: str, match_id: str | None = None) -> Path:
    if not match_id:
        return poster_manifest_path(root, edition, date)
    safe_match = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in match_id)
    return edition_data_root(root, edition) / "reports" / "posters" / f"{date}-{safe_match}-poster-manifest.json"


def _prompt_text_path(root: Path, edition: str, date: str, match_id: str | None = None) -> Path:
    if not match_id:
        return edition_data_root(root, edition) / "reports" / "posters" / f"{date}-poster-prompts.txt"
    safe_match = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in match_id)
    return edition_data_root(root, edition) / "reports" / "posters" / f"{date}-{safe_match}-poster-prompt.txt"


def _format_prompt_text(poster_items: list[dict]) -> str:
    if not poster_items:
        return "未找到可生成海报的预测项。\n"
    blocks = []
    for item in poster_items:
        header = f"# {item.get('match_id', '')} {item.get('home_team', '')} vs {item.get('away_team', '')}".strip()
        prompt = str(item.get("prompt", "")).strip()
        negative_prompt = str(item.get("negative_prompt", "")).strip()
        block = f"{header}\n\n{prompt}"
        if negative_prompt:
            block += f"\n\n负面提示词：\n{negative_prompt}"
        blocks.append(block)
    return "\n\n---\n\n".join(blocks) + "\n"


def _load_rosters_by_team_id(root: Path, edition: str) -> dict[str, dict]:
    roster_path = raw_edition_root(root, edition) / "rosters" / "fifa-squad-lists.json"
    roster = load_json(roster_path, {})
    teams = roster.get("teams", []) if isinstance(roster, dict) else []
    result = {}
    for team in teams:
        if not isinstance(team, dict):
            continue
        team_id = str(team.get("team_id") or team.get("code") or "").lower()
        if team_id:
            result[team_id] = team
    return result


def _format_player(player: dict) -> str:
    shirt = player.get("shirt_number")
    position = str(player.get("position") or "").strip()
    name = str(player.get("player_name") or player.get("name_on_shirt") or "").strip()
    club = str(player.get("club") or "").strip()
    shirt_text = f"{shirt}号" if shirt not in (None, "") else ""
    main = " ".join(part for part in [shirt_text, position, name] if part)
    return f"{main} ({club})" if club else main


def _format_roster(team: dict | None) -> str:
    players = team.get("players", []) if isinstance(team, dict) else []
    if not players:
        return "官方阵容未找到，请生成前先补齐该队 roster。"
    return ", ".join(_format_player(player) for player in players if isinstance(player, dict))


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _kickoff_text(kickoff_at: str, timezone_name: str) -> str:
    kickoff = _parse_datetime(kickoff_at)
    if not kickoff:
        return "开赛时间待确认"
    local = kickoff.astimezone(ZoneInfo(timezone_name))
    return f"{local.month}月{local.day}日 {local:%H:%M} 开赛"


def prompt_for_prediction(item: dict) -> str:
    home = _team_name(item.get("home_team"))
    away = _team_name(item.get("away_team"))
    prediction = item["prediction"]
    score = prediction["score"]
    result = prediction.get("result") or prediction.get("predicted_outcome", "")
    return (
        "Create a polished Chinese editorial sports prediction poster. "
        f"Match: {home} vs {away}. "
        f"Prediction: {score['home']}-{score['away']}, result {result}, "
        f"total goals {prediction['total_goals']}, confidence {prediction['confidence']}. "
        "Use bold football matchday typography, clear score hierarchy, subtle pitch texture, "
        "national-team color accents, and a small visible disclaimer: 娱乐预测，非投注建议. "
        "Do not include betting odds, stake amounts, guaranteed-win wording, or sportsbook branding."
    )


def _get_team_meta(team_name: str, roster: dict | None) -> dict:
    meta = TEAM_META.get(team_name)
    if not meta:
        name_zh = TEAM_TRANSLATIONS.get(team_name, team_name)
        stars_list = []
        if roster and isinstance(roster, dict):
            players = roster.get("players", [])
            for p in players:
                if isinstance(p, dict):
                    pname = p.get("player_name") or p.get("name_on_shirt")
                    if pname:
                        stars_list.append(str(pname))
            stars_list = stars_list[:4]
        if not stars_list:
            stars_list = ["核心球员"]

        meta = {
            "name_zh": name_zh,
            "colors": "国家队经典主色球衣",
            "glow_color": "国家队代表光效",
            "stars": ", ".join(stars_list),
            "adjective": f"{name_zh}劲旅"
        }
    return meta


def prompt_for_showdown_prediction(
    *,
    item: dict,
    home_roster: dict | None,
    away_roster: dict | None,
    timezone_name: str,
) -> tuple[str, str]:
    home = _team_name(item.get("home_team"))
    away = _team_name(item.get("away_team"))

    home_meta = _get_team_meta(home, home_roster)
    away_meta = _get_team_meta(away, away_roster)

    home_zh = home_meta["name_zh"]
    away_zh = away_meta["name_zh"]

    prediction = item["prediction"]
    score = prediction["score"]
    kickoff = _kickoff_text(str(item.get("kickoff_at") or ""), timezone_name)

    home_players = _format_roster(home_roster)
    away_players = _format_roster(away_roster)

    divination = item.get("divination_overlay", {}) or {}
    hexagram = divination.get("hexagram", "乾")

    home_adj = home_meta["adjective"]
    away_adj = away_meta["adjective"]
    atmosphere = f"{home_adj}，硬碰{away_adj}"
    play_card = item.get("play_card", {}) if isinstance(item.get("play_card"), dict) else {}
    support_caption = str(play_card.get("poster_caption") or "").strip()
    if not support_caption:
        result = prediction.get("result") or prediction.get("predicted_outcome")
        score_text = f"{score['home']}-{score['away']}"
        if result == "home_win":
            support_caption = f"AI预测比分 {score_text}，{home_zh}主线占优，胜负趋势指向主队。"
        elif result == "away_win":
            support_caption = f"AI预测比分 {score_text}，{away_zh}主线占优，胜负趋势指向客队。"
        else:
            support_caption = f"AI预测比分 {score_text}，双方拉扯成局，平局剧本需要重点防范。"

    support = f"天纪卦象【{hexagram}】：{support_caption}"

    prompt = f"""为世界杯小组赛「{home_zh} vs {away_zh}」制作一张高燃赛前预测宣传海报。

海报必须准确出现这些中文文字：
主标题：{home_zh} VS {away_zh}
比赛时间：{kickoff}
氛围文案：{atmosphere}
副文案：AI 赛前预测｜胜负趋势分析
辅助文案：{support}
免责声明：娱乐预测，非投注建议

重要要求：
1. 海报中必须完整出现 {home_zh} 和 {away_zh} 两支球队的所有 26 人阵容（总计 52 人），禁止只渲染少数几名核心球员，绝不可遗漏后排球员。采用震撼的大合影战争海报对称构图：核心球星位于前排显眼位置，其余队员依次在中后排呈密集、威武的阵列排开。
2. ⚠️【绝对禁令：严禁将球员名字直接以文字形式写在海报画面中】。除顶部标题与指定文案外，画面各处（如球员头部上方、身旁、下方等）绝对不要出现任何球员的名字、英文缩写或拼音文字标签！所有球员名字仅作为其真实长相生成特征（面部、发型、肤色、身材、球衣号码）的提示基础，而非用于排版文字。

{home_zh} 完整 26 人球员名单：
{home_players}

{away_zh} 完整 26 人球员名单：
{away_players}

构图：
横版 16:9。画面整体呈严整的战争大合影列阵：左半侧为 {home_zh} 完整阵容，穿 {home_zh} 国家队 {home_meta['colors']}；右半侧为 {away_zh} 完整阵容，穿 {away_zh} 国家队 {away_meta['colors']}。{home_zh} 一侧前排突出渲染：{home_meta['stars']}；{away_zh} 一侧前排突出渲染：{away_meta['stars']}。双方球员均根据公开赛事实时照片的真实面容生成，眼神中透露出大战将至的紧迫与杀气，前排前倾，后排紧密矗立。画面中央草坪放置足球，四周伴有强烈的光效对冲、草屑飞扬、战火硝烟与极具张力的氛围。

视觉氛围：
夜晚的现代化世界杯体育场内部，高空探照灯形成极具戏剧张力的顶光和侧逆光，看台座无虚席。{home_meta['glow_color']} 与 {away_meta['glow_color']} 两股标志性的国家队代表色光效在画面中央激烈交撞。整体呈电影级体育海报质感，追求极高的超写实摄影细节、高对比度、高光影清晰度，摒弃任何卡通、低像素或塑料质感的 3D 渲染，呈现高燃的小组赛决战气氛。

文字设计：
顶部显著位置排版大标题「{home_zh} VS {away_zh}」
大标题正下方放置「{kickoff}」
画面中部或底部点缀「{atmosphere}」
底部放置副文案「AI 赛前预测｜胜负趋势分析」
所有文字版式均需与专业顶级赛事宣传视觉契合，位置合理、层次分明，不要乱码，不要出现任何多余或错误的文本图层。

风格关键词：
ultra realistic sports poster, cinematic football photography, official tournament atmosphere, dramatic stadium lighting, realistic faces, full squad lineup, intense face-off, high detail, professional commercial poster, epic matchday poster

避免：
不要生成虚构球员，不要只出现几个人，不要遗漏完整阵容，不要重复同一个球员，不要错误球衣颜色，不要错误国旗，不要乱码文字，不要卡通风，不要游戏渲染风，不要塑料皮肤，不要脸部畸形，不要多手多脚，不要把 {home_zh} 球员放到 {away_zh} 阵容里，不要把 {away_zh} 球员放到 {home_zh} 阵容里。"""

    return prompt, NEGATIVE_PROMPT_SHOWDOWN


def build_poster_manifest(
    *,
    root: Path,
    edition: str,
    date: str,
    report_path: Path | None = None,
    match_id: str | None = None,
    now: str | None = None,
    style: str = "prediction",
    timezone_name: str = "Asia/Shanghai",
) -> dict:
    generated_at = iso_now(now)
    prompt_text_path = _prompt_text_path(root, edition, date, match_id)
    report_path = report_path or (edition_data_root(root, edition) / "reports" / "daily-predictions" / f"{date}.json")
    if not report_path.exists():
        fallback_path = edition_data_root(root, edition) / "reports" / "backtests" / report_path.name
        if fallback_path.exists():
            report_path = fallback_path
    report = load_json(report_path, {})
    rosters_by_team_id = _load_rosters_by_team_id(root, edition) if style == "showdown" else {}
    ledger = load_match_ledger(root, edition) if style == "showdown" else {"matches": []}
    matches_by_id = {match.get("match_id"): match for match in ledger.get("matches", []) if isinstance(match, dict)}
    poster_items = []
    for prediction in report.get("predictions", []):
        if match_id and prediction.get("match_id") != match_id:
            continue
        match = matches_by_id.get(prediction.get("match_id"), {})
        home_team = _prediction_team(prediction.get("home_team"), match.get("home_team") if isinstance(match, dict) else {})
        away_team = _prediction_team(prediction.get("away_team"), match.get("away_team") if isinstance(match, dict) else {})
        home = _team_name(home_team)
        away = _team_name(away_team)
        score = prediction["prediction"]["score"]
        home_team_id = _team_id(home_team)
        away_team_id = _team_id(away_team)
        prompt = prompt_for_prediction(prediction)
        negative_prompt = ""
        kickoff_text = ""
        roster_counts = {}
        home_meta = _get_team_meta(home, rosters_by_team_id.get(home_team_id) if style == "showdown" else None)
        away_meta = _get_team_meta(away, rosters_by_team_id.get(away_team_id) if style == "showdown" else None)
        home_zh = home_meta["name_zh"]
        away_zh = away_meta["name_zh"]

        if style == "showdown":
            home_roster = rosters_by_team_id.get(home_team_id)
            away_roster = rosters_by_team_id.get(away_team_id)
            prompt, negative_prompt = prompt_for_showdown_prediction(
                item=prediction,
                home_roster=home_roster,
                away_roster=away_roster,
                timezone_name=timezone_name,
            )
            kickoff_text = _kickoff_text(str(prediction.get("kickoff_at") or ""), timezone_name)
            roster_counts = {
                home_team_id or home: len(home_roster.get("players", [])) if isinstance(home_roster, dict) else 0,
                away_team_id or away: len(away_roster.get("players", [])) if isinstance(away_roster, dict) else 0,
            }
        poster_items.append(
            {
                "poster_id": f"{prediction['match_id']}:prediction-poster",
                "match_id": prediction["match_id"],
                "style": style,
                "home_team": home,
                "away_team": away,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "prediction": prediction["prediction"],
                "kickoff_at": prediction.get("kickoff_at", ""),
                "kickoff_text": kickoff_text,
                "roster_counts": roster_counts,
                "disclaimer": DISCLAIMER,
                "prompt": prompt,
                "prompt_text_path": str(prompt_text_path),
                "negative_prompt": negative_prompt,
                "required_text": [
                    home,
                    away,
                    home_zh,
                    away_zh,
                    f"{score['home']}-{score['away']}",
                    "娱乐预测，非投注建议",
                ]
                + ([kickoff_text, "AI 赛前预测｜胜负趋势分析"] if kickoff_text else []),
                "forbidden_text": ["投注金额", "稳赢", "稳胆", "下注", "odds", "sportsbook"],
            }
        )
    manifest = {
        "version": 1,
        "edition": edition,
        "date": date,
        "generated_at": generated_at,
        "mode": "worldcup-prediction-poster-prompt-manifest",
        "source_report": str(report_path),
        "manifest_path": str(_manifest_path(root, edition, date, match_id)),
        "prompt_text_path": str(prompt_text_path),
        "match_id_filter": match_id or "",
        "style": style,
        "timezone": timezone_name,
        "poster_items": poster_items,
        "summary": {"poster_items": len(poster_items)},
        "safety_invariants": [
            "poster_prompt_derived_from_structured_prediction_report",
            "poster_disclaimer_required",
            "poster_must_not_include_betting_language",
            "user_facing_image2_prompt_is_plain_text",
        ],
    }
    write_text(prompt_text_path, _format_prompt_text(poster_items))
    write_json(Path(manifest["manifest_path"]), manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--edition", required=True)
    build.add_argument("--date", required=True)
    build.add_argument("--report-path")
    build.add_argument("--match-id")
    build.add_argument("--now")
    build.add_argument("--style", choices=["prediction", "showdown"], default="prediction")
    build.add_argument("--timezone", dest="timezone_name", default="Asia/Shanghai")
    build.add_argument("--root", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    args = build_parser().parse_args(argv)
    result = build_poster_manifest(
        root=Path(args.root).resolve(),
        edition=args.edition,
        date=args.date,
        report_path=Path(args.report_path).resolve() if args.report_path else None,
        match_id=args.match_id,
        now=args.now,
        style=args.style,
        timezone_name=args.timezone_name,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
