# 世界杯预测消息面数据采集调研报告

## 调研目标
为 FIFA-WINNER-SKILL 项目建立**自动化消息面数据采集系统**，重点关注：
1. 伤停名单（Injuries & Suspensions）
2. 首发阵容（Starting Lineups）
3. 球队新闻（Team News & Press）
4. 舆情分析（Sentiment Analysis）
5. 赔率动态（Odds Movement）

---

## 一、核心数据源分类

### 1. 官方数据源（权威但更新慢）

#### FIFA 官方 API
- **URL**: https://api.fifa.com/
- **优势**: 官方数据、100%准确
- **劣势**: 无实时伤停、新闻需要爬虫
- **适用**: 赛程、积分榜、历史数据
- **成本**: 免费（公开API）

#### Transfermarkt
- **URL**: https://www.transfermarkt.com/
- **GitHub**: [dcaribou/transfermarkt-scraper](https://github.com/dcaribou/transfermarkt-scraper)
- **优势**: 
  - 球员身价、转会记录
  - 伤病历史数据完整
  - 支持国家队和俱乐部
- **劣势**: 需要爬虫（无官方API）
- **实现方式**:
  ```python
  # 基于已有的 transfermarkt-scraper
  python -m tfmkt players --national-team "Brazil"
  python -m tfmkt injuries --team-id 3449  # Brazil national team
  ```
- **成本**: 免费（爬虫）
- **频率**: 每日1次（避免封IP）

---

### 2. 体育数据平台（综合性强）

#### API-Football (RapidAPI)
- **URL**: https://rapidapi.com/api-sports/api/api-football
- **文档**: https://www.api-football.com/documentation-v3
- **优势**:
  - **伤停API**: `/injuries` 实时更新
  - **阵容API**: `/fixtures/lineups` 赛前2小时发布
  - **球队新闻**: `/teams/news` (部分支持)
  - **赔率API**: `/odds` 支持主流博彩公司
- **数据覆盖**:
  - 世界杯 ✅
  - 国家队友谊赛 ✅
  - 实时伤停 ✅
  - 首发阵容 ✅
- **成本**: 
  - 免费层: 100 requests/day
  - Basic: $10/month (3000 requests/day)
  - Pro: $30/month (10000 requests/day)
- **推荐方案**: Basic 层足够（世界杯期间）

#### Football-Data.org
- **URL**: https://www.football-data.org/
- **GitHub**: https://github.com/openfootball
- **优势**:
  - 完全免费
  - RESTful API
  - 覆盖主流联赛和国际赛事
- **劣势**:
  - **无伤停数据** ❌
  - **无新闻API** ❌
  - 仅提供赛程、比分、积分榜
- **适用**: 作为辅助数据源
- **成本**: 免费（10 requests/minute）

#### SofaScore API（非官方）
- **URL**: https://www.sofascore.com/
- **GitHub**: 多个非官方爬虫项目
- **优势**:
  - 实时比分和统计
  - 阵容和伤停实时更新
  - 赛前预测和赔率
- **劣势**: 无官方API，需要逆向工程
- **实现方式**: 
  ```python
  # 非官方API endpoint（需要验证）
  GET https://api.sofascore.com/api/v1/event/{match_id}/lineups
  GET https://api.sofascore.com/api/v1/team/{team_id}/injuries
  ```
- **风险**: 可能被封锁
- **成本**: 免费（爬虫）

#### FotMob API（非官方）
- **URL**: https://www.fotmob.com/
- **GitHub**: 多个非官方包装项目
- **优势**:
  - 移动端优化的数据结构
  - 实时更新伤停和阵容
  - 丰富的球队新闻
- **实现方式**:
  ```python
  # 非官方API（移动端接口）
  GET https://www.fotmob.com/api/matches?date=2026-06-11
  GET https://www.fotmob.com/api/teams?id=9825  # Brazil
  ```
- **成本**: 免费（爬虫）

---

### 3. 新闻与舆情数据源

#### NewsAPI
- **URL**: https://newsapi.org/
- **优势**:
  - 支持关键词过滤（"Brazil injury", "Argentina lineup"）
  - 支持多语言（英语、西班牙语、葡萄牙语）
  - 支持日期范围查询
- **示例查询**:
  ```python
  GET https://newsapi.org/v2/everything?
      q="Brazil football injury" OR "Brazil starting lineup"&
      from=2026-06-10&
      to=2026-06-11&
      language=en&
      sortBy=publishedAt
  ```
- **成本**:
  - 免费层: 100 requests/day
  - Developer: $449/month (10000 requests/day)
- **推荐**: 免费层足够（每日1次批量查询）

#### ESPN API / RSS Feed
- **URL**: 
  - RSS: https://www.espn.com/espn/rss/news
  - 非官方API: https://site.api.espn.com/apis/site/v2/sports/soccer/
- **优势**:
  - 权威的体育新闻
  - RSS Feed 免费
  - 包含伤停报道和首发预测
- **实现方式**:
  ```python
  # 当前项目已有 ESPN RSS 抓取
  python scripts/worldcup_live_fetcher.py fetch-news --edition 2026 --date 2026-06-11
  ```
- **成本**: 免费

#### Twitter API (X API)
- **URL**: https://developer.twitter.com/
- **优势**:
  - 实时性最强（官方账号第一时间发布伤停）
  - 舆情分析最佳数据源
- **关键账号**:
  - @FIFAWorldCup
  - @CBF_Futebol (巴西足协)
  - @Argentina (阿根廷足协)
  - 各队官方 Twitter
- **示例查询**:
  ```python
  # Twitter API v2
  GET https://api.twitter.com/2/tweets/search/recent?
      query=(@CBF_Futebol OR @FIFAWorldCup) injury OR lesion&
      max_results=100
  ```
- **成本**:
  - 免费层: **已取消** ❌
  - Basic: $100/month (10000 tweets/month)
  - Pro: $5000/month
- **替代方案**: 使用 nitter.net 爬虫（免费但不稳定）

#### Reddit API
- **URL**: https://www.reddit.com/dev/api/
- **优势**:
  - 免费且丰富的社区讨论
  - r/soccer, r/worldcup 实时更新
  - 球迷情绪分析的好来源
- **实现方式**:
  ```python
  import praw
  reddit = praw.Reddit(client_id='...', client_secret='...')
  subreddit = reddit.subreddit('soccer')
  for post in subreddit.search('Brazil injury', time_filter='day'):
      print(post.title, post.score)
  ```
- **成本**: 免费（需注册应用）

---

### 4. 赔率数据源（市场信号）

#### The Odds API
- **URL**: https://the-odds-api.com/
- **文档**: https://the-odds-api.com/liveapi/guides/v4/
- **优势**:
  - 官方API，稳定可靠
  - 支持多家博彩公司
  - 实时赔率更新
- **当前项目已集成**: ✅
  ```python
  python scripts/worldcup_live_fetcher.py fetch-odds --edition 2026 --date 2026-06-11
  ```
- **成本**:
  - 免费层: 500 requests/month
  - Starter: $50/month (10000 requests/month)
- **推荐**: 免费层足够（世界杯64场比赛）

#### 中国体育彩票竞彩网
- **URL**: https://www.sporttery.cn/
- **优势**:
  - 官方数据，中国市场参考
  - 固定奖金赔率
- **参考项目**: [Crain99/worldcut-2026](https://github.com/Crain99/worldcut-2026)
- **实现方式**: 爬虫（需要代理）
- **成本**: 免费

---

## 二、推荐的数据采集架构

### 数据采集层级（按优先级）

#### P0 核心数据（必须有）
1. **伤停名单**: API-Football `/injuries` (实时)
2. **赔率数据**: The Odds API (当前已有)
3. **赛程与比分**: FIFA 官方 / Football-Data.org

#### P1 重要数据（增强准确性）
4. **首发阵容**: API-Football `/fixtures/lineups` (赛前2小时)
5. **球队新闻**: ESPN RSS Feed (当前已有)
6. **球员身价**: Transfermarkt 爬虫（每周1次）

#### P2 辅助数据（提升可玩性）
7. **舆情分析**: NewsAPI 关键词搜索
8. **社区讨论**: Reddit API 情绪分析
9. **官方动态**: Twitter API（可选，成本高）

---

## 三、具体实现方案

### 方案A：最小化成本方案（推荐）

**月成本**: ~$10-20

#### 数据源组合
1. **API-Football Basic** ($10/month)
   - 伤停、阵容、赔率（备用）
2. **NewsAPI 免费层** (100 requests/day)
   - 关键词新闻搜索
3. **ESPN RSS** (免费)
   - 权威新闻源
4. **The Odds API 免费层** (500 requests/month)
   - 主力赔率数据
5. **Reddit API** (免费)
   - 舆情与情绪分析
6. **Transfermarkt 爬虫** (免费)
   - 球员身价与伤病历史

#### 数据采集流程
```python
# 每日自动化流程（世界杯期间）
def daily_data_collection(edition, date):
    # 1. 伤停数据（API-Football）
    fetch_injuries_api_football(edition, date)
    
    # 2. 赔率数据（The Odds API）
    fetch_odds(edition, date)  # 当前已有
    
    # 3. 新闻数据（ESPN RSS + NewsAPI）
    fetch_news_espn(edition, date)  # 当前已有
    fetch_news_api(edition, date, keywords=["injury", "lineup", "suspended"])
    
    # 4. 舆情数据（Reddit API）
    fetch_reddit_sentiment(edition, date)
    
    # 5. 阵容预测（赛前2小时触发）
    if is_match_day(date) and hours_before_kickoff < 2:
        fetch_lineups_api_football(edition, date)
```

### 方案B：完全免费方案（备用）

**月成本**: $0

#### 数据源组合（全部爬虫）
1. **SofaScore 爬虫** - 伤停、阵容
2. **FotMob 爬虫** - 新闻、统计
3. **ESPN RSS** - 权威新闻
4. **The Odds API 免费层** - 赔率
5. **Reddit API** - 舆情
6. **Transfermarkt 爬虫** - 球员数据

#### 风险
- 爬虫可能被封锁
- 需要维护多个爬虫脚本
- 数据稳定性差

---

## 四、数据结构设计

### 伤停数据结构
```json
{
  "date": "2026-06-11",
  "team_code": "BRA",
  "team_name": "Brazil",
  "injuries": [
    {
      "player_id": "123456",
      "player_name": "Neymar Jr",
      "position": "Forward",
      "injury_type": "ankle",
      "status": "out",  // out | doubtful | expected_return
      "expected_return": "2026-06-15",
      "severity": "high",  // high | medium | low
      "source": "api-football",
      "updated_at": "2026-06-11T10:00:00Z"
    }
  ],
  "suspensions": [
    {
      "player_id": "789012",
      "player_name": "Casemiro",
      "reason": "yellow_cards",
      "matches_remaining": 1,
      "source": "fifa-official"
    }
  ]
}
```

### 新闻舆情数据结构
```json
{
  "date": "2026-06-11",
  "team_code": "BRA",
  "news": [
    {
      "title": "Neymar ruled out of opening match",
      "summary": "Brazil star Neymar will miss...",
      "source": "ESPN",
      "url": "https://...",
      "published_at": "2026-06-11T08:30:00Z",
      "sentiment": "negative",  // positive | neutral | negative
      "keywords": ["injury", "neymar", "out"],
      "relevance_score": 0.95
    }
  ],
  "sentiment_score": -0.42,  // -1.0 (very negative) to +1.0 (very positive)
  "key_topics": ["injury concerns", "lineup changes", "tactical adjustments"]
}
```

### 阵容数据结构
```json
{
  "match_id": "2026-GA-01",
  "match_date": "2026-06-11",
  "home_team": "MEX",
  "away_team": "RSA",
  "home_lineup": {
    "formation": "4-3-3",
    "starting_11": [
      {
        "player_id": "123",
        "player_name": "Guillermo Ochoa",
        "position": "GK",
        "jersey_number": 13,
        "market_value": 2000000,
        "form_rating": 7.5  // 最近5场平均评分
      }
      // ... 其他10名球员
    ],
    "substitutes": [...],
    "coach": "Javier Aguirre",
    "source": "api-football",
    "confidence": "confirmed"  // predicted | probable | confirmed
  },
  "away_lineup": {...}
}
```

---

## 五、舆情分析算法设计

### 情绪词典（Sentiment Lexicon）

#### 正向词（Positive）
```python
POSITIVE_WORDS = {
    # 英语
    "return", "recovered", "fit", "ready", "confident", "form", "strong",
    "motivated", "united", "prepared", "excellent", "impressive",
    
    # 西班牙语
    "recuperado", "listo", "fuerte", "motivado", "excelente",
    
    # 葡萄牙语  
    "recuperado", "pronto", "forte", "motivado", "excelente",
    
    # 中文
    "复出", "康复", "状态", "信心", "团结", "准备充分"
}
```

#### 负向词（Negative）
```python
NEGATIVE_WORDS = {
    # 英语
    "injury", "injured", "out", "doubt", "doubtful", "suspended", "banned",
    "crisis", "concern", "problem", "struggle", "conflict", "tension",
    
    # 西班牙语
    "lesión", "lesionado", "duda", "suspendido", "crisis", "problema",
    
    # 葡萄牙语
    "lesão", "machucado", "dúvida", "suspenso", "crise", "problema",
    
    # 中文
    "伤停", "受伤", "缺阵", "疑似", "停赛", "内讧", "问题"
}
```

### 舆情评分算法
```python
def calculate_sentiment_score(news_list):
    """
    计算舆情评分 (-1.0 到 +1.0)
    """
    total_score = 0
    for news in news_list:
        # 1. 关键词匹配
        positive_count = count_keywords(news['text'], POSITIVE_WORDS)
        negative_count = count_keywords(news['text'], NEGATIVE_WORDS)
        
        # 2. 标题权重更高
        if news['type'] == 'headline':
            weight = 2.0
        else:
            weight = 1.0
        
        # 3. 时效性衰减（24小时内权重1.0，之后每天衰减10%）
        hours_ago = (now - news['published_at']).total_seconds() / 3600
        time_decay = max(0.5, 1.0 - (hours_ago - 24) / 240)  # 10天后最低0.5
        
        # 4. 来源可信度权重
        source_weight = {
            'fifa-official': 1.0,
            'espn': 0.9,
            'bbc-sport': 0.9,
            'transfermarkt': 0.8,
            'twitter': 0.6,
            'reddit': 0.5
        }.get(news['source'], 0.7)
        
        # 综合评分
        news_score = (positive_count - negative_count) * weight * time_decay * source_weight
        total_score += news_score
    
    # 归一化到 [-1, 1]
    return np.tanh(total_score / len(news_list)) if news_list else 0.0
```

---

## 六、实施优先级与时间表

### 第一周：核心数据源集成
1. **注册 API-Football**（1天）
   - 获取 API Key
   - 测试 `/injuries` 和 `/fixtures/lineups` 接口
2. **实现伤停采集**（2天）
   - 编写 `fetch_injuries_api_football.py`
   - 数据存储到 `daily-evidence/<date>.json`
3. **实现阵容采集**（1天）
   - 编写 `fetch_lineups_api_football.py`
4. **测试与验证**（1天）

### 第二周：新闻与舆情
5. **集成 NewsAPI**（2天）
   - 关键词搜索
   - 多语言支持
6. **实现舆情分析**（2天）
   - 情绪词典构建
   - 评分算法实现
7. **集成 Reddit API**（1天）

### 第三周：自动化调度
8. **数据采集调度器**（2天）
   - 每日定时任务
   - 赛前2小时阵容抓取
9. **数据质量监控**（2天）
   - 缺失数据报警
   - 数据源故障切换
10. **文档与测试**（1天）

---

## 七、成本效益分析

### 月度成本对比

| 方案 | 数据源 | 月成本 | 数据质量 | 稳定性 | 推荐度 |
|------|--------|--------|----------|--------|--------|
| 方案A（推荐） | API-Football + NewsAPI + 免费源 | $10-20 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 方案B（备用） | 全部免费爬虫 | $0 | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ |
| 方案C（高级） | 多个付费API | $100+ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ |

### ROI 分析
- **预测准确率提升**: 有伤停数据预计提升 8-12%
- **用户体验**: 实时更新提升用户粘性
- **运营效率**: 自动化采集节省 90% 时间

---

## 八、风险与应对

### 风险1：API 配额耗尽
**应对**: 
- 分层调用策略（重要比赛 > 一般比赛）
- 自动降级到免费爬虫

### 风险2：数据源失效
**应对**:
- 多数据源备份
- 自动故障检测与切换

### 风险3：爬虫被封
**应对**:
- 使用代理IP池
- 限制请求频率
- User-Agent 轮换

---

## 九、总结与建议

### 最佳实践
1. **优先使用官方API**（稳定可靠）
2. **爬虫作为备用**（降低成本）
3. **多数据源交叉验证**（提高准确性）
4. **定时任务 + 赛前触发**（实时性）

### 下一步行动
1. **立即执行**: 注册 API-Football Basic ($10/month)
2. **本周完成**: 伤停数据采集脚本
3. **两周内**: 完整的自动化采集流水线
4. **持续优化**: 舆情分析与情绪评分

### 推荐技术栈
```python
# 数据采集
requests  # HTTP 请求
beautifulsoup4  # HTML 解析
selenium  # 动态页面爬取

# 任务调度
APScheduler  # Python 定时任务
celery  # 分布式任务队列（可选）

# 数据处理
pandas  # 数据清洗
nltk / spaCy  # 自然语言处理
textblob  # 情感分析

# 存储
sqlite3  # 查询层
json  # 审计层
```

---

## 附录：关键 GitHub 项目参考

1. **transfermarkt-scraper**: https://github.com/dcaribou/transfermarkt-scraper
2. **football-data**: https://github.com/openfootball
3. **worldcut-2026**: https://github.com/Crain99/worldcut-2026
4. **awesome-football**: https://github.com/planetopendata/awesome-football

---

**文档版本**: v1.0  
**最后更新**: 2026-06-11  
**作者**: AI Octopus Paul Team
