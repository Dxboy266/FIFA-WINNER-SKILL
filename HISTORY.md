# FIFA-WINNER-SKILL 历史预测与复盘记录

本文档收录本届世界杯所有历史比赛的预测报告、实际赛果及命中复盘数据。

## 预测命中率概览

- **已评估比赛数**: 0 场
- **胜平负命中率**: 0.00%
- **比分直落命中率**: 0.00%
- **总进球数大小命中率**: 0.00%

## 历史对阵日志

| 比赛ID | 阶段 | 开球时间 (北京时间) | 比赛对阵 | 预测比分 | 实际比分 | 状态 |
|---|---|---|---|---|---|---|
| `2026-GA-02` | Group | 2026-06-12 10:00 | South Korea vs Czechia | 1-0 | 2-1 | 已复盘 |
| `2026-GA-01` | Group | 2026-06-12 03:00 | Mexico vs South Africa | 2-1 | 2-0 | 已复盘 |

## Model Self-Reflection Journal / 模型自反思日志

# Model Self-Reflection & Adjustment Journal

记录模型预测失误或偏差的细节，以及背后的调参反馈。

### Match 2026-GA-02: South Korea vs Czechia
- **Prediction**: 1-0 (home_win)
- **Actual**: 2-1 (home_win)
- **Status**: Scoreline Discrepancy
- **Feature Analysis**:
  - Ranking Strength: Home 55.5 vs Away 43.0
  - Squad Depth: Home 85.0 vs Away 92.6
  - Historical Proxy: Home 55.5 vs Away 43.0
  - Rest & Travel: Home 85.0 vs Away 85.0
  - Evidence Completeness: 1.0
  - Divination Overlay: Home Mod 0.0 vs Away Mod 0.5 (Hexagram: 戌时 (19:00-20:59))
- **Reflection**: The model correctly predicted the home_win direction, but the scoreline differed by 2 goals. The Home team scored 1 more goal(s) than predicted. The model might have under-valued their attacking depth or clean sheet probability. The Away team scored 1 more goal(s) than predicted. The model might have under-valued their transition offense or rest status.


