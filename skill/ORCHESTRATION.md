# Orchestration Guide

宿主 Agent 调用本 Skill 时的最佳工作流建议。宿主 Agent 可以自主决定调用顺序，以下是最常见的推荐模式。

## 1. 初始化新届次

```
触发条件：用户提到新的世界杯届次，或 match-ledger.json 不存在

推荐步骤：
1. python skill/scripts/worldcup_edition_init.py init --edition <edition> --root .
2. python skill/scripts/worldcup_source_readiness_auditor.py write --edition <edition> --root .
3. python skill/scripts/worldcup_prediction_evidence_planner.py write --edition <edition> --root .

产物：
- wiki/<edition>/data/match-ledger.json
- wiki/<edition>/raw/source-registry.json
- wiki/<edition>/data/source-readiness.json
- wiki/<edition>/data/prediction-evidence-plan.json
```

## 2. 每日预测

```
触发条件：用户要求预测某天的比赛，或后续任意淘汰赛日

推荐步骤：
1. 编译/刷新证据：
   python skill/scripts/compile_prediction_evidence.py
   python skill/scripts/worldcup_source_readiness_auditor.py write --edition <edition> --root .
   python skill/scripts/worldcup_prediction_evidence_planner.py write --edition <edition> --root .

2. 采集每日证据（如果缺失）：
   python skill/scripts/daily_evidence_input.py init --edition <edition> --date <date> --root .
   python skill/scripts/worldcup_live_fetcher.py fetch-odds --edition <edition> --date <date> --root .
   python skill/scripts/worldcup_live_fetcher.py fetch-news --edition <edition> --date <date> --root .

3. 生成预测（先 outcome/score，再 confidence）：
   python skill/scripts/daily_prediction_runner.py run --edition <edition> --date <date> --root .

4. 更新看板：
   python skill/scripts/prediction_visual_dashboard.py write --edition <edition> --root .

注意：
- 小组赛到决赛同一管线；不因“关键场”改标识抬高信心
- 证据不完整时仍然可以预测，但要在回答中声明 evidence gaps
- 预测一旦生成就锁定，开球后不得修改
- 必须附加安全声明：娱乐预测，非投注建议
```

## 3. 单场/指定预测

```
触发条件：用户要求预测特定比赛

推荐步骤：
0. 可选先编译证据：
   python skill/scripts/compile_prediction_evidence.py
   python skill/scripts/worldcup_prediction_evidence_planner.py write --edition <edition> --root .

1. 按球队预测：
   python skill/scripts/prediction_scoring_model.py predict --edition <edition> --teams "Team A,Team B" --root .
   
2. 按 match_id 预测：
   python skill/scripts/prediction_scoring_model.py predict --edition <edition> --match-id <match_id> --root .
   
3. 按小组/阶段预测：
   python skill/scripts/octopus_paul_agent.py predict --edition <edition> --group <group> --root .
   python skill/scripts/octopus_paul_agent.py predict --edition <edition> --phase <phase> --root .
   python skill/scripts/octopus_paul_agent.py predict --edition <edition> --all --root .
```

## 4. 生成报告/海报

```
触发条件：用户明确要求海报、图片或分享卡片

推荐步骤：
1. 生成报告 prompt：
   python skill/scripts/prediction_report_prompt_builder.py build --edition <edition> --date <date> --report-path <report.json> --match-id <match_id> --root .
   
2. 生成海报 prompt：
   python skill/scripts/poster_prompt_builder.py build --edition <edition> --date <date> --style showdown --match-id <match_id> --root .
   
3. 生成图片（如果后端可用）：
   python skill/scripts/poster_generator.py generate --manifest <manifest.json> --backend image2 --root .

注意：
- 仅在用户明确要求时才生成海报
- 后端缺失时返回 blocked 结果
```

## 5. 赛后复盘

```
触发条件：比赛结束后用户要求复盘

前提：最终比分已录入 match-ledger.json

推荐步骤：
1. 评估预测：
   python skill/scripts/prediction_evaluator.py write --edition <edition> --date <date> --root .
   
2. 更新汇总看板：
   python skill/scripts/prediction_evaluation_dashboard.py write --edition <edition> --root .
   
3. 反思调参（可选）：
   python skill/scripts/octopus_reflection_tuning.py tune --edition <edition> --root .

注意：
- 开球后不得修改赛前预测
- 评估分别报告赛果方向、精确比分、总进球数的命中情况
```

## 6. 更新看板/可视化

```
触发条件：预测数据变更后需要刷新展示

推荐步骤：
1. python skill/scripts/prediction_visual_dashboard.py write --edition <edition> --root .
2. 读取产物：wiki/<edition>/wiki/dashboard/index.html

看板合并规则：
- user_local > octopus_default > none
- 读取每张卡的 prediction_origin 判断数据来源
```

## 摘要格式

完成预测任务后，宿主 Agent 应按以下格式回答：

```
Status: created | locked_existing_report | blocked | no_matches_found
Report: <path>
Matches: <count>
Main pick: <home/draw/away plus score>
Confidence: <low/medium/high>
Evidence gaps: <list or none>
Key layers: <2-3 analysis layer summaries>
Safety: 娱乐预测，非投注建议；不得作为投注、购彩或资金决策依据。
```

不要粘贴完整 JSON 除非用户明确要求。
