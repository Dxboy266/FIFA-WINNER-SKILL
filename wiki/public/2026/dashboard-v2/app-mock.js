// FIFA 2026 Dashboard V2 — real embedded data (file:// safe)
// Expects window.DASHBOARD_DATA from data-embedded.js

const PHASE_LABEL = {
  group: '小组赛',
  round_of_32: '32强',
  round_of_16: '16强',
  quarter_final: '8强',
  semi_final: '半决赛',
  final: '决赛',
};

// 不展示三四名：赛程进度 / 筛选 / 列表均跳过
const PHASE_ORDER = [
  'group',
  'round_of_32',
  'round_of_16',
  'quarter_final',
  'semi_final',
  'final',
];

const HIDDEN_PHASES = new Set(['third_place']);

const EVAL_LABEL = {
  perfect: '完美双中',
  result: '胜负命中',
  miss: '完全失误',
};

const EVAL_CLASS = {
  perfect: 'eval-perfect',
  result: 'eval-result',
  miss: 'eval-miss',
};

const CONF_LABEL = {
  high: '高信心',
  medium: '中等信心',
  low: '低信心',
  unknown: '未知',
  none: '无',
};

const state = {
  currentView: 'overview',
  data: null,
  filters: { phase: 'all', date: null, status: 'all' },
  charts: {},
};

document.addEventListener('DOMContentLoaded', () => {
  state.data = window.DASHBOARD_DATA || null;
  if (!state.data) {
    console.error('DASHBOARD_DATA missing');
    return;
  }
  setupNavigation();
  setupEventListeners();
  renderView('overview');
  const s = state.data.stats || {};
  console.log(
    'Dashboard ready',
    s.total_predictions,
    'preds /',
    s.with_results,
    'evaluated /',
    s.result_accuracy + '% result /',
    s.score_accuracy + '% score'
  );
});

function setupNavigation() {
  document.querySelectorAll('.nav-item').forEach((item) => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      document.querySelectorAll('.nav-item').forEach((n) => n.classList.remove('active'));
      item.classList.add('active');
      switchView(item.dataset.view);
    });
  });
}

function setupEventListeners() {
  const refreshBtn = document.getElementById('refreshBtn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => {
      refreshBtn.style.animation = 'spin 0.5s linear';
      setTimeout(() => {
        refreshBtn.style.animation = '';
      }, 500);
      // 静态嵌入数据：重渲当前视图即可
      renderView(state.currentView);
    });
    refreshBtn.title = '重绘当前视图（数据为发布时嵌入，需重新构建后才会更新）';
  }

  const phaseDetailBtn = document.getElementById('phaseDetailBtn');
  if (phaseDetailBtn) {
    phaseDetailBtn.addEventListener('click', () => {
      document.querySelectorAll('.nav-item').forEach((n) => n.classList.remove('active'));
      document.querySelector('.nav-item[data-view="accuracy"]')?.classList.add('active');
      switchView('accuracy');
    });
  }

  document.querySelectorAll('.filter-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.filter-chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      state.filters.status = chip.dataset.filter || 'all';
      renderRecentMatches();
    });
  });

  const phaseFilter = document.getElementById('phaseFilter');
  if (phaseFilter) {
    phaseFilter.addEventListener('change', (e) => {
      state.filters.phase = e.target.value;
      renderPredictionsView();
    });
  }

  const dateFilter = document.getElementById('dateFilter');
  if (dateFilter) {
    populateDateFilterOptions(dateFilter);
    dateFilter.addEventListener('change', (e) => {
      state.filters.date = e.target.value || null;
      renderPredictionsView();
    });
  }

  const clearFilters = document.getElementById('clearFilters');
  if (clearFilters) {
    clearFilters.addEventListener('click', () => {
      state.filters.phase = 'all';
      state.filters.date = null;
      if (phaseFilter) phaseFilter.value = 'all';
      if (dateFilter) dateFilter.value = '';
      document.querySelectorAll('.filter-chip').forEach((c) => c.classList.remove('active'));
      document.querySelector('.filter-chip[data-filter="all"]')?.classList.add('active');
      state.filters.status = 'all';
      renderPredictionsView();
      renderRecentMatches();
    });
  }
}

/** Build date options from embedded matches (Beijing match day preferred). */
function populateDateFilterOptions(selectEl) {
  if (!selectEl || selectEl.dataset.ready === '1') return;
  const counts = new Map();
  matches().forEach((m) => {
    if (!(m.has_prediction || m.final_score)) return;
    const d = m.beijing_date || (m.kickoff_at || '').slice(0, 10);
    if (!d) return;
    counts.set(d, (counts.get(d) || 0) + 1);
  });
  const dates = [...counts.keys()].sort().reverse();
  // keep first "全部日期" option
  selectEl.innerHTML = '<option value="">全部日期</option>';
  dates.forEach((d) => {
    const opt = document.createElement('option');
    opt.value = d;
    const n = counts.get(d);
    // label: 2026-07-15（北京） · 2场
    opt.textContent = `${d}（北京） · ${n}场`;
    selectEl.appendChild(opt);
  });
  selectEl.dataset.ready = '1';
}

function switchView(viewName) {
  state.currentView = viewName;
  document.querySelectorAll('.view-panel').forEach((p) => p.classList.remove('active'));
  document.getElementById(`view-${viewName}`)?.classList.add('active');

  const titles = {
    overview: '总览看板',
    predictions: '比赛预测',
    analytics: '数据分析',
    accuracy: '准确率追踪',
    tianji: '天纪占卜',
    settings: '设置',
  };
  const crumbs = {
    overview: '总览',
    predictions: '比赛预测',
    analytics: '数据分析',
    accuracy: '准确率',
    tianji: '天纪占卜',
    settings: '设置',
  };
  const titleEl = document.querySelector('.page-title');
  if (titleEl) titleEl.textContent = titles[viewName] || viewName;
  const crumb = document.querySelector('.breadcrumb-item.active');
  if (crumb) crumb.textContent = crumbs[viewName] || viewName;
  renderView(viewName);
}

function renderView(viewName) {
  switch (viewName) {
    case 'overview':
      return renderOverviewView();
    case 'predictions':
      return renderPredictionsView();
    case 'analytics':
      return renderAnalyticsView();
    case 'accuracy':
      return renderAccuracyView();
    case 'tianji':
      return renderTianjiView();
  }
}

/** Canonical filter key: Beijing calendar day when available. */
function matchDateKey(m) {
  return m.beijing_date || (m.kickoff_at || '').slice(0, 10) || '';
}

function stats() {
  return state.data.stats || {};
}
// 列表不展示未绑定球队的决赛/占位卡（W101 等）
function isPlaceholderMatch(m) {
  if (HIDDEN_PHASES.has(m.phase)) return true;
  const hn = String(m.home_team?.name || m.home_team?.team_id || '');
  const an = String(m.away_team?.name || m.away_team?.team_id || '');
  if (/^W\d|^L\d|胜者|负者|TBD|待定/i.test(hn) || /^W\d|^L\d|胜者|负者|TBD|待定/i.test(an)) {
    return true;
  }
  return false;
}

function matches() {
  return (state.data.matches || []).filter((m) => !isPlaceholderMatch(m));
}
function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}
function formatKickoff(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  } catch {
    return String(iso).slice(0, 16);
  }
}
function confBadge(conf, kind = '') {
  const c = String(conf || 'unknown').toLowerCase();
  const label = CONF_LABEL[c] || conf || '—';
  const prefix = kind === 'result' ? '方向·' : kind === 'score' ? '比分·' : '';
  return `<span class="badge badge-${c}">${prefix}${label}</span>`;
}
function evalBadge(m) {
  const ev = typeof m === 'string' ? m : m?.evaluation;
  const label =
    (typeof m === 'object' && m?.evaluation_label) ||
    EVAL_LABEL[ev] ||
    (typeof m === 'object' && m?.hit_class === 'not-predicted' && '未预测') ||
    (typeof m === 'object' && m?.hit_class === 'placeholder' && '占位') ||
    (typeof m === 'object' && m?.hit_class === 'pending' && '待赛果') ||
    (ev ? String(ev) : '待赛');
  const cls =
    EVAL_CLASS[ev] ||
    (label === '未预测'
      ? 'badge-pending'
      : label === '占位'
        ? 'badge-pending'
        : label === '待赛果'
          ? 'badge-pending'
          : '');
  return `<span class="badge ${cls}">${label}</span>`;
}
function scoreText(score) {
  if (!score || score.home == null) return '—';
  return `${score.home}-${score.away}`;
}
function disposeChart(key) {
  if (state.charts[key]) {
    try {
      state.charts[key].dispose();
    } catch {}
    delete state.charts[key];
  }
}

/* ================= Overview ================= */

function renderOverviewView() {
  const s = stats();
  setText('totalPredictions', s.total_predictions ?? '—');
  setText('accuracyRate', s.with_results != null ? `${s.result_accuracy}%` : '—');
  setText('perfectHits', s.perfect_hits ?? '—');
  setText('resultHits', s.result_hits ?? '—');
  setText('todayMatches', s.upcoming_count ?? s.today_count ?? 0);

  const trendEl = document.querySelector('#view-overview .stat-trend span');
  if (trendEl) {
    trendEl.textContent = `比分 ${s.score_accuracy}% · 进球数 ${s.goals_accuracy ?? '—'}% · 已验 ${s.with_results}`;
  }
  document.querySelector('#view-overview .stat-trend')?.classList.add('stat-meta');

  // detail under perfect/result cards
  const perfectCard = document.querySelector('#perfectHits')?.closest('.stat-card');
  if (perfectCard) {
    const d = perfectCard.querySelector('.stat-detail');
    if (d) d.textContent = `比分全中 ${s.score_accuracy}%（${s.perfect_hits}/${s.with_results}）`;
  }
  const resultCard = document.querySelector('#resultHits')?.closest('.stat-card');
  if (resultCard) {
    const d = resultCard.querySelector('.stat-detail');
    if (d) d.textContent = `含比分命中 · 失误 ${s.misses}`;
  }

  renderPhaseTracker();
  renderOverviewBreakdown();
  renderRecentMatches();
  renderAccuracyChart();
  renderDataQualityBanner();
}

function renderDataQualityBanner() {
  let host = document.getElementById('dataQualityBanner');
  if (!host) {
    const hero = document.querySelector('#view-overview .hero-stats');
    if (!hero) return;
    host = document.createElement('div');
    host.id = 'dataQualityBanner';
    host.className = 'data-quality-banner';
    hero.insertAdjacentElement('afterend', host);
  }
  const s = stats();
  const src = state.data.source || {};
  const notPred = (state.data.not_predicted || []).filter(
    (x) => !HIDDEN_PHASES.has(x.phase)
  );
  const upcoming = s.upcoming_count ?? 0;
  host.innerHTML = `
    <div class="dq-grid">
      <div>
        <div class="dq-label">数据源</div>
        <div class="dq-value">prediction-dashboard + match-ledger</div>
        <div class="dq-sub">as_of ${state.data.as_of || '—'} · 生成 ${src.dashboard_generated_at || '—'}</div>
      </div>
      <div>
        <div class="dq-label">覆盖</div>
        <div class="dq-value">${s.total_predictions} 预测 / ${s.total_matches} 场次</div>
        <div class="dq-sub">已验 ${s.with_results} · 待赛 ${upcoming} · 赛后无预测 ${s.not_predicted_completed || 0}</div>
      </div>
      <div>
        <div class="dq-label">看板命中（ledger 覆盖）</div>
        <div class="dq-value">${s.result_accuracy != null ? s.result_accuracy + '%' : '—'}</div>
        <div class="dq-sub">胜负 ${s.result_hits}/${s.with_results} · 比分 ${s.score_accuracy}%</div>
      </div>
      <div>
        <div class="dq-label">缺口</div>
        <div class="dq-value">${notPred.length ? notPred.map((x) => x.match_id).join(' · ') : '无重大缺口'}</div>
        <div class="dq-sub">${
          notPred.length
            ? notPred.map((x) => `${x.home} vs ${x.away} ${x.actual || ''}`).join('；')
            : '半决赛已预测 · 决赛待球队绑定'
        }</div>
      </div>
    </div>
  `;
}

function renderOverviewBreakdown() {
  let host = document.getElementById('overviewBreakdown');
  if (!host) {
    const hero = document.querySelector('#view-overview .hero-stats');
    if (!hero) return;
    host = document.createElement('div');
    host.id = 'overviewBreakdown';
    host.className = 'overview-breakdown';
    const banner = document.getElementById('dataQualityBanner');
    (banner || hero).insertAdjacentElement('afterend', host);
  }
  const phases = stats().by_phase || {};
  const cards = PHASE_ORDER.filter((k) => phases[k])
    .map((k) => {
      const p = phases[k];
      return `<div class="mini-stat">
        <div class="mini-stat-label">${p.label || PHASE_LABEL[k]}</div>
        <div class="mini-stat-value">${p.result_accuracy}%</div>
        <div class="mini-stat-detail">${p.result}/${p.total} 胜负 · 比分 ${p.score_accuracy}%（${p.perfect}）</div>
      </div>`;
    })
    .join('');
  host.innerHTML = `
    <div class="section-header" style="margin-bottom:var(--space-4)">
      <h2 class="section-title" style="font-size:16px">分阶段命中（ledger 覆盖后）</h2>
      <span class="muted-text">已验 ${stats().with_results} / 预测 ${stats().total_predictions}</span>
    </div>
    <div class="mini-stat-grid">${cards}</div>
  `;
}

function renderPhaseTracker() {
  const container = document.getElementById('phaseTracker');
  if (!container) return;
  const progress = stats().phase_progress || {};
  container.innerHTML =
    PHASE_ORDER.map((key) => {
      const p = progress[key];
      if (!p || !p.total) return '';
      const pct = Math.round((p.completed / p.total) * 100);
      return `<div class="phase-row">
        <div class="phase-row-head">
          <span class="phase-name">${PHASE_LABEL[key] || key}</span>
          <span class="phase-count">${p.completed}/${p.total} · 预测 ${p.predicted || 0}</span>
        </div>
        <div class="phase-bar"><div class="phase-bar-fill" style="width:${pct}%"></div></div>
      </div>`;
    }).join('') || '<p class="muted-text">暂无赛程</p>';
}

function renderRecentMatches() {
  const container = document.getElementById('recentMatches');
  if (!container) return;
  const asOf = state.data.as_of || TODAY_FALLBACK();
  let list = matches().slice();
  const f = state.filters.status || 'all';
  if (f === 'today') {
    list = list.filter(
      (m) => m.beijing_date === asOf || (m.kickoff_at || '').startsWith(asOf)
    );
  } else if (f === 'upcoming') {
    list = list.filter((m) => m.has_prediction && !m.final_score);
  } else {
    list = list.filter((m) => m.has_prediction || m.final_score);
  }
  // 待赛优先，再按开球时间新→旧
  list.sort((a, b) => {
    const ap = a.has_prediction && !a.final_score ? 0 : a.final_score ? 2 : 1;
    const bp = b.has_prediction && !b.final_score ? 0 : b.final_score ? 2 : 1;
    if (ap !== bp) return ap - bp;
    return (b.kickoff_at || '').localeCompare(a.kickoff_at || '');
  });
  list = list.slice(0, 12);
  container.innerHTML = list.length
    ? list.map((m) => renderMatchCard(m, 'compact')).join('')
    : '<p class="empty-state">暂无数据</p>';
}

function TODAY_FALLBACK() {
  // 优先用数据 as_of；否则取本地日历日
  try {
    return new Date().toISOString().slice(0, 10);
  } catch {
    return '2026-07-12';
  }
}

function radarVal(side, key) {
  const v = side?.[key];
  return v == null || v === '' ? '—' : v;
}

function renderDistBars(dist) {
  if (!Array.isArray(dist) || !dist.length) return '';
  const rows = dist.slice(0, 5).map((d) => {
    const sc = d.score || {};
    const label = sc.home != null ? `${sc.home}-${sc.away}` : d.score_text || '—';
    const pct = Math.round((Number(d.probability) || 0) * 100);
    return `<div class="dist-row">
      <span class="dist-score mono">${label}</span>
      <div class="dist-track"><div class="dist-fill" style="width:${pct}%"></div></div>
      <span class="dist-pct mono">${pct}%</span>
    </div>`;
  });
  return `<div class="match-panel match-panel-span">
    <div class="panel-title">比分分布 <span class="muted-text small">（非铁分）</span></div>
    <div class="dist-list">${rows.join('')}</div>
  </div>`;
}

function renderMatchCard(match, style = 'full') {
  const home = match.home_team?.name || 'TBD';
  const away = match.away_team?.name || 'TBD';
  const pred = match.prediction || {};
  const pscore = pred.score || null;
  const actual = match.final_score;
  const phase = PHASE_LABEL[match.phase] || match.phase || '';
  const hex =
    match.divination?.hexagram_name ||
    match.divination?.hexagram ||
    match.tianji_score_text ||
    '';
  const resultConf = match.result_confidence || pred.result_confidence || pred.confidence;
  const scoreConf = match.score_confidence || pred.score_confidence || 'low';

  if (style === 'compact') {
    return `<div class="match-card match-card-compact">
      <div class="match-card-main">
        <div class="match-meta">${match.match_id} · ${phase} · ${formatKickoff(match.kickoff_at)}</div>
        <div class="match-teams">${home} <span class="vs">vs</span> ${away}</div>
        <div class="match-tags">
          ${match.has_prediction ? confBadge(resultConf, 'result') : '<span class="badge badge-pending">无预测</span>'}
          ${evalBadge(match)}
          ${hex && match.has_prediction ? `<span class="badge badge-hex">${match.divination?.hexagram_name || hex}</span>` : ''}
        </div>
      </div>
      <div class="match-score-block">
        <div class="pred-score">${match.has_prediction ? scoreText(pscore) : '—'}</div>
        <div class="pred-label">预测</div>
        ${actual ? `<div class="actual-score">实际 ${scoreText(actual)}</div>` : `<div class="actual-score pending">未赛</div>`}
      </div>
    </div>`;
  }

  const va = match.venue_adaptation || {};
  const div = match.divination || {};
  const play = match.play_card || {};
  const radarH = match.radar?.home || {};
  const radarA = match.radar?.away || {};
  const layers = match.analysis_layers || [];
  const dist = pred.scoreline_distribution || [];
  const hasRadar =
    radarVal(radarH, 'attack') !== '—' || radarVal(radarA, 'attack') !== '—';

  return `<div class="match-card match-card-full ${match.evaluation ? 'eval-' + match.evaluation : ''}">
    <div class="match-card-top">
      <div class="match-identity">
        <div class="match-meta">
          <span class="phase-pill">${phase || '—'}</span>
          <span>${match.match_id}</span>
          <span>${formatKickoff(match.kickoff_at)}</span>
          ${match.beijing_date ? `<span>北京 ${match.beijing_date} ${match.beijing_time || ''}</span>` : ''}
        </div>
        <div class="match-teams-lg">
          <span class="team-name">${home}</span>
          <span class="vs">vs</span>
          <span class="team-name">${away}</span>
        </div>
        <div class="match-venue">${match.venue || ''}</div>
        <div class="match-tags" style="margin-top:10px">
          ${match.has_prediction ? confBadge(resultConf, 'result') : ''}
          ${match.has_prediction ? confBadge(scoreConf, 'score') : ''}
          ${evalBadge(match)}
          ${match.edge_tier ? `<span class="badge">edge·${match.edge_tier}</span>` : ''}
          ${match.game_script ? `<span class="badge">${match.game_script}</span>` : ''}
        </div>
      </div>
      <div class="match-score-hero">
        <div class="score-hero-pred">
          <div class="pred-score">${match.has_prediction ? scoreText(pscore) : '—:—'}</div>
          <div class="pred-label">预测 · ${pred.result_label || pred.result || '—'}</div>
        </div>
        <div class="score-hero-actual ${actual ? '' : 'is-pending'}">
          <div class="actual-score-lg">${actual ? scoreText(actual) : '待赛'}</div>
          <div class="pred-label">${actual ? '实际比分' : '赛果未出'}</div>
        </div>
        ${
          pred.tianji_score_text
            ? `<div class="tianji-score-chip">天纪 ${pred.tianji_score_text}${
                pred.tianji_total_goals != null ? ` · ${pred.tianji_total_goals}球` : ''
              }</div>`
            : ''
        }
      </div>
    </div>
    <div class="match-grid">
      <div class="match-panel">
        <div class="panel-title">排名 / 雷达</div>
        <div class="score-pair">
          <span>#${match.home_team?.ranking ?? '—'}</span>
          <span class="muted-text">vs</span>
          <span>#${match.away_team?.ranking ?? '—'}</span>
        </div>
        ${
          hasRadar
            ? `<div class="detail-list">
          <div><span>攻</span><span class="mono">${radarVal(radarH, 'attack')} / ${radarVal(radarA, 'attack')}</span></div>
          <div><span>防</span><span class="mono">${radarVal(radarH, 'defense')} / ${radarVal(radarA, 'defense')}</span></div>
          <div><span>中</span><span class="mono">${radarVal(radarH, 'midfield')} / ${radarVal(radarA, 'midfield')}</span></div>
          <div><span>近况</span><span class="mono">${radarVal(radarH, 'recent_form')} / ${radarVal(radarA, 'recent_form')}</span></div>
        </div>`
            : `<div class="muted-text small">雷达未回填（占位/后绑定场次）</div>`
        }
      </div>
      <div class="match-panel">
        <div class="panel-title">天纪</div>
        <div class="hex-line">
          <span class="hex-glyph">${div.hexagram || div.hexagram_name || '☰'}</span>
          <div>
            <div class="hex-name">${div.hexagram_name || div.hexagram || '—'}</div>
            <div class="muted-text small">${div.shichen || ''}</div>
          </div>
        </div>
        <div class="muted-text small">${div.interpretation || div.hexagram_interpretation || '—'}</div>
        <div class="fortune-row">主 ${div.combined_home_fortune || '—'} · 客 ${div.combined_away_fortune || '—'}</div>
      </div>
      <div class="match-panel">
        <div class="panel-title">预测细节</div>
        <div class="detail-list">
          <div><span>方向</span><span>${pred.result_label || pred.result || '—'}</span></div>
          <div><span>总进球</span><span>${pred.total_goals ?? '—'}</span></div>
          <div><span>xG</span><span class="mono">${fmtXG(pred.expected_goals_proxy)}</span></div>
          <div><span>零封概率</span><span class="mono">${fmtCS(pred.clean_sheet_probability)}</span></div>
          <div><span>分析层</span><span>${match.layer_count || 0}</span></div>
          <div><span>来源</span><span class="origin-tag">${pred.origin || match.prediction_origin || '—'}</span></div>
        </div>
      </div>
      <div class="match-panel">
        <div class="panel-title">场地 / 摘要</div>
        <div class="detail-list">
          <div><span>主队旅行</span><span>${fmtNum(va.travel_km_home)} km · ${va.adaptation_risk_home || '—'}</span></div>
          <div><span>客队旅行</span><span>${fmtNum(va.travel_km_away)} km · ${va.adaptation_risk_away || '—'}</span></div>
          <div><span>温差</span><span class="mono">${fmtNum(va.temperature_delta_home)} / ${fmtNum(va.temperature_delta_away)} °C</span></div>
        </div>
        <div class="summary-text">${play.title || play.hook || (play.watch_points || [])[0] || '—'}</div>
        ${(play.risk_flags || []).length ? `<div class="risk-flags">${(play.risk_flags || []).slice(0, 3).map((r) => `<span class="badge">${r}</span>`).join('')}</div>` : ''}
      </div>
      ${renderDistBars(dist)}
    </div>
    ${
      layers.length
        ? `<div class="layers-row">${layers
            .slice(0, 4)
            .map(
              (l) =>
                `<div class="layer-chip"><strong>${l.name || '层'}</strong><span>${truncate(l.summary, 56)}</span></div>`
            )
            .join('')}</div>`
        : ''
    }
  </div>`;
}

function fmtNum(v) {
  if (v == null || v === '') return '—';
  const n = Number(v);
  return Number.isFinite(n) ? (Math.abs(n) >= 100 ? Math.round(n) : Math.round(n * 10) / 10) : v;
}
function fmtXG(x) {
  if (!x) return '—';
  return `${fmtNum(x.home)} / ${fmtNum(x.away)}`;
}
function fmtCS(x) {
  if (!x) return '—';
  const h = x.home != null ? Math.round(x.home * 100) + '%' : '—';
  const a = x.away != null ? Math.round(x.away * 100) + '%' : '—';
  return `${h} / ${a}`;
}
function truncate(s, n) {
  s = String(s || '');
  return s.length > n ? s.slice(0, n) + '…' : s || '—';
}

/* ================= Predictions ================= */

function renderPredictionsView() {
  const container = document.getElementById('predictionsGrid');
  if (!container) return;
  let list = matches().filter((m) => m.has_prediction || m.final_score);
  if (state.filters.phase && state.filters.phase !== 'all') {
    list = list.filter((m) => m.phase === state.filters.phase);
  }
  if (state.filters.date) {
    const d = state.filters.date;
    list = list.filter((m) => matchDateKey(m) === d);
  }
  // pending / upcoming first, then recent results
  list.sort((a, b) => {
    const ap = a.has_prediction && !a.final_score ? 0 : a.final_score ? 2 : 1;
    const bp = b.has_prediction && !b.final_score ? 0 : b.final_score ? 2 : 1;
    if (ap !== bp) return ap - bp;
    return (b.kickoff_at || '').localeCompare(a.kickoff_at || '');
  });
  container.innerHTML = list.length
    ? list.map((m) => renderMatchCard(m, 'full')).join('')
    : '<p class="empty-state" style="grid-column:1/-1">暂无符合条件的比赛</p>';
}

/* ================= Accuracy ================= */

function renderAccuracyView() {
  const s = stats();
  const meters = document.querySelectorAll('#view-accuracy .accuracy-card');
  const values = [
    {
      label: '胜负准确率',
      value: s.result_accuracy || 0,
      sub: `${s.result_hits || 0}/${s.with_results || 0}`,
    },
    {
      label: '比分准确率',
      value: s.score_accuracy || 0,
      sub: `${s.perfect_hits || 0}/${s.with_results || 0}`,
    },
    {
      label: '总进球命中率',
      value: s.goals_accuracy || 0,
      sub: `${s.goals_hits || 0}/${s.with_results || 0}`,
    },
  ];
  meters.forEach((card, i) => {
    const v = values[i];
    if (!v) return;
    const label = card.querySelector('.accuracy-label');
    const fill = card.querySelector('.meter-fill');
    const val = card.querySelector('.meter-value');
    if (label) label.textContent = v.label;
    if (fill) {
      fill.style.width = `${Math.min(100, v.value)}%`;
      fill.classList.toggle('meter-fill-warn', i === 2 && v.value < 30);
    }
    if (val) val.textContent = `${v.value}%`;
    let sub = card.querySelector('.meter-sub');
    if (!sub) {
      sub = document.createElement('div');
      sub.className = 'meter-sub';
      card.appendChild(sub);
    }
    sub.textContent = v.sub;
  });

  const tbody = document.querySelector('#accuracyTable tbody');
  if (tbody) {
    const byPhase = s.by_phase || {};
    tbody.innerHTML =
      PHASE_ORDER.filter((k) => byPhase[k])
        .map((k) => {
          const p = byPhase[k];
          return `<tr>
            <td>${p.label || PHASE_LABEL[k]}</td>
            <td class="text-right">${p.total}</td>
            <td class="text-right">${p.perfect}</td>
            <td class="text-right">${p.result}</td>
            <td class="text-right">${p.miss}</td>
            <td class="text-right accent-cell">${p.result_accuracy}%</td>
          </tr>`;
        })
        .join('') ||
      '<tr><td colspan="6" class="muted-text">暂无</td></tr>';
  }

  ensureAccuracyExtras();
  renderAccuracyDetailTable();
  renderAccuracyCharts();
  renderIssueTags();
}

function ensureAccuracyExtras() {
  if (document.getElementById('accuracyDetailTable')) return;
  const section = document.querySelector('#view-accuracy .section');
  if (!section) return;

  const charts = document.createElement('div');
  charts.className = 'accuracy-charts';
  charts.innerHTML = `
    <div class="analytics-card">
      <h3 class="card-title">累计命中率走势</h3>
      <div id="accTrendChart" class="chart"></div>
    </div>
    <div class="analytics-card">
      <h3 class="card-title">按信心度命中</h3>
      <div id="accConfChart" class="chart"></div>
    </div>
  `;
  section.insertBefore(charts, section.querySelector('.data-table-wrapper'));

  const issues = document.createElement('div');
  issues.id = 'issueTagsPanel';
  issues.className = 'issues-panel';
  section.appendChild(issues);

  const wrap = document.createElement('div');
  wrap.className = 'data-table-wrapper';
  wrap.style.marginTop = 'var(--space-6)';
  wrap.innerHTML = `
    <div class="table-caption">逐场回测（${(state.data.accuracy_rows || []).length} 场，最近优先）</div>
    <table class="data-table" id="accuracyDetailTable">
      <thead>
        <tr>
          <th>比赛</th>
          <th>阶段</th>
          <th class="text-right">预测</th>
          <th class="text-right">实际</th>
          <th>信心</th>
          <th>卦象</th>
          <th>结果</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  `;
  section.appendChild(wrap);
}

function renderIssueTags() {
  const host = document.getElementById('issueTagsPanel');
  if (!host) return;
  const tags = state.data.model_issue_tags || [];
  const actions = state.data.corrective_actions || [];
  host.innerHTML = `
    <div class="section-header"><h2 class="section-title" style="font-size:16px">模型问题标签 / 纠正动作</h2></div>
    <div class="issues-grid">
      <div class="analytics-card">
        <h3 class="card-title">高频错误标签</h3>
        ${
          tags.length
            ? tags
                .map(
                  (t) =>
                    `<div class="issue-row"><span>${t.tag || t.name || 'tag'}</span><span class="mono">${t.frequency ?? t.total_occurrences ?? t.count ?? ''}</span></div>`
                )
                .join('')
            : '<div class="muted-text">无标签</div>'
        }
      </div>
      <div class="analytics-card">
        <h3 class="card-title">纠正动作</h3>
        ${
          actions.length
            ? actions
                .map(
                  (a) =>
                    `<div class="issue-row"><span>${a.description || a.action_id || ''}</span><span class="badge">${a.status || a.priority || ''}</span></div>`
                )
                .join('')
            : '<div class="muted-text">无动作</div>'
        }
      </div>
    </div>
  `;
}

function renderAccuracyDetailTable() {
  const tbody = document.querySelector('#accuracyDetailTable tbody');
  if (!tbody) return;
  const rows = state.data.accuracy_rows || [];
  tbody.innerHTML = rows
    .map(
      (r) => `<tr class="row-${r.evaluation || 'pending'}">
      <td>
        <div class="cell-main">${r.home} vs ${r.away}</div>
        <div class="cell-sub">${r.match_id} · ${formatKickoff(r.kickoff_at)}</div>
      </td>
      <td>${PHASE_LABEL[r.phase] || r.phase || '—'}</td>
      <td class="text-right mono">${r.pred}</td>
      <td class="text-right mono">${r.actual}</td>
      <td>${confBadge(r.confidence)}</td>
      <td>${r.hexagram || '—'}</td>
      <td>${evalBadge({ evaluation: r.evaluation, evaluation_label: r.evaluation_label })}</td>
    </tr>`
    )
    .join('');
}

function renderAccuracyCharts() {
  const trend = state.data.trend || [];
  const conf = stats().by_confidence || {};
  if (!window.echarts) return;

  const trendDom = document.getElementById('accTrendChart');
  if (trendDom) {
    disposeChart('accTrend');
    const chart = echarts.init(trendDom);
    state.charts.accTrend = chart;
    chart.setOption(lineTrendOption(trend));
  }

  const confDom = document.getElementById('accConfChart');
  if (confDom) {
    disposeChart('accConf');
    const chart = echarts.init(confDom);
    state.charts.accConf = chart;
    const keys = Object.keys(conf);
    chart.setOption({
      backgroundColor: 'transparent',
      textStyle: { color: '#EAEDF8' },
      tooltip: { trigger: 'axis' },
      legend: {
        data: ['胜负命中率', '比分命中率', '场次'],
        textStyle: { color: '#7A7A90' },
      },
      grid: { left: 40, right: 40, top: 40, bottom: 40 },
      xAxis: {
        type: 'category',
        data: keys.map((k) => CONF_LABEL[k] || k),
        axisLabel: { color: '#7A7A90' },
      },
      yAxis: [
        {
          type: 'value',
          max: 100,
          axisLabel: { color: '#7A7A90', formatter: '{value}%' },
          splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
        },
        {
          type: 'value',
          axisLabel: { color: '#7A7A90' },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: '胜负命中率',
          type: 'bar',
          data: keys.map((k) => conf[k].result_accuracy),
          itemStyle: { color: '#6C63FF', borderRadius: [6, 6, 0, 0] },
        },
        {
          name: '比分命中率',
          type: 'bar',
          data: keys.map((k) => conf[k].score_accuracy),
          itemStyle: { color: '#22D3EE', borderRadius: [6, 6, 0, 0] },
        },
        {
          name: '场次',
          type: 'line',
          yAxisIndex: 1,
          data: keys.map((k) => conf[k].total),
          itemStyle: { color: '#FBBF24' },
        },
      ],
    });
  }
}

function lineTrendOption(trend) {
  return {
    backgroundColor: 'transparent',
    textStyle: { color: '#EAEDF8' },
    tooltip: { trigger: 'axis' },
    legend: {
      data: ['累计胜负命中率', '累计比分命中率', '当日场次'],
      textStyle: { color: '#7A7A90' },
    },
    grid: { left: 48, right: 48, top: 48, bottom: 36 },
    xAxis: {
      type: 'category',
      data: trend.map((t) => String(t.date).slice(5)),
      axisLabel: { color: '#7A7A90', fontSize: 11 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.08)' } },
    },
    yAxis: [
      {
        type: 'value',
        max: 100,
        axisLabel: { color: '#7A7A90', formatter: '{value}%' },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
      },
      {
        type: 'value',
        axisLabel: { color: '#7A7A90' },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '累计胜负命中率',
        type: 'line',
        smooth: true,
        data: trend.map((t) => t.cum_result_accuracy),
        itemStyle: { color: '#22D3EE' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(34,211,238,0.2)' },
              { offset: 1, color: 'rgba(34,211,238,0)' },
            ],
          },
        },
      },
      {
        name: '累计比分命中率',
        type: 'line',
        smooth: true,
        data: trend.map((t) => t.cum_score_accuracy),
        itemStyle: { color: '#34D399' },
      },
      {
        name: '当日场次',
        type: 'bar',
        yAxisIndex: 1,
        data: trend.map((t) => t.day_total),
        itemStyle: { color: 'rgba(108,99,255,0.35)', borderRadius: [4, 4, 0, 0] },
      },
    ],
  };
}

/* ================= Analytics ================= */

function renderAnalyticsView() {
  if (!window.echarts) return;
  renderStrengthChart();
  renderConfidenceChart();
  renderHeatmapChart();
}

function renderStrengthChart() {
  const chartDom = document.getElementById('strengthChart');
  if (!chartDom) return;
  disposeChart('strength');
  const bins = stats().strength_bins || {};
  const order = ['Top5', '6-10', '11-20', '21-40', '40+'];
  const chart = echarts.init(chartDom);
  state.charts.strength = chart;
  chart.setOption({
    backgroundColor: 'transparent',
    textStyle: { color: '#EAEDF8' },
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 16, top: 24, bottom: 32 },
    xAxis: {
      type: 'category',
      data: order,
      axisLabel: { color: '#7A7A90' },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#7A7A90' },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
    },
    series: [
      {
        name: '队伍出现次数（按 FIFA 排名）',
        type: 'bar',
        data: order.map((k) => bins[k] || 0),
        itemStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: '#6C63FF' },
              { offset: 1, color: '#22D3EE' },
            ],
          },
          borderRadius: [6, 6, 0, 0],
        },
      },
    ],
  });
}

function renderConfidenceChart() {
  const chartDom = document.getElementById('confidenceChart');
  if (!chartDom) return;
  disposeChart('confidence');
  const dist = stats().confidence_dist || {};
  const chart = echarts.init(chartDom);
  state.charts.confidence = chart;
  const data = Object.entries(dist).map(([k, v]) => ({
    name: CONF_LABEL[k] || k,
    value: v,
  }));
  chart.setOption({
    backgroundColor: 'transparent',
    textStyle: { color: '#EAEDF8' },
    tooltip: { trigger: 'item' },
    series: [
      {
        name: '信心度分布',
        type: 'pie',
        radius: ['42%', '70%'],
        data,
        label: { color: '#EAEDF8' },
        itemStyle: {
          color: (params) =>
            ['#34D399', '#22D3EE', '#FBBF24', '#F87171', '#6C63FF'][
              params.dataIndex % 5
            ],
        },
      },
    ],
  });
}

function renderHeatmapChart() {
  const chartDom = document.getElementById('heatmapChart');
  if (!chartDom) return;
  disposeChart('heatmap');
  const byPhase = stats().by_phase || {};
  const phases = PHASE_ORDER.filter((k) => byPhase[k]);
  const labels = phases.map((k) => byPhase[k].label || PHASE_LABEL[k]);
  // Stacked 100% bars: miss / result-only / perfect — clearer than heatmap for n=8 R16
  const perfect = phases.map((k) => {
    const p = byPhase[k];
    return Math.round(((p.perfect || 0) / (p.total || 1)) * 100);
  });
  const resultOnly = phases.map((k) => {
    const p = byPhase[k];
    return Math.round((((p.result || 0) - (p.perfect || 0)) / (p.total || 1)) * 100);
  });
  const miss = phases.map((k) => {
    const p = byPhase[k];
    return Math.round(((p.miss || 0) / (p.total || 1)) * 100);
  });
  const totals = phases.map((k) => byPhase[k].total || 0);
  const chart = echarts.init(chartDom);
  state.charts.heatmap = chart;
  chart.setOption({
    backgroundColor: 'transparent',
    textStyle: { color: '#EAEDF8' },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (items) => {
        if (!items?.length) return '';
        const i = items[0].dataIndex;
        const p = byPhase[phases[i]];
        return [
          `<b>${labels[i]}</b> · n=${totals[i]}`,
          `完全命中 ${p.perfect}/${totals[i]} (${perfect[i]}%)`,
          `仅胜负 ${(p.result || 0) - (p.perfect || 0)}/${totals[i]} (${resultOnly[i]}%)`,
          `失误 ${p.miss}/${totals[i]} (${miss[i]}%)`,
          `胜负准确率 ${p.result_accuracy ?? Math.round(((p.result || 0) / (p.total || 1)) * 100)}%`,
        ].join('<br/>');
      },
    },
    legend: {
      data: ['完全命中', '仅胜负', '失误'],
      top: 0,
      textStyle: { color: '#7A7A90' },
    },
    grid: { top: 40, left: 48, right: 24, bottom: 36, containLabel: true },
    xAxis: {
      type: 'category',
      data: labels.map((l, i) => `${l}\nn=${totals[i]}`),
      axisLabel: { color: '#7A7A90', lineHeight: 16 },
    },
    yAxis: {
      type: 'value',
      max: 100,
      axisLabel: { color: '#7A7A90', formatter: '{value}%' },
      splitLine: { lineStyle: { color: 'rgba(122,122,144,0.15)' } },
    },
    series: [
      {
        name: '完全命中',
        type: 'bar',
        stack: 'hit',
        data: perfect,
        itemStyle: { color: '#34D399' },
        label: {
          show: true,
          formatter: (p) => (p.value ? `${p.value}%` : ''),
          color: '#0F0F16',
          fontSize: 11,
        },
      },
      {
        name: '仅胜负',
        type: 'bar',
        stack: 'hit',
        data: resultOnly,
        itemStyle: { color: '#6C63FF' },
        label: {
          show: true,
          formatter: (p) => (p.value ? `${p.value}%` : ''),
          color: '#EAEDF8',
          fontSize: 11,
        },
      },
      {
        name: '失误',
        type: 'bar',
        stack: 'hit',
        data: miss,
        itemStyle: { color: '#F87171' },
        label: {
          show: true,
          formatter: (p) => (p.value ? `${p.value}%` : ''),
          color: '#0F0F16',
          fontSize: 11,
        },
        emphasis: { focus: 'series' },
      },
    ],
  });
}

/* ================= Tianji ================= */

function renderTianjiView() {
  renderTodayHexagram();
  renderStarsChart();
  renderFortuneChart();
}

function renderTodayHexagram() {
  const container = document.getElementById('todayHexagram');
  if (!container) return;
  const withDiv = matches().find(
    (m) => m.divination && (m.divination.hexagram_name || m.divination.hexagram)
  );
  const d = withDiv?.divination || {};
  const top = stats().hexagram_top || [];
  container.innerHTML = `
    <div class="hexagram-hero">
      <div class="hex-glyph-lg">${d.hexagram || d.hexagram_name || '☰'}</div>
      <div class="hex-name-lg">${d.hexagram_name || '—'}</div>
      <div class="muted-text">${d.hexagram_interpretation || d.interpretation || '暂无解读'}</div>
      <div class="hex-meta">${d.shichen || ''} ${d.lunar_date ? '· ' + d.lunar_date : ''}</div>
      ${
        withDiv
          ? `<div class="hex-from">来自 ${withDiv.match_id}: ${withDiv.home_team?.name} vs ${withDiv.away_team?.name}</div>`
          : ''
      }
    </div>
    <div class="hex-top-list">
      <div class="panel-title">高频卦象</div>
      ${
        top
          .map(
            ([name, count]) =>
              `<div class="hex-top-row"><span>${name}</span><span class="mono">${count}</span></div>`
          )
          .join('') || '<div class="muted-text">暂无</div>'
      }
    </div>
  `;
}

function renderStarsChart() {
  const chartDom = document.getElementById('starsChart');
  if (!chartDom || !window.echarts) return;
  disposeChart('stars');
  const starCount = {};
  matches().forEach((m) => {
    (m.divination?.home_stars || []).forEach((s) => {
      const name = String(s).split(' ')[0];
      starCount[name] = (starCount[name] || 0) + 1;
    });
  });
  let top = Object.entries(starCount)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  if (!top.length) top = [['紫微', 0], ['天机', 0], ['太阳', 0], ['天同', 0], ['天府', 0], ['武曲', 0]];
  const max = Math.max(5, ...top.map(([, v]) => v));
  const chart = echarts.init(chartDom);
  state.charts.stars = chart;
  chart.setOption({
    backgroundColor: 'transparent',
    radar: {
      indicator: top.map(([name]) => ({ name, max })),
      axisName: { color: '#7A7A90' },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.08)' } },
      splitArea: {
        areaStyle: { color: ['rgba(108,99,255,0.04)', 'rgba(108,99,255,0.08)'] },
      },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: top.map(([, v]) => v),
            name: '主队星曜频次',
            areaStyle: { color: 'rgba(108,99,255,0.25)' },
            lineStyle: { color: '#6C63FF' },
            itemStyle: { color: '#22D3EE' },
          },
        ],
      },
    ],
  });
}

function renderFortuneChart() {
  const chartDom = document.getElementById('fortuneChart');
  if (!chartDom || !window.echarts) return;
  disposeChart('fortune');
  const byDate = {};
  matches().forEach((m) => {
    const d = m.beijing_date || (m.kickoff_at || '').slice(0, 10);
    const mod = m.divination?.home_modifier;
    if (!d || mod == null) return;
    (byDate[d] ||= []).push(Number(mod));
  });
  const dates = Object.keys(byDate).sort();
  const values = dates.map((d) => {
    const arr = byDate[d];
    return +(arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(2);
  });
  const chart = echarts.init(chartDom);
  state.charts.fortune = chart;
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 16, top: 24, bottom: 32 },
    xAxis: {
      type: 'category',
      data: dates.map((d) => d.slice(5)),
      axisLabel: { color: '#7A7A90' },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#7A7A90' },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
    },
    series: [
      {
        name: '主队气运修正均值',
        type: 'line',
        smooth: true,
        data: values,
        areaStyle: { opacity: 0.25 },
        itemStyle: { color: '#6C63FF' },
      },
    ],
  });
}

function renderAccuracyChart() {
  const chartDom = document.getElementById('accuracyChart');
  if (!chartDom || !window.echarts) return;
  disposeChart('overviewAcc');
  const chart = echarts.init(chartDom);
  state.charts.overviewAcc = chart;
  chart.setOption(lineTrendOption(state.data.trend || []));
}

window.addEventListener('resize', () => {
  Object.values(state.charts).forEach((c) => {
    try {
      c.resize();
    } catch {}
  });
});
