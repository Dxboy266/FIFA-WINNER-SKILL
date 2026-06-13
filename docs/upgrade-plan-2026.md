## FIFA-WINNER-SKILL 综合升级方案

基于对项目 32 个 Python 脚本（约 460 KB 源码）、14 个 JSON Schema、知识库数据完整性、Agent 接口层以及已有架构文档的全面分析，制定本升级方案。

---

## 一、项目健康诊断报告

### 1.1 整体评估

| 维度 | 状态 | 说明 |
|------|------|------|
| 语法检查 | 通过 | 全部 32 个脚本零语法错误 |
| 单元测试 | 17/18 通过 (94.4%) | 1 个导出打包测试失败，非核心逻辑 |
| 核心架构 | 良好 | 五大模块清晰分离，安全护栏完善 |
| 知识库数据 | 基本就绪 | 48 支球队、104 场比赛、排名/阵容深度齐备 |
| Agent 接口 | 完善 | AGENT_CARD / TOOL_CATALOG / HANDOFFS / TRACE 齐全 |
| 测试覆盖 | 不足 | SQLite 层、评估器、情报简报等核心模块零覆盖 |

### 1.2 发现的两个关键 Bug

**Bug 1 — 客队名称匹配错误（P0 级别）**

`worldcup_live_fetcher.py` 第 113 行将 `away_name` 错误赋值为 `match["home_team"]["name"]`，导致赔率数据始终无法正确匹配客队，直接影响双轨背离模型中市场轨道的准确性。

**Bug 2 — 新闻键名不匹配（P0 级别）**

`extract_injuries_from_news.py` 从 `"news"` 键读取新闻列表，但管道中其他脚本将新闻写入 `"late_news"` 键。这导致 NLP 伤停提取器永远读不到任何新闻数据，功能完全失效。

### 1.3 架构层面的六大改进空间

**1. 代码重复：** `worldcup_core.py` 和 `prediction_scoring_model.py` 各自实现了 `build_play_card()` 和 `build_prediction()`，逻辑有分歧，容易产生不一致的输出。

**2. 巨型函数：** `predict_match()` 函数长达约 320 行，承担了数据查找、多维打分、玄学修正、市场分析、信心判定、分析层构建、场景推演、玩法卡片生成等所有职责，急需拆分。

**3. 伤停数据三格式不兼容：** `daily_evidence_input.py` 使用扁平列表，`extract_injuries_from_news.py` 使用按球队嵌套字典，`fetch_injuries_api_football.py` 也使用嵌套字典但结构不同。下游消费者无法统一处理。

**4. 缺乏数据时效性追踪：** 管道无法区分新鲜数据和过期数据，旧的证据和刚抓取的数据被同等对待。

**5. 特征计算分散：** 排名权重、阵容深度、休息天数等特征计算散落在各脚本中，没有统一的特征注册表。

**6. 单一预测模型：** 目前只有加权打分模型 + 玄学修正，缺少泊松分布、蒙特卡洛模拟等多元化预测手段。

---

## 二、升级路线图（四大阶段）

### 阶段一：修复关键问题与基础加固（1-2 周）

这一阶段的目标是修复已知 Bug、补齐测试覆盖、统一数据格式，为后续的架构升级打好地基。

**1.1 修复两个 P0 Bug**

修复 `worldcup_live_fetcher.py` 的客队名称匹配错误，以及 `extract_injuries_from_news.py` 的 `"news"` -> `"late_news"` 键名不匹配。同时修复测试中缺失的 `examples/` 目录问题。

**1.2 统一伤停数据格式**

定义一个伤停数据的规范 schema：扁平列表结构，每条记录包含 `team_code`、`player_name`、`type`（injury/suspension）、`severity`、`status`、`source`（manual/nlp/api）、`recorded_at` 时间戳。所有三个写入方（手动、NLP、API）都统一追加到同一个列表，通过 `source` 字段区分来源。添加按 `(team_code, player_name, date)` 的去重逻辑。

**1.3 共享基础设施提取**

将以下重复逻辑提取到 `worldcup_core.py` 中：空白证据模板工厂函数 `empty_evidence_payload()`、球队名称/代码映射字典、严重程度/状态枚举值。消除 `daily_evidence_input.py`、`worldcup_live_fetcher.py`、`extract_injuries_from_news.py` 之间的三处重复代码。

**1.4 补充关键模块测试**

为以下模块添加单元测试：`worldcup_db.py`（SQLite 层）、`prediction_evaluator.py`（评估器）、`tianji_oracle.py`（玄学层）、`extract_injuries_from_news.py`（NLP 提取器）。目标测试覆盖率从当前的 94.4%（按用例数）提升到核心模块全覆盖。

**1.5 替换弃用 API**

将 `extract_injuries_from_news.py` 和 `fetch_injuries_api_football.py` 中的 `datetime.utcnow()` 全部替换为 `datetime.now(timezone.utc)`，以兼容 Python 3.12+。

**1.6 修复天纪神算的干支错误**

`tianji_oracle.py` 第 249 行将 2026 年标记为"丙午马年"，实际 2026 年为丙午年（马年），但农历转换函数 `get_lunar_date_2026()` 假设所有月份固定 29 天，这与实际农历月份（29-30 天不等）有偏差。引入 `lunarcalendar` 或 `cnlunar` 库以确保准确性。

---

### 阶段二：特征工程与模型升级（2-3 周）

这一阶段的目标是将分散的特征计算统一到注册表中，并引入多模型集成框架。

**2.1 特征注册表 (FeatureRegistry)**

在 `scripts/feature_engineering/` 目录下创建统一的特征注册表。每个特征定义包含：`feature_id`、`feature_name`、`weight`、`calculator`（计算函数引用）、`dependencies`（依赖的数据源列表）。当前的五大特征（排名强度 30%、阵容深度 20%、历史底蕴 20%、休息旅途 15%、证据完整度 15%）全部迁移到注册表中。同时注册新的特征：进攻火力（场均进球/xG）、防守能力（场均失球/xGA）、核心球员影响因子（伤停时动态调整）、动机指数（出线形势、复仇情结等）。

**2.2 核心预测函数拆分重构**

将 `predict_match()` 从 320 行的巨型函数拆分为 6-8 个职责单一的小函数或一个 `MatchPredictor` 类。建议拆分为：`load_match_context()`（加载数据）、`compute_data_score()`（基本面打分）、`compute_divination_overlay()`（玄学修正）、`analyze_market_track()`（市场轨道分析）、`determine_confidence()`（信心判定）、`build_analysis_layers()`（七层分析栈）、`build_scenario_analysis()`（场景推演）、`assemble_report()`（组装最终报告）。消除 `build_play_card()` 和 `build_prediction()` 的重复实现，统一使用 `worldcup_core.py` 中的版本。

**2.3 多模型集成框架 (ModelEnsemble)**

在 `scripts/prediction_models/` 目录下实现多模型集成：

- **泊松分布模型**：基于进攻/防守火力计算最可能比分概率分布。
- **Elo 评分模型**：基于 FIFA 排名和历史战绩的 Elo 胜率转换。
- **蒙特卡洛模拟**：10,000 次随机模拟，输出胜平负概率分布。
- **双轨背离模型**：保留现有的核心模型。
- **天纪玄学修正**：保留为娱乐层，权重降至 5-10%。

集成器通过加权平均或投票机制合并各模型的预测。当前发布口径保持数据模型 60% / 天纪娱乐层最高 40%。天纪必须按比赛场馆当地时间计算，并且只能作为娱乐叙事与轻量修正，不能覆盖伤停、天气、阵容、赛程等硬证据。

**2.4 评估器增强**

为 `prediction_evaluator.py` 添加 Brier 评分（概率预测的多维评分规则）和累积模式（跨比赛日聚合评估）。将信心校准从内嵌 JSON 改为可读的 Markdown 表格输出。

---

### 阶段三：Agent 智能化升级（2-3 周）

这一阶段的目标是让章鱼哥从"需要人手动触发脚本"进化为"具备自主规划与反思能力的智能 Agent"。

**3.1 自主 ReAct 规划循环**

在 `scripts/agent_runtime/` 目录下实现一个轻量级的 ReAct（Reasoning + Acting）循环引擎。当接收到"帮我预测明天的比赛"这类指令时，Agent 能够自主拆解子任务：检查赛程 → 采集赔率 → 采集新闻 → 提取伤停 → 检查证据完整度 → 补充缺失证据 → 生成预测 → 输出报告。每一步的推理过程和工具调用结果都记录在 trace 中。

**3.2 赛后反思与权重自调整**

在 `scripts/agent_runtime/self_reflection.py` 中实现赛后反思机制。每场比赛结束后，Agent 自动比对预测与实际赛果，写入反思日志（哪些特征偏差最大、玄学修正是否合理、市场轨道是否被低估）。当某类预测（如爆冷预警）出现持续偏差时，通过反射机制微调特征权重，实现自适应演进。

**3.3 证据时效性与自动补给**

为所有证据条目添加 `recorded_at` 时间戳。在情报简报（`matchday_intelligence_briefing.py`）中引入时效性检查：超过 6 小时的证据标记为"陈旧"。添加 `--auto-fill` 模式，让情报简报能自动调用采集脚本补充常见缺口（赔率、新闻），而不仅仅是建议命令。

**3.4 管道编排器**

创建一个简洁的编排脚本 `scripts/daily_pipeline_orchestrator.py`，将完整的日常流水线串联起来：`fetch-odds -> fetch-news -> extract-injuries -> intelligence-briefing -> predict -> poster`。支持 `--dry-run` 模式预览将执行的操作，支持按日期、按分组、按对阵的灵活调度。

---

### 阶段四：可视化与对外输出（2-3 周）

这一阶段的目标是让用户能通过 Web 看板直观查看预测，并提升社交传播能力。

**4.1 Web Dashboard 原型**

基于现有的 `prediction_visual_dashboard.py`（已能生成 Markdown 看板），扩展为一个轻量级的 Flask/FastAPI Web 应用。核心页面包含：当日比赛卡片（队徽、预测比分、胜平负概率条形图、双轨背离指示器、信心指数）、历史命中率追踪面板、模拟积分走势。使用响应式设计，支持移动端查看。

**4.2 赛后评估看板升级**

将 `prediction_evaluation_dashboard.py` 的聚合数据接入 Web Dashboard，展示：胜平负命中率趋势图、比分偏差分布、信心校准曲线（高/中/低信心各自的历史命中率）、模型问题标签统计（如"定位球威胁低估"出现频次）。

**4.3 社交传播增强**

优化玩法卡片和海报 Prompt 的输出质量。增加短视频脚本模板（15 秒/60 秒两档），便于在社交平台传播。添加比赛日倒计时分享卡片。

---

## 三、优先级与时间线总览

| 阶段 | 内容 | 预估时间 | 关键产出 |
|------|------|----------|----------|
| 一 | 修复 Bug + 基础加固 | 1-2 周 | 两个 P0 Bug 修复、统一伤停格式、补齐测试 |
| 二 | 特征工程 + 模型升级 | 2-3 周 | FeatureRegistry、多模型集成、评估器增强 |
| 三 | Agent 智能化 | 2-3 周 | ReAct 循环、赛后反思、管道编排器 |
| 四 | 可视化与输出 | 2-3 周 | Web Dashboard、评估看板、社交传播 |
| **总计** | | **7-11 周** | |

---

## 四、关键决策建议

**关于玄学层权重：** 当前决策保留数据模型 60% / 天纪娱乐层最高 40%。提高天纪占比是产品特色，但必须配套三条约束：第一，按比赛场馆当地时间排盘；第二，展示时单独标明换算时间；第三，不允许天纪覆盖已验证的球队、球员、天气、伤停、阵容和赛程事实。

**关于 LLM 自动下注：** 可以做模拟积分系统追踪预测准确率，但绝不做真实投注建议。强化免责声明，每次输出都携带。

**关于数据存储策略：** 维持现有的 JSON + SQLite 双层架构。JSON 作为不可变的审计记录（canonical），SQLite 作为查询索引层。冲突时以 JSON 为准。

**关于外部依赖：** 项目目前仅有 `pdfplumber` 一个外部依赖。建议保持精简原则，新增的多模型集成和特征工程优先使用纯 Python + 标准库实现。Web Dashboard 可引入 Flask/FastAPI 作为可选依赖。

---

## 五、立即可执行的行动项

以下是在阶段一中最先应该完成的 5 项工作：

1. 修复 `worldcup_live_fetcher.py` 第 113 行的 `away_name` 赋值 Bug
2. 修复 `extract_injuries_from_news.py` 的 `"news"` -> `"late_news"` 键名不匹配
3. 在 `worldcup_core.py` 中添加 `empty_evidence_payload()` 工厂函数，统一证据模板
4. 拆分 `predict_match()` 巨型函数为模块化组件
5. 修复 `tianji_oracle.py` 的农历转换精度问题
