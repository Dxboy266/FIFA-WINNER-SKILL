# 消息面数据采集系统使用指南

## 快速开始

### 第一步：注册 API-Football

1. 访问 https://www.api-football.com/
2. 注册账号
3. 选择 **Basic Plan** ($10/month, 3000 requests/day)
4. 获取 API Key

### 第二步：配置 API Key

#### 方法1：环境变量（推荐）
```bash
# Windows PowerShell
$env:API_FOOTBALL_KEY = "your-api-key-here"

# 或者添加到 .env 文件
echo "API_FOOTBALL_KEY=your-api-key-here" >> .env
```

#### 方法2：命令行参数
```bash
python scripts/fetch_injuries_api_football.py --api-key your-api-key-here ...
```

### 第三步：运行伤停数据采集

```bash
# 获取所有球队的伤停数据
python scripts/fetch_injuries_api_football.py --edition 2026 --date 2026-06-11 --root .

# 只获取特定球队
python scripts/fetch_injuries_api_football.py --edition 2026 --date 2026-06-11 --teams "BRA,ARG,FRA" --root .
```

---

## 数据采集脚本说明

### 1. 伤停数据采集 (fetch_injuries_api_football.py)

**功能**：
- 从 API-Football 获取实时伤停和停赛数据
- 自动评估伤病严重程度（high/medium/low）
- 区分伤病和停赛
- 保存到每日证据文件

**数据来源**: API-Football `/injuries` endpoint

**输出路径**: `knowledge-base/{edition}/data/daily-evidence/{date}.json`

**输出格式**:
```json
{
  "date": "2026-06-11",
  "edition": "2026",
  "injuries": {
    "teams": {
      "BRA": {
        "team_code": "BRA",
        "team_name": "Brazil",
        "injuries": [
          {
            "player_id": 12345,
            "player_name": "Neymar Jr",
            "type": "ankle",
            "reason": "Ankle injury",
            "status": "out",
            "severity": "high",
            "source": "api-football",
            "updated_at": "2026-06-11T10:00:00Z"
          }
        ],
        "suspensions": [
          {
            "player_id": 67890,
            "player_name": "Casemiro",
            "reason": "Yellow card suspension",
            "matches_remaining": 1,
            "source": "api-football"
          }
        ],
        "total_count": 2
      }
    },
    "summary": {
      "total_teams": 32,
      "teams_with_injuries": 18,
      "total_injuries": 45,
      "total_suspensions": 12
    }
  },
  "data_sources": [
    {
      "type": "injuries",
      "source": "api-football",
      "fetched_at": "2026-06-11T10:00:00Z"
    }
  ]
}
```

**使用示例**:
```bash
# 每日采集（世界杯期间）
python scripts/fetch_injuries_api_football.py \
  --edition 2026 \
  --date $(date +%Y-%m-%d) \
  --root .

# 只采集今天有比赛的球队
python scripts/fetch_injuries_api_football.py \
  --edition 2026 \
  --date 2026-06-11 \
  --teams "MEX,RSA,KOR,CZE" \
  --root .
```

---

## 数据采集调度方案

### 方案A：手动触发（开发阶段）

```bash
# 每天早上执行一次
python scripts/fetch_injuries_api_football.py --edition 2026 --date 2026-06-11 --root .
python scripts/worldcup_live_fetcher.py fetch-odds --edition 2026 --date 2026-06-11 --root .
python scripts/worldcup_live_fetcher.py fetch-news --edition 2026 --date 2026-06-11 --root .
```

### 方案B：自动化调度（生产环境）

#### Windows 任务计划程序

1. 打开"任务计划程序"
2. 创建基本任务
3. 触发器：每天早上 8:00
4. 操作：启动程序
   - 程序：`python`
   - 参数：`scripts/fetch_injuries_api_football.py --edition 2026 --date %date:~0,10% --root .`
   - 起始于：`D:\res\project\FIFA-WINNER-SKILL`

#### Python APScheduler（推荐）

创建 `scripts/scheduler/daily_pipeline.py`:

```python
from apscheduler.schedulers.blocking import BlockingScheduler
from datetime import datetime
import subprocess

def run_daily_pipeline():
    """每日数据采集流水线"""
    date = datetime.now().strftime("%Y-%m-%d")
    edition = "2026"
    
    print(f"[{datetime.now()}] Starting daily pipeline for {date}...")
    
    # 1. 伤停数据
    subprocess.run([
        "python", "scripts/fetch_injuries_api_football.py",
        "--edition", edition,
        "--date", date,
        "--root", "."
    ])
    
    # 2. 赔率数据
    subprocess.run([
        "python", "scripts/worldcup_live_fetcher.py",
        "fetch-odds",
        "--edition", edition,
        "--date", date,
        "--root", "."
    ])
    
    # 3. 新闻数据
    subprocess.run([
        "python", "scripts/worldcup_live_fetcher.py",
        "fetch-news",
        "--edition", edition,
        "--date", date,
        "--root", "."
    ])
    
    print(f"[{datetime.now()}] Daily pipeline completed!")

# 创建调度器
scheduler = BlockingScheduler()

# 每天早上 8:00 执行
scheduler.add_job(run_daily_pipeline, 'cron', hour=8, minute=0)

print("Scheduler started. Press Ctrl+C to exit.")
scheduler.start()
```

运行调度器：
```bash
python scripts/scheduler/daily_pipeline.py
```

---

## API 配额管理

### API-Football Basic Plan 配额
- **每日请求数**: 3000
- **每分钟请求数**: 60

### 配额消耗估算（世界杯期间）

#### 每日固定消耗
- 伤停数据：32队 × 1 = **32 requests**
- 赔率数据：每天平均4场比赛 × 1 = **4 requests**
- 阵容数据：赛前2小时 × 2队 × 4场 = **8 requests**

**每日总计**: ~50 requests/day

#### 配额充足性
- Basic Plan: 3000 requests/day
- 实际消耗: ~50 requests/day
- **余量**: 60倍 ✅

---

## 数据质量监控

### 数据完整性检查

创建 `scripts/data_quality_check.py`:

```python
def check_daily_evidence(edition, date):
    """检查每日证据数据完整性"""
    evidence_file = f"knowledge-base/{edition}/data/daily-evidence/{date}.json"
    
    with open(evidence_file, 'r') as f:
        data = json.load(f)
    
    checks = {
        "has_injuries": "injuries" in data,
        "has_odds": "odds" in data,
        "has_news": "news" in data,
        "injuries_count": len(data.get("injuries", {}).get("teams", {})),
        "news_count": len(data.get("news", [])),
    }
    
    print(f"Data Quality Report for {date}:")
    print(f"  - Injuries: {'✓' if checks['has_injuries'] else '✗'} ({checks['injuries_count']} teams)")
    print(f"  - Odds: {'✓' if checks['has_odds'] else '✗'}")
    print(f"  - News: {'✓' if checks['has_news'] else '✗'} ({checks['news_count']} articles)")
    
    return all([checks['has_injuries'], checks['has_odds'], checks['has_news']])
```

---

## 故障排查

### 问题1：API Key 无效
**错误信息**: `API_FOOTBALL_KEY not found` 或 `401 Unauthorized`

**解决方案**:
1. 检查环境变量是否设置：`echo $env:API_FOOTBALL_KEY`
2. 确认 API Key 是否正确
3. 登录 API-Football 检查账户状态

### 问题2：配额耗尽
**错误信息**: `429 Too Many Requests`

**解决方案**:
1. 检查今日已使用配额
2. 等待配额重置（每天 UTC 00:00）
3. 考虑升级到 Pro Plan ($30/month, 10000 requests/day)

### 问题3：Team ID 未找到
**错误信息**: `Warning: Team code 'XXX' not found in TEAM_ID_MAP`

**解决方案**:
在 `TEAM_ID_MAP` 中添加国家队 ID：
```python
TEAM_ID_MAP = {
    "XXX": 12345,  # 新增的国家队
    # ...
}
```

如何查找 Team ID：
1. 访问 https://www.api-football.com/documentation-v3#tag/Teams
2. 使用 `/teams?name=Brazil` 搜索
3. 获取返回的 `team.id`

---

## 下一步计划

### 已完成 ✅
- [x] 伤停数据采集脚本
- [x] 数据结构设计
- [x] API 集成

### 进行中 🚧
- [ ] 阵容数据采集（赛前2小时）
- [ ] 舆情分析（NewsAPI + Reddit）
- [ ] 自动化调度器

### 待实施 📋
- [ ] 数据质量监控
- [ ] 故障自动切换
- [ ] Web Dashboard 集成

---

## 相关文档

- [数据采集调研报告](./data-collection-research.md)
- [架构改进方案](./architecture-improvement-plan.md)
- [API-Football 官方文档](https://www.api-football.com/documentation-v3)

---

**文档版本**: v1.0  
**最后更新**: 2026-06-11  
**维护者**: FIFA-WINNER-SKILL Team
