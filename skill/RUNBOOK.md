# Runbook

宿主 Agent（Codex、Claude Code、Cursor Agent、CI 等）操作本 Skill 的手册。

## 首次阅读顺序

1. `AGENT_README.md`
2. `skill/AGENT_CARD.json`
3. `skill/TOOL_CATALOG.json`
4. `skill/ORCHESTRATION.md`
5. `skill/GUARDRAILS.md`
6. `skill/schema/daily-prediction-report.schema.json`

## 预检

```bash
python -m pytest -q
python skill/scripts/worldcup_github_readiness_auditor.py write --edition 2026 --root .
```

如果测试失败，先报告失败再做预测声明。

## 预测流程

对**任意未开球比赛**（含淘汰赛 SF/F）走同一套：

```bash
# 证据编译 + 计划刷新
python skill/scripts/compile_prediction_evidence.py
python skill/scripts/worldcup_prediction_evidence_planner.py write --edition 2026 --root .

# 当日证据 + 可选 live 赔率/新闻
python skill/scripts/daily_evidence_input.py init --edition 2026 --date YYYY-MM-DD --root .
python skill/scripts/worldcup_live_fetcher.py fetch-odds --edition 2026 --date YYYY-MM-DD --root .
python skill/scripts/worldcup_live_fetcher.py fetch-news --edition 2026 --date YYYY-MM-DD --root .

# 预测（先算赛果/比分，再评信心）
python skill/scripts/daily_prediction_runner.py run --edition 2026 --date YYYY-MM-DD --root .
# 或单场：
python skill/scripts/prediction_scoring_model.py predict --edition 2026 --match-id 2026-SF-01 --root .
# 或阶段：
python skill/scripts/octopus_paul_agent.py predict --edition 2026 --phase semi_final --root .
```

规则：

- 信心由 `_score_prediction_confidence` 实算；禁止 force-high / 改前端徽章。
- `coinflip` 边差不能 high；证据齐全也不例外。
- 已锁定正式预测优先复用；开球后禁止改写。

使用 `prediction_scoring_model.py predict` 或 `octopus_paul_agent.py predict` 进行按比赛/球队/小组/阶段/全量的预测。

## 用户数据覆盖流程

1. 生成或刷新用户本地预测：

```bash
python skill/scripts/daily_prediction_runner.py run --edition 2026 --date 2026-06-11 --root .
```

2. 重建看板合并视图：

```bash
python skill/scripts/prediction_visual_dashboard.py write --edition 2026 --root .
```

3. 读取看板 JSON：`wiki/person/2026/reports/dashboard/prediction-dashboard.json`

4. 按 `prediction_origin` 解释每张卡：
   - `user_local`：用户生成的预测，覆盖打包的默认预测
   - `octopus_default`：打包的 AI 章鱼哥默认预测（用户尚未生成该比赛）
   - `none`：仅公共事实卡，不声称存在预测

## 正式预测冻结规则

正式预测是 `canonical`，用于日报、看板、复盘和赛后评估。

- 一场比赛只保留一份正式预测
- 已存在正式预测时，后续日常运行优先复用，不重新生成
- 一旦开球，状态进入 `kickoff_locked`
- 一旦录入最终赛果，状态进入 `result_locked`
- 历史回放、模型对比、调参实验只能写入 `experiment/backtest`，不能回写正式历史

这条规则高于一切：**已经出的正式预测结果不要动**。

## 海报流程

仅在用户明确要求时：

```bash
python skill/scripts/poster_prompt_builder.py build --edition 2026 --date 2026-06-11 --style showdown --match-id 2026-GA-01 --root .
python skill/scripts/poster_generator.py generate --manifest <poster-manifest.json> --backend image2 --root .
```

如果后端缺失，blocked 结果是正确的。

## 复盘流程

在最终比分录入后：

```bash
python skill/scripts/prediction_evaluator.py write --edition 2026 --date 2026-06-11 --root .
python skill/scripts/prediction_evaluation_dashboard.py write --edition 2026 --root .
```

使用评估输出进行校准和反思。开球后不得重写赛前选择。
