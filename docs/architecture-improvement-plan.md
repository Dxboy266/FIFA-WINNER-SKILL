# FIFA-WINNER-SKILL 架构改进方案

基于以下三个信息源的分析：
1. 当前项目 FIFA-WINNER-SKILL 的架构
2. Crain99/worldcut-2026 的实时可视化方案
3. 抖音视频中的四层预测模型架构

---

## 一、当前项目架构分析

### ✅ 优势
1. **双轨背离模型**：物理轨（实力+玄学）vs 市场轨（赔率），独特且有娱乐性
2. **模块化设计**：5大核心模块清晰分离
3. **Agent-to-Agent 接口**：完善的 AGENT_CARD.json 和 TOOL_CATALOG.json
4. **知识库结构**：统一的 knowledge-base 目录，便于 Agent 读取
5. **多层分析**：evidence_integrity、scenario_analysis、decision_audit

### ⚠️ 短板
1. **数据采集层**：依赖手动触发，缺乏自动化调度
2. **特征工程层**：特征计算分散在各个脚本中，没有统一的特征注册表
3. **预测模型层**：单一打分模型，缺少多模型集成
4. **可视化层**：只有海报生成，缺少实时交互式看板

---

## 二、worldcut-2026 的启发

### 关键亮点
1. **实时数据源**：直接对接中国体育彩票竞彩网固定奖金
2. **Web 可视化**：HTML + Python Flask 实时看板
3. **LLM 自动下注**：模拟账户 + 历史记录 + SQLite 持久化
4. **移动端适配**：响应式设计

### 可借鉴点
- ✅ **实时数据管道**：定时抓取赔率，而不是手动触发
- ✅ **Web Dashboard**：用户可以通过浏览器查看预测，而不是只看 JSON
- ✅ **模拟账户系统**：追踪预测准确率，建立信誉评分
- ✅ **SQLite 持久化**：既有 JSON 作为 audit trail，又有 SQLite 作为查询层

---

## 三、抖音四层架构模型

根据视频中的架构图，标准的世界杯预测模型应该分为：

### 1. 数据采集层 (Data Collection Layer)
**当前状态**：✅ 已有但需要自动化

**现有数据源**：
- FIFA 排名 / Elo 评分
- 近10场赛果
- 球员身价
- 伤停名单
- 历史交锋记录
- 旅行与休息日
- 天气 / 地理 / 裁判
- 市场赔率
- 舆情/总分

**建议改进**：
```python
# 新增：数据采集调度器
class DataCollectionScheduler:
    """
    定时任务调度器，每天自动执行：
    1. fetch-odds (赔率)
    2. fetch-news (舆情)
    3. fetch-injuries (伤停)
    4. fetch-weather (天气)
    """
    def run_daily_pipeline(self, edition, date):
        # 自动化数据采集流程
        pass
```

### 2. 特征工程层 (Feature Engineering Layer)
**当前状态**：⚠️ 分散在各个脚本中

**现有特征**：
- 进攻火力 (场均进球 / XG)
- 防守能力 (场均失球 / XGA)
- 主客场优势系统 (+0.3-0.5球)
- 核心球员影响因子
- 动机指数 (出线形势、第三名、复仇情结)
- 隐含概率

**建议改进**：
```python
# 新增：特征注册表 + 特征工程管道
class FeatureRegistry:
    """
    统一的特征注册表，所有特征在这里定义：
    - feature_id: 唯一标识
    - feature_name: 特征名称
    - weight: 权重
    - calculator: 计算函数
    - dependencies: 依赖的数据源
    """
    
    FEATURES = {
        "attack_power": {
            "weight": 0.15,
            "calculator": calculate_attack_power,
            "dependencies": ["match_ledger", "team_stats"]
        },
        "defense_ability": {
            "weight": 0.15,
            "calculator": calculate_defense_ability,
            "dependencies": ["match_ledger", "team_stats"]
        },
        # ... 更多特征
    }

class FeatureEngineer:
    """
    特征工程管道，自动计算所有注册的特征
    """
    def compute_all_features(self, home_team, away_team, date):
        features = {}
        for feature_id, config in FeatureRegistry.FEATURES.items():
            features[feature_id] = config["calculator"](home_team, away_team, date)
        return features
```

### 3. 预测模型层 (Prediction Model Layer)
**当前状态**：⚠️ 单一打分模型

**现有模型**：
- 基本面打分模型 (加权求和)
- 玄学修正模型 (天纪神算)

**建议改进**：
```python
# 新增：多模型集成框架
class ModelEnsemble:
    """
    集成多个预测模型：
    1. 泊松分布模型 (Poisson Distribution) - 基于进攻/防守火力
    2. 逻辑回归/排名比较 (Logistic Regression / Elo Rating)
    3. Elo胜率转换 (Elo Win Rate Conversion) - 基于3%~5%
    4. 蒙特卡洛模拟 (Monte Carlo Simulation) - 10,000次模拟
    5. 双轨背离模型 (当前的核心模型)
    6. 天纪玄学修正 (娱乐层)
    """
    
    models = [
        ("poisson", PoissonModel(), 0.25),
        ("elo", EloModel(), 0.20),
        ("monte_carlo", MonteCarloModel(), 0.20),
        ("dual_track", DualTrackDivergenceModel(), 0.25),  # 你的核心模型
        ("tianji", TianjiOracleModel(), 0.10),  # 玄学娱乐
    ]
    
    def predict(self, features):
        """
        集成预测：加权平均或投票机制
        """
        predictions = []
        for name, model, weight in self.models:
            pred = model.predict(features)
            predictions.append((pred, weight))
        
        # 加权平均
        final_pred = weighted_average(predictions)
        return final_pred
```

**模型说明**：
- **泊松分布模型**：最可能比分 = 2:1 / 1:0 / 比赛概率
- **逻辑回归/排名比较**：胜平负概率
- **Elo胜率转换**：基于 3%~5% 的基线调整
- **蒙特卡洛模拟**：10,000次随机模拟，输出终盘概率
- **双轨背离**：你的特色模型
- **天纪玄学**：娱乐加成

### 4. 输出与可视化结果 (Output & Visualization Layer)
**当前状态**：⚠️ 只有海报生成

**现有输出**：
- JSON 报告
- 海报 Prompt（Midjourney/DALL-E）
- 玩法卡片

**建议改进**：
```python
# 新增：Web Dashboard
class WebDashboard:
    """
    实时可视化看板：
    1. 胜平负概率柱状图
    2. 最可能比分（例如 2:1, 1:0, 微赛）
    3. 总进球期望（大/小/2.5球概率）
    4. 风险预警（核心球员缺席、变化、裁判严格）
    """
    
    def render_match_card(self, match_id, prediction):
        """
        渲染比赛卡片：
        - 队徽、球队名
        - 预测比分
        - 胜平负概率条形图
        - 双轨背离指示器
        - 信心指数
        - 看点分析
        """
        pass
    
    def render_dashboard(self, date):
        """
        渲染当日看板：
        - 所有比赛的卡片
        - 模拟账户余额
        - 历史预测准确率
        """
        pass
```

---

## 四、改进优先级建议

### P0 (立即实施)
1. **特征注册表**：建立 `FeatureRegistry` 统一管理特征
2. **数据采集调度器**：定时自动化采集赔率、新闻、伤停
3. **Web Dashboard 原型**：简单的 HTML 看板展示当日预测

### P1 (短期实施)
4. **多模型集成**：引入泊松分布模型和蒙特卡洛模拟
5. **模拟账户系统**：追踪预测准确率，建立信誉评分
6. **移动端适配**：响应式设计，支持手机查看

### P2 (中期实施)
7. **反思与自调整**：赛后自动反思，微调模型权重
8. **ReAct 规划循环**：Agent 自主拆解任务，自动调用工具
9. **实时推送**：Telegram/微信机器人推送预测结果

---

## 五、具体实施路线图

### 第一阶段：特征工程层重构 (1-2周)
```bash
knowledge-base/
  agent/
    FEATURE_REGISTRY.json  # 新增：特征注册表
    FEATURE_CATALOG.md     # 新增：特征说明文档
  
scripts/
  feature_engineering/
    __init__.py
    feature_registry.py     # 特征注册表
    feature_calculator.py   # 特征计算器
    feature_pipeline.py     # 特征工程管道
```

### 第二阶段：多模型集成 (2-3周)
```bash
scripts/
  prediction_models/
    __init__.py
    poisson_model.py        # 泊松分布模型
    elo_model.py            # Elo 评分模型
    monte_carlo_model.py    # 蒙特卡洛模拟
    dual_track_model.py     # 现有的双轨背离模型
    tianji_model.py         # 玄学模型
    model_ensemble.py       # 模型集成器
```

### 第三阶段：Web Dashboard (2-3周)
```bash
web/
  static/
    css/
    js/
  templates/
    dashboard.html          # 看板页面
    match_card.html         # 比赛卡片
  app.py                    # Flask 应用
  api.py                    # RESTful API
```

### 第四阶段：自动化调度 (1周)
```bash
scripts/
  scheduler/
    __init__.py
    daily_pipeline.py       # 每日自动化流程
    cron_config.yaml        # 定时任务配置
```

---

## 六、架构演进示意图

```
当前架构 (5模块) → 改进架构 (4层)

┌─────────────────────────────────────────────────────────┐
│                   数据采集层 (自动化调度)                    │
│  定时任务: 赔率/新闻/伤停/天气 → daily-evidence             │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│                   特征工程层 (统一注册表)                    │
│  FeatureRegistry + FeatureCalculator → features.json    │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│                   预测模型层 (多模型集成)                    │
│  泊松/Elo/蒙特卡洛/双轨背离/天纪 → ensemble prediction     │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│                   输出可视化层 (Web + 海报)                  │
│  Web Dashboard + 玩法卡片 + Poster Prompt                │
└─────────────────────────────────────────────────────────┘
```

---

## 七、关键决策点

### Q1: 要不要保留玄学层？
**建议**：保留但降低权重到 5%，作为娱乐亮点。重点放在数据驱动模型上。

### Q2: 要不要引入 LLM 自动下注？
**建议**：可以做**模拟账户**，但绝对不做真实下注建议。强化免责声明。

### Q3: 要不要做移动端 App？
**建议**：先做 Web 响应式设计，再考虑微信小程序/Telegram Bot。

### Q4: 数据存储用 JSON 还是 SQLite？
**建议**：**两者都用**：
- JSON 作为 audit trail（不可变记录）
- SQLite 作为查询层（快速检索）
- 如果冲突，以 JSON 为准

---

## 八、成本与收益分析

### 开发成本
- 特征工程重构：1-2周
- 多模型集成：2-3周
- Web Dashboard：2-3周
- 自动化调度：1周
- **总计：6-9周**

### 预期收益
- ✅ 预测准确率提升：单一模型 → 集成模型，预计提升 5-10%
- ✅ 用户体验改善：JSON → Web 看板，降低使用门槛
- ✅ 运营效率提升：手动触发 → 自动化调度，节省 80% 时间
- ✅ 社交传播力：海报 + 玩法卡片 + Web 分享链接

---

## 九、Next Steps

### 立即行动
1. **创建 Feature Registry**：定义所有特征及其权重
2. **实现泊松分布模型**：作为多模型集成的第一步
3. **搭建 Flask 原型**：最简单的 Web 看板，展示今日预测

### 本周完成
4. **重构特征工程层**：所有特征计算从脚本中抽离到统一管道
5. **添加自动化调度**：使用 cron 或 APScheduler 定时采集数据

### 两周内完成
6. **多模型集成框架**：集成至少 3 个模型（泊松/Elo/双轨背离）
7. **Web Dashboard MVP**：可以查看当日预测和历史记录

---

## 总结

你的项目已经有了很好的基础（双轨背离模型、Agent 接口、知识库结构），现在需要：

1. **向上游扩展**：自动化数据采集
2. **向中间补强**：特征工程 + 多模型集成
3. **向下游优化**：Web 可视化 + 移动端适配

参考 worldcut-2026 的实时看板和抖音视频的四层架构，重点是：
- **数据自动化**：不再手动触发
- **特征统一化**：建立注册表
- **模型多样化**：集成多个算法
- **输出可视化**：Web Dashboard

**最关键的改进**：建立 **FeatureRegistry** 和 **ModelEnsemble**，这是从"脚本工具"升级为"智能 Agent"的核心。
