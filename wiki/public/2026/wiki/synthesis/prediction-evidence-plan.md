---
type: synthesis
edition: 2026
status: active
---

# 世界杯 2026 预测证据计划

这份计划列出赛前预测需要的证据、可信来源和当前缺口。它只做 readiness 判断，不直接抓取资料。

## 汇总

- 证据项：10
- complete：7
- partial：2
- blocked：1

## 证据项

### 官方赛程和比赛事实

- Evidence ID：`official_fixtures`
- 状态：complete
- 是否必需：是
- 推荐来源：fifa-match-schedule(T0)
- 用途：确定 match_id、开球时间、场馆、阶段、对阵和每天可预测比赛。
- 当前阻塞：无

### 官方球队阵容

- Evidence ID：`official_rosters`
- 状态：complete
- 是否必需：是
- 推荐来源：fifa-squad-lists-pdf(T0), national-fa-official-sites(T0)
- 用途：确认 48 队大名单、教练、球员位置、俱乐部和基础身份，支撑阵容深度判断。
- 当前阻塞：无

### FIFA 男足排名

- Evidence ID：`fifa_rankings`
- 状态：complete
- 是否必需：是
- 推荐来源：fifa-men-ranking(T0)
- 用途：提供跨队伍强弱基线，不能单独决定胜负，但影响数据模型基础分。
- 当前阻塞：无

### 历届世界杯成绩和比赛结果

- Evidence ID：`historical_worldcup_results`
- 状态：partial
- 是否必需：是
- 推荐来源：openfootball(T1), openfootball-worldcup-json(T1), wikipedia(T1)
- 用途：补足国家队世界杯经验、淘汰赛韧性、进球/失球历史基线。
- 当前阻塞：historical_results_compiled_from_snapshot_not_live_feed

### 近期国家队战绩

- Evidence ID：`recent_form_results`
- 状态：complete
- 是否必需：是
- 推荐来源：football-data-org(T2), api-football(T2), worldfootball-elo(T1), national-fa-official-sites(T0)
- 用途：反映近 6-12 个月状态、进攻/防守趋势和教练体系稳定性。
- 当前阻塞：无

### 阵容深度和位置平衡

- Evidence ID：`squad_depth_position_balance`
- 状态：complete
- 是否必需：是
- 推荐来源：fifa-squad-lists-pdf(T0)
- 用途：根据官方大名单统计门将、后卫、中场、前锋结构，识别板凳深度和位置短板。
- 当前阻塞：无

### 伤停、停赛和赛前可用性

- Evidence ID：`injury_availability`
- 状态：complete
- 是否必需：是
- 推荐来源：national-fa-official-sites(T0), espn-soccer(T3), fbref(T3), statbunker(T3)
- 用途：关键球员缺阵会显著影响胜平负和总进球倾向，必须赛前每日更新。
- 当前阻塞：无

### 场馆、休息天数和旅行因素

- Evidence ID：`venue_rest_travel`
- 状态：partial
- 是否必需：是
- 推荐来源：fifa-match-schedule(T0)
- 用途：根据赛程计算休息间隔、跨城市旅行和主办国/近主场因素。
- 当前阻塞：travel_distance_not_modeled

### 交锋历史

- Evidence ID：`head_to_head`
- 状态：complete
- 是否必需：否
- 推荐来源：openfootball(T1), openfootball-worldcup-json(T1), wikipedia(T1)
- 用途：作为低权重参考，帮助解释风格克制和历史心理因素。
- 当前阻塞：无

### 球员身份和别名补强

- Evidence ID：`player_identity_enrichment`
- 状态：blocked
- 是否必需：否
- 推荐来源：wikidata-sparql(T1), wikipedia(T1)
- 用途：用 Wikidata/Wikipedia 对齐球员别名、出生日期、国家队/俱乐部关系，方便后续深档和海报。
- 当前阻塞：wikidata_identity_enrichment_missing
