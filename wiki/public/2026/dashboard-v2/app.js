// FIFA 2026 Dashboard V2 - Main Application
// Data-driven, finesse-styled dashboard

const API_BASE = '../';
const DB_PATH = '../worldcup.db';

// State management
const state = {
  currentView: 'overview',
  matches: [],
  predictions: [],
  stats: {},
  filters: {
    phase: 'all',
    date: null,
    status: 'all'
  }
};

// Initialize app
document.addEventListener('DOMContentLoaded', async () => {
  console.log('🚀 FIFA 2026 Dashboard V2 initializing...');

  // Setup navigation
  setupNavigation();

  // Setup event listeners
  setupEventListeners();

  // Load initial data
  await loadData();

  // Render current view
  renderView(state.currentView);

  console.log('✅ Dashboard ready');
});

// Navigation setup
function setupNavigation() {
  const navItems = document.querySelectorAll('.nav-item');

  navItems.forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      const view = item.dataset.view;

      // Update active state
      navItems.forEach(n => n.classList.remove('active'));
      item.classList.add('active');

      // Switch view
      switchView(view);
    });
  });
}

// Event listeners
function setupEventListeners() {
  // Refresh button
  const refreshBtn = document.getElementById('refreshBtn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      refreshBtn.classList.add('rotating');
      await loadData();
      renderView(state.currentView);
      setTimeout(() => refreshBtn.classList.remove('rotating'), 500);
    });
  }

  // Filter chips
  const filterChips = document.querySelectorAll('.filter-chip');
  filterChips.forEach(chip => {
    chip.addEventListener('click', () => {
      const filter = chip.dataset.filter;
      filterChips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      filterMatches(filter);
    });
  });

  // Phase filter
  const phaseFilter = document.getElementById('phaseFilter');
  if (phaseFilter) {
    phaseFilter.addEventListener('change', (e) => {
      state.filters.phase = e.target.value;
      renderPredictionsView();
    });
  }

  // Date filter
  const dateFilter = document.getElementById('dateFilter');
  if (dateFilter) {
    dateFilter.addEventListener('change', (e) => {
      state.filters.date = e.target.value;
      renderPredictionsView();
    });
  }

  // Clear filters
  const clearFilters = document.getElementById('clearFilters');
  if (clearFilters) {
    clearFilters.addEventListener('click', () => {
      state.filters = { phase: 'all', date: null, status: 'all' };
      if (phaseFilter) phaseFilter.value = 'all';
      if (dateFilter) dateFilter.value = '';
      renderPredictionsView();
    });
  }
}

// Load data from files
async function loadData() {
  try {
    console.log('📊 Loading data...');

    // Load recent predictions (latest 7 days)
    const predictions = await loadRecentPredictions();
    state.predictions = predictions;

    // Calculate stats
    state.stats = calculateStats(predictions);

    console.log(`✅ Loaded ${predictions.length} predictions`);

  } catch (error) {
    console.error('❌ Error loading data:', error);
  }
}

// Load recent predictions from JSON files
async function loadRecentPredictions() {
  const predictions = [];
  const today = new Date();

  // Load last 30 days of predictions
  for (let i = 0; i < 30; i++) {
    const date = new Date(today);
    date.setDate(date.getDate() - i);
    const dateStr = date.toISOString().split('T')[0];

    try {
      const response = await fetch(`../reports/daily-predictions/${dateStr}.json`);
      if (response.ok) {
        const data = await response.json();
        if (data.predictions && data.predictions.length > 0) {
          predictions.push(...data.predictions.map(p => ({
            ...p,
            report_date: dateStr
          })));
        }
      }
    } catch (error) {
      // File doesn't exist or error loading
    }
  }

  return predictions;
}

// Calculate statistics
function calculateStats(predictions) {
  const stats = {
    total: predictions.length,
    withResults: 0,
    perfectHits: 0,
    resultHits: 0,
    misses: 0,
    accuracyRate: 0,
    scoreAccuracy: 0,
    resultAccuracy: 0,
    byPhase: {},
    today: 0
  };

  const today = new Date().toISOString().split('T')[0];

  predictions.forEach(pred => {
    // Count today's matches
    const kickoffDate = pred.kickoff_at?.split('T')[0];
    if (kickoffDate === today) {
      stats.today++;
    }

    // Check if has actual result (from match ledger)
    const hasResult = pred.final_score?.home !== undefined;
    if (!hasResult) return;

    stats.withResults++;

    const predictedScore = pred.prediction?.score || {};
    const actualScore = pred.final_score || {};

    // Check perfect hit (score match)
    if (predictedScore.home === actualScore.home &&
        predictedScore.away === actualScore.away) {
      stats.perfectHits++;
      stats.resultHits++;
    }
    // Check result hit (win/draw/loss correct)
    else {
      const predictedResult = getResult(predictedScore.home, predictedScore.away);
      const actualResult = getResult(actualScore.home, actualScore.away);

      if (predictedResult === actualResult) {
        stats.resultHits++;
      } else {
        stats.misses++;
      }
    }

    // By phase stats
    const phase = pred.phase || 'unknown';
    if (!stats.byPhase[phase]) {
      stats.byPhase[phase] = { total: 0, perfect: 0, result: 0, miss: 0 };
    }
    stats.byPhase[phase].total++;
  });

  // Calculate accuracy rates
  if (stats.withResults > 0) {
    stats.scoreAccuracy = (stats.perfectHits / stats.withResults * 100).toFixed(1);
    stats.resultAccuracy = (stats.resultHits / stats.withResults * 100).toFixed(1);
    stats.accuracyRate = stats.resultAccuracy; // Overall = result accuracy
  }

  return stats;
}

// Helper: determine match result
function getResult(home, away) {
  if (home > away) return 'home_win';
  if (away > home) return 'away_win';
  return 'draw';
}

// Switch view
function switchView(viewName) {
  state.currentView = viewName;

  // Update view panels
  document.querySelectorAll('.view-panel').forEach(panel => {
    panel.classList.remove('active');
  });
  document.getElementById(`view-${viewName}`).classList.add('active');

  // Update page title
  const titles = {
    overview: '总览看板',
    predictions: '比赛预测',
    analytics: '数据分析',
    accuracy: '准确率追踪',
    tianji: '天纪占卜',
    settings: '设置'
  };
  document.querySelector('.page-title').textContent = titles[viewName];

  // Render view content
  renderView(viewName);
}

// Render specific view
function renderView(viewName) {
  switch (viewName) {
    case 'overview':
      renderOverviewView();
      break;
    case 'predictions':
      renderPredictionsView();
      break;
    case 'analytics':
      renderAnalyticsView();
      break;
    case 'accuracy':
      renderAccuracyView();
      break;
    case 'tianji':
      renderTianjiView();
      break;
  }
}

// Render Overview view
function renderOverviewView() {
  // Update hero stats
  document.getElementById('totalPredictions').textContent = state.stats.total || '--';
  document.getElementById('accuracyRate').textContent =
    state.stats.accuracyRate ? `${state.stats.accuracyRate}%` : '--';
  document.getElementById('perfectHits').textContent = state.stats.perfectHits || '--';
  document.getElementById('resultHits').textContent = state.stats.resultHits || '--';
  document.getElementById('todayMatches').textContent = state.stats.today || '--';

  // Render phase tracker
  renderPhaseTracker();

  // Render recent matches
  renderRecentMatches();

  // Render accuracy chart
  renderAccuracyChart();
}

// Render phase tracker
function renderPhaseTracker() {
  const container = document.getElementById('phaseTracker');
  if (!container) return;

  const phases = [
    { name: '小组赛', key: 'group', total: 48, completed: 48 },
    { name: '32强', key: 'round_of_32', total: 16, completed: 16 },
    { name: '16强', key: 'round_of_16', total: 8, completed: 8 },
    { name: '8强', key: 'quarter_final', total: 4, completed: 4 },
    { name: '半决赛', key: 'semi_final', total: 2, completed: 1 },
    { name: '决赛', key: 'final', total: 1, completed: 0 }
  ];

  container.innerHTML = phases.map(phase => {
    const progress = (phase.completed / phase.total * 100).toFixed(0);
    return `
      <div style="margin-bottom: 1rem;">
        <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem; font-size: 14px;">
          <span style="font-weight: 600;">${phase.name}</span>
          <span style="color: var(--muted); font-family: var(--font-mono);">${phase.completed}/${phase.total}</span>
        </div>
        <div style="height: 8px; background: rgba(255,255,255,0.05); border-radius: 999px; overflow: hidden;">
          <div style="height: 100%; width: ${progress}%; background: linear-gradient(90deg, var(--cyan), var(--accent)); transition: width 0.6s;"></div>
        </div>
      </div>
    `;
  }).join('');
}

// Render recent matches
function renderRecentMatches() {
  const container = document.getElementById('recentMatches');
  if (!container) return;

  const recent = state.predictions.slice(0, 5);

  if (recent.length === 0) {
    container.innerHTML = '<p style="color: var(--muted); text-align: center; padding: 2rem;">暂无预测数据</p>';
    return;
  }

  container.innerHTML = recent.map(match => renderMatchCard(match, 'compact')).join('');
}

// Render match card
function renderMatchCard(match, style = 'full') {
  const homeTeam = match.home_team?.name || match.home_team_id;
  const awayTeam = match.away_team?.name || match.away_team_id;
  const predictedScore = match.prediction?.score || {};
  const actualScore = match.final_score || {};
  const hasResult = actualScore.home !== undefined;

  const kickoffDate = new Date(match.kickoff_at).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
  });

  if (style === 'compact') {
    return `
      <div style="background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 1rem; display: flex; align-items: center; justify-content: space-between; gap: 1rem;">
        <div style="flex: 1;">
          <div style="font-size: 12px; color: var(--muted); margin-bottom: 0.5rem;">${match.match_id} · ${kickoffDate}</div>
          <div style="font-weight: 600;">${homeTeam} vs ${awayTeam}</div>
        </div>
        <div style="text-align: right;">
          <div style="font-family: var(--font-mono); font-size: 20px; font-weight: 700;">${predictedScore.home}-${predictedScore.away}</div>
          ${hasResult ? `<div style="font-size: 12px; color: var(--cyan);">实际: ${actualScore.home}-${actualScore.away}</div>` : ''}
        </div>
      </div>
    `;
  }

  return `<div>Full card rendering...</div>`;
}

// Render Predictions view
function renderPredictionsView() {
  const container = document.getElementById('predictionsGrid');
  if (!container) return;

  let filtered = state.predictions;

  // Apply filters
  if (state.filters.phase !== 'all') {
    filtered = filtered.filter(p => p.phase === state.filters.phase);
  }
  if (state.filters.date) {
    filtered = filtered.filter(p => p.kickoff_at?.startsWith(state.filters.date));
  }

  if (filtered.length === 0) {
    container.innerHTML = '<p style="grid-column: 1/-1; color: var(--muted); text-align: center; padding: 3rem;">暂无符合条件的预测</p>';
    return;
  }

  container.innerHTML = filtered.map(match => renderMatchCard(match, 'full')).join('');
}

// Render Analytics view
function renderAnalyticsView() {
  renderStrengthChart();
  renderConfidenceChart();
  renderHeatmapChart();
}

// Render charts using ECharts
function renderStrengthChart() {
  const chartDom = document.getElementById('strengthChart');
  if (!chartDom) return;

  const chart = echarts.init(chartDom);
  const option = {
    backgroundColor: 'transparent',
    textStyle: { color: '#EAEDF8' },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: ['50-60', '60-70', '70-80', '80-90', '90-100'] },
    yAxis: { type: 'value' },
    series: [{
      name: '队伍数量',
      type: 'bar',
      data: [5, 12, 18, 8, 3],
      itemStyle: { color: '#6C63FF' }
    }]
  };
  chart.setOption(option);
}

function renderConfidenceChart() {
  const chartDom = document.getElementById('confidenceChart');
  if (!chartDom) return;

  const chart = echarts.init(chartDom);
  const option = {
    backgroundColor: 'transparent',
    textStyle: { color: '#EAEDF8' },
    tooltip: { trigger: 'item' },
    series: [{
      name: '信心度',
      type: 'pie',
      radius: ['40%', '70%'],
      data: [
        { value: 45, name: '高信心' },
        { value: 38, name: '中等信心' },
        { value: 17, name: '低信心' }
      ],
      itemStyle: {
        color: function(params) {
          const colors = ['#34D399', '#22D3EE', '#FBBF24'];
          return colors[params.dataIndex];
        }
      }
    }]
  };
  chart.setOption(option);
}

function renderHeatmapChart() {
  const chartDom = document.getElementById('heatmapChart');
  if (!chartDom) return;

  const chart = echarts.init(chartDom);
  const option = {
    backgroundColor: 'transparent',
    textStyle: { color: '#EAEDF8' },
    tooltip: { position: 'top' },
    grid: { height: '50%', top: '10%' },
    xAxis: {
      type: 'category',
      data: ['小组赛', '32强', '16强', '8强', '半决赛', '决赛']
    },
    yAxis: {
      type: 'category',
      data: ['完全命中', '胜负命中', '失误']
    },
    visualMap: {
      min: 0,
      max: 100,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: '5%',
      textStyle: { color: '#EAEDF8' }
    },
    series: [{
      name: '准确率',
      type: 'heatmap',
      data: [[0,0,75], [1,0,68], [2,0,72], [3,0,65], [4,0,58], [5,0,0]],
      label: { show: true },
      itemStyle: { borderColor: '#0F0F16', borderWidth: 2 }
    }]
  };
  chart.setOption(option);
}

// Render Accuracy view
function renderAccuracyView() {
  const tbody = document.querySelector('#accuracyTable tbody');
  if (!tbody) return;

  const phaseNames = {
    group: '小组赛',
    round_of_32: '32强',
    round_of_16: '16强',
    quarter_final: '8强',
    semi_final: '半决赛',
    final: '决赛'
  };

  tbody.innerHTML = Object.entries(state.stats.byPhase).map(([phase, data]) => {
    const accuracy = data.total > 0 ? ((data.perfect + data.result) / data.total * 100).toFixed(1) : '0.0';
    return `
      <tr>
        <td>${phaseNames[phase] || phase}</td>
        <td class="text-right">${data.total}</td>
        <td class="text-right">${data.perfect || 0}</td>
        <td class="text-right">${data.result || 0}</td>
        <td class="text-right">${data.miss || 0}</td>
        <td class="text-right" style="font-weight: 700; color: var(--cyan);">${accuracy}%</td>
      </tr>
    `;
  }).join('');
}

// Render Tianji view
function renderTianjiView() {
  renderTodayHexagram();
  renderStarsChart();
  renderFortuneChart();
}

function renderTodayHexagram() {
  const container = document.getElementById('todayHexagram');
  if (!container) return;

  container.innerHTML = `
    <div style="text-align: center; padding: 2rem;">
      <div style="font-size: 64px; margin-bottom: 1rem;">☰</div>
      <div style="font-size: 20px; font-weight: 700; margin-bottom: 0.5rem;">乾卦</div>
      <div style="color: var(--muted);">天行健，君子以自强不息</div>
    </div>
  `;
}

function renderStarsChart() {
  const chartDom = document.getElementById('starsChart');
  if (!chartDom) return;

  const chart = echarts.init(chartDom);
  const option = {
    backgroundColor: 'transparent',
    textStyle: { color: '#EAEDF8' },
    radar: {
      indicator: [
        { name: '紫微', max: 10 },
        { name: '天机', max: 10 },
        { name: '太阳', max: 10 },
        { name: '天同', max: 10 },
        { name: '天府', max: 10 }
      ]
    },
    series: [{
      name: '星曜能量',
      type: 'radar',
      data: [{ value: [7, 5, 8, 6, 9], name: '当前' }]
    }]
  };
  chart.setOption(option);
}

function renderFortuneChart() {
  const chartDom = document.getElementById('fortuneChart');
  if (!chartDom) return;

  const chart = echarts.init(chartDom);
  const option = {
    backgroundColor: 'transparent',
    textStyle: { color: '#EAEDF8' },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: ['6/15', '6/20', '6/25', '6/30', '7/5', '7/10'] },
    yAxis: { type: 'value' },
    series: [{
      name: '气运指数',
      type: 'line',
      smooth: true,
      data: [65, 72, 68, 75, 80, 78],
      areaStyle: { opacity: 0.3 },
      itemStyle: { color: '#6C63FF' }
    }]
  };
  chart.setOption(option);
}

// Render accuracy chart
function renderAccuracyChart() {
  const chartDom = document.getElementById('accuracyChart');
  if (!chartDom) return;

  const chart = echarts.init(chartDom);
  const option = {
    backgroundColor: 'transparent',
    textStyle: { color: '#EAEDF8' },
    tooltip: { trigger: 'axis' },
    legend: { data: ['完全命中率', '胜负命中率'], textStyle: { color: '#EAEDF8' } },
    xAxis: { type: 'category', data: ['6月', '7月初', '7月中'] },
    yAxis: { type: 'value', max: 100 },
    series: [
      {
        name: '完全命中率',
        type: 'line',
        smooth: true,
        data: [28, 32, 35],
        itemStyle: { color: '#34D399' }
      },
      {
        name: '胜负命中率',
        type: 'line',
        smooth: true,
        data: [65, 68, 72],
        itemStyle: { color: '#22D3EE' }
      }
    ]
  };
  chart.setOption(option);
}

// Filter matches
function filterMatches(filter) {
  state.filters.status = filter;
  renderRecentMatches();
}

console.log('📱 App script loaded');
