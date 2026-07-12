# Guardrails

宿主 Agent 调用本 Skill 时必须遵守的安全和完整性边界。

## 必须附加的声明

每一条预测类回答必须包含：

```
娱乐预测，非投注建议；不得作为投注、购彩或资金决策依据。
```

允许的表述：娱乐预测、基于证据的不确定性、情景分析、看点与风险标记、赛后复盘。

禁止的表述：投注建议、仓位/资金管理、博彩推荐、"稳赢"、"稳胆"、"必赚"、"梭哈"、"lock bet"。

## 证据护栏

做出预测摘要前，宿主 Agent 应检查：

1. `wiki/public/<edition>/match-ledger.json`（或 edition data root 下的 match-ledger）是否存在
2. `wiki/public/<edition>/prediction-evidence-plan.json` 是否存在；缺失/过期时先跑 `compile_prediction_evidence.py` + planner `write`
3. 日期相关任务时，`wiki/public/<edition>/daily-evidence/<date>.json` 是否存在
4. 目标比赛是否尚未开始

如果证据状态为 `partial` 或 `blocked`，必须在回答中保持可见。不得因为叙事听起来合理就提升置信度。

## 信心护栏

- 必须先算出赛果/比分，再调用评分器给信心；禁止只改 UI/展示策略把场次标成 high。
- `edge_tier=coinflip` 不得 high。
- 缺失赔率记为 market `none`，不是 mock 伪造盘口；不要因此把 evidence_quality 打成 `unusable`。
- `confidence-display-policy.json` 的 force-high 列表应为空；信心以模型 JSON 为准。

## 存储护栏

- `wiki/<edition>/` 下的 JSON 和 Markdown 是规范产物（canonical artifacts）
- SQLite 是查询/索引层，从这些产物派生
- 如果 JSON 和 SQLite 不一致：以锁定的 JSON 报告为准，报告不一致，重建索引

## 锁定报告护栏

赛前预测报告一旦生成就锁定。开球后：

- 不得重新生成预测来迎合比赛结果
- 不得静默编辑选择、比分、置信度或分析层
- 使用赛后评估工具来复盘

## 来源与版权护栏

- 赛程、阵容、排名、赛果优先使用 T0 官方源
- 存储 URL、元数据、结构化值和短摘要
- 不存储大量受版权保护的文本
- 遵守来源条款、速率限制和 API key 边界

## 海报护栏

仅在用户明确要求海报、图片、分享卡片或视觉素材时才生成海报 prompt 或图片。

如果图片后端缺失，诚实返回 blocked 结果，不得声称图片已生成。
