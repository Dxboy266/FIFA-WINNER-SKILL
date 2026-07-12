# FIFA 2026 世界杯预测 Dashboard V2

基于 [finesse-skill](https://github.com/mouse-lin/finesse-skill) 设计系统重构的高端数据可视化界面。

## ✨ 核心特性

### 🎨 设计原则
- **Premium Substrate**: OKLCH 色彩系统，深色主题优先
- **纹理层**: SVG 噪点纹理，提升视觉质感
- **数据密度优化**: 信息层次清晰，充分利用所有预测数据
- **响应式布局**: 适配桌面/平板/移动端

### 📊 功能模块

#### 1. 总览看板 (Overview)
- **Hero Stats**: 5个关键指标卡片
  - 预测总场次
  - 预测准确率（带趋势）
  - 完全命中数
  - 胜负命中数
  - 今日比赛
- **赛程进度追踪**: 各阶段完成度可视化
- **最近预测列表**: 紧凑型卡片展示
- **准确率趋势图**: ECharts 折线图

#### 2. 比赛预测 (Predictions)
- **高级筛选**:
  - 按阶段筛选（小组赛/淘汰赛/决赛）
  - 按日期筛选
  - 一键清除筛选
- **预测卡片网格**: 完整展示所有预测数据
- **数据维度**:
  - 基本面打分（ranking, squad_depth, historical_proxy）
  - 场地适应性（travel_km, temperature_delta, altitude_delta）
  - 天纪卦象与星曜
  - 多层分析堆栈（9层）

#### 3. 数据分析 (Analytics)
- **基本面实力分布**: 柱状图
- **预测信心度分析**: 饼图
- **准确率热力图**: 按阶段和结果类型

#### 4. 准确率追踪 (Accuracy)
- **总体准确率仪表盘**:
  - 比分准确率
  - 胜负准确率
  - 总体准确率
- **详细数据表**: 按阶段统计命中情况

#### 5. 天纪占卜 (Tianji)
- **今日卦象**: 八卦展示
- **星曜分布**: 雷达图
- **气运走势**: 时间序列图

#### 6. 系统设置 (Settings)
- 数据自动刷新开关
- 天纪占卜显示开关
- 深色模式（当前固定）

## 🏗️ 技术架构

### 文件结构
```
dashboard-v2/
├── index.html      # 主页面，完整的 UI 结构
├── styles.css      # Finesse 风格样式系统
├── app.js          # 数据加载与交互逻辑
└── README.md       # 本文档
```

### 依赖
- **ECharts 5.4.3**: 图表可视化
- **Google Fonts**: Inter / JetBrains Mono / Noto Sans SC
- **无其他外部依赖**: 纯原生 JavaScript

### 数据源
- `../reports/daily-predictions/{date}.json`: 每日预测报告
- `../worldcup.db`: SQLite 数据库（未来集成）

## 🚀 快速开始

### 本地运行
1. 确保数据文件存在于正确路径
2. 使用 HTTP 服务器启动（不能直接打开 HTML）:
   ```bash
   # Python
   python -m http.server 8000
   
   # Node.js
   npx http-server
   ```
3. 访问 `http://localhost:8000/wiki/public/2026/dashboard-v2/`

### 数据加载逻辑
- 自动加载最近 30 天的预测数据
- 实时计算统计指标
- 支持手动刷新

## 📐 设计系统

### 色彩 Token (OKLCH-based)
```css
--bg: #04060D           /* 近黑背景，微带蓝色调 */
--surface: #0F0F16      /* 表面层 */
--ink: #EAEDF8          /* 主文本，非纯白 */
--muted: #7A7A90        /* 次要文本 */
--accent: #6C63FF       /* 主题色（紫色）*/
--cyan: #22D3EE         /* 强调色（青色）*/
--green: #34D399        /* 成功色（绿色）*/
--amber: #FBBF24        /* 警告色（琥珀色）*/
--red: #F87171          /* 错误色（红色）*/
```

### 间距系统 (4px 基准)
- `--space-1`: 0.25rem (4px)
- `--space-2`: 0.5rem (8px)
- `--space-3`: 0.75rem (12px)
- `--space-4`: 1rem (16px)
- `--space-6`: 1.5rem (24px)
- `--space-8`: 2rem (32px)
- `--space-12`: 3rem (48px)

### 圆角系统
- `--radius-sm`: 8px (小组件)
- `--radius-md`: 12px (按钮/输入框)
- `--radius-lg`: 16px (卡片/筛选器)
- `--radius-xl`: 20px (大型卡片/容器)

### 阴影系统
- `--shadow-sm`: 轻微阴影
- `--shadow-md`: 中等阴影
- `--shadow-lg`: 大阴影（悬停状态）
- `--shadow-xl`: 超大阴影（模态框）

### 字体
- **UI 字体**: Inter / Noto Sans SC
- **等宽字体**: JetBrains Mono（数字/代码）

## 🎯 数据利用

### 完整利用的数据维度
✅ **基本面分析**
- ranking_strength (排名实力)
- squad_depth (阵容深度)
- historical_proxy (历史战绩)
- rest_travel (休息与旅行)
- evidence_completeness (证据完整度)

✅ **场地适应性**
- travel_km (旅行距离)
- temperature_delta (温度差异)
- altitude_delta (海拔差异)
- adaptation_risk (适应风险)

✅ **天纪占卜**
- hexagram (卦象)
- stars (星曜)
- fortune_modifier (气运修正)
- shichen (时辰)

✅ **多层分析**
- 9层分析堆栈
- 证据完整度层
- 基本面强弱层
- 阵容对位层
- 临场变量层
- 场地适应层
- 市场背离层
- 比赛剧本层
- 天纪比分分布
- 反方审稿层

✅ **预测结果**
- 比分预测
- 胜负预测
- 信心指数
- 比分分布概率
- 零封概率

## 📱 响应式设计

### 断点
- **Desktop**: > 1024px (侧边栏 + 主内容)
- **Tablet**: 640px - 1024px (无侧边栏，单列布局)
- **Mobile**: < 640px (单列，紧凑间距)

### 适配策略
- 导航：桌面侧边栏 → 移动端隐藏（未来添加抽屉导航）
- 卡片网格：自适应列数（`auto-fit` / `minmax`）
- 统计卡片：5列 → 2列 → 1列
- 图表：自适应容器宽度

## 🔄 未来改进

### 短期 (v2.1)
- [ ] 移动端抽屉导航
- [ ] 比赛详情弹窗
- [ ] 实时数据自动刷新
- [ ] 数据加载骨架屏
- [ ] 错误状态优化

### 中期 (v2.2)
- [ ] 队伍对比雷达图
- [ ] 历史交锋记录
- [ ] 预测回测分析
- [ ] 导出报告功能
- [ ] 暗色/亮色主题切换

### 长期 (v3.0)
- [ ] 实时比赛追踪
- [ ] WebSocket 实时推送
- [ ] 用户自定义看板
- [ ] 多语言支持
- [ ] PWA 离线支持

## 🐛 已知问题

1. **数据加载**: 需要确保 JSON 文件路径正确
2. **图表渲染**: 首次加载时可能需要等待 ECharts 加载
3. **移动端**: 侧边栏暂时完全隐藏，未来添加抽屉菜单

## 📄 License

本项目基于 MIT 协议开源。

## 🙏 致谢

- 设计系统灵感: [finesse-skill](https://github.com/mouse-lin/finesse-skill)
- 图表库: [Apache ECharts](https://echarts.apache.org/)
- 字体: Google Fonts

---

**构建时间**: 2026-07-12  
**版本**: v2.0.0  
**作者**: FIFA Winner Skill Team
