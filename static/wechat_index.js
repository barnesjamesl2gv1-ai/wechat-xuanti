/* 微信指数两轮验证 · 手绘画图
   目标：用户在 7 天 / 30 天两张图上，用三种颜色分别描出三个关键词的趋势，
   系统按坐标轴把笔迹换算成「时间—近似热度」数据，自动存草稿。 */
(function () {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const CHARTS = ['d7', 'd30'];
  const DAY = 86400000;

  const state = {
    ctx: null,
    task: null,
    round: 'round1',
    series: 0,
    nowMs: Date.now(),
    saveTimer: null,
    activeChart: 'd7',
    saving: false,
    drawing: null,
    jobActive: false,
    jobTimer: null,
  };

  const el = {};
  function cacheEls() {
    el.root = $('#index-verify');
    el.saving = $('#iv-saving');
    el.legend = $('#iv-legend');
    el.brushes = $('#iv-brushes');
    el.ymaxes = { d7: $('#iv-ymax-d7'), d30: $('#iv-ymax-d30') };
    el.periodSelect = $('#iv-period-select');
    el.status = $('#iv-status');
    el.submit = $('#iv-submit');
    el.regen = $('#iv-regen');
    el.progress = $('#iv-progress');
    el.analysis = $('#iv-analysis');
    el.final = $('#iv-final');
    el.rounds = Array.from(document.querySelectorAll('.iv-round'));
    el.canvases = { d7: $('#iv-canvas-d7'), d30: $('#iv-canvas-d30') };
    el.hints = { d7: $('#iv-hint-d7'), d30: $('#iv-hint-d30') };
  }

  /* ------------------------------------------------------------ 数据访问 */

  function roundData() {
    return (state.task && state.task.charts && state.task.charts[state.round]) || null;
  }

  function chartData(name) {
    const rnd = roundData();
    return (rnd && rnd.charts && rnd.charts[name]) || null;
  }

  function seriesOf(name) {
    const chart = chartData(name);
    return (chart && chart.series) || [];
  }

  function colors() {
    return (state.task && state.task.colors) || ['#2b4f9e', '#96690f', '#2f7d5b'];
  }

  function keywords() {
    const rnd = roundData();
    return (rnd && rnd.keywords) || [];
  }

  function currentKeyword() {
    return keywords()[state.series] || '';
  }

  /* ------------------------------------------------------------ 画布几何 */

  function geometry(canvas, days) {
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || 480;
    const height = canvas.clientHeight || 260;
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const pad = { left: 58, right: 14, top: 14, bottom: 28 };
    return {
      ctx, width, height, pad,
      plotW: Math.max(10, width - pad.left - pad.right),
      plotH: Math.max(10, height - pad.top - pad.bottom),
      t0: state.nowMs - days * DAY,
      t1: state.nowMs,
    };
  }

  function tsToX(g, ts) {
    return g.pad.left + ((ts - g.t0) / (g.t1 - g.t0)) * g.plotW;
  }
  function xToTs(g, x) {
    const ratio = (x - g.pad.left) / g.plotW;
    return Math.round(g.t0 + Math.max(0, Math.min(1, ratio)) * (g.t1 - g.t0));
  }
  function valueToY(g, value, yMax) {
    return g.pad.top + (1 - Math.max(0, Math.min(1, value / yMax))) * g.plotH;
  }
  function yToValue(g, y, yMax) {
    const ratio = 1 - (y - g.pad.top) / g.plotH;
    return Math.max(0, Math.min(1, ratio)) * yMax;
  }

  function compact(value) {
    if (value >= 10000) return `${(value / 10000).toFixed(value % 10000 === 0 ? 0 : 1)}万`;
    if (value >= 1000) return `${(value / 1000).toFixed(value % 1000 === 0 ? 0 : 1)}k`;
    return String(Math.round(value));
  }

  function dateLabel(ts, days) {
    const d = new Date(ts);
    return days > 10
      ? `${d.getMonth() + 1}/${d.getDate()}`
      : `${d.getMonth() + 1}/${d.getDate()}`;
  }

  /* ------------------------------------------------------------ 绘制 */

  function drawChart(name) {
    const canvas = el.canvases[name];
    if (!canvas) return;
    const days = name === 'd7' ? 7 : 30;
    const chart = chartData(name);
    const g = geometry(canvas, days);
    const { ctx } = g;
    const yMax = Number(chart && chart.y_max) || 0;

    ctx.clearRect(0, 0, g.width, g.height);

    // 网格
    ctx.strokeStyle = '#eeebe3';
    ctx.lineWidth = 1;
    ctx.font = '11px "Microsoft YaHei UI", system-ui, sans-serif';
    ctx.fillStyle = '#6b7280';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';

    const yTicks = 4;
    for (let i = 0; i <= yTicks; i += 1) {
      const y = g.pad.top + (g.plotH / 1) * (i / yTicks);
      ctx.beginPath();
      ctx.moveTo(g.pad.left, y);
      ctx.lineTo(g.pad.left + g.plotW, y);
      ctx.stroke();
      if (yMax > 0) {
        const value = yMax * (1 - i / yTicks);
        ctx.fillText(compact(value), g.pad.left - 8, y);
      }
    }

    const xTicks = days > 10 ? 6 : 7;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (let i = 0; i <= xTicks; i += 1) {
      const x = g.pad.left + (g.plotW / xTicks) * i;
      ctx.beginPath();
      ctx.moveTo(x, g.pad.top);
      ctx.lineTo(x, g.pad.top + g.plotH);
      ctx.stroke();
      const ts = g.t0 + ((g.t1 - g.t0) / xTicks) * i;
      ctx.fillText(dateLabel(ts, days), x, g.pad.top + g.plotH + 8);
    }

    // 轴框
    ctx.strokeStyle = '#e3dfd5';
    ctx.strokeRect(g.pad.left, g.pad.top, g.plotW, g.plotH);

    if (yMax <= 0) {
      ctx.fillStyle = '#6b7280';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.font = '13px "Microsoft YaHei UI", system-ui, sans-serif';
      ctx.fillText('先在右上填 Y 轴上限，再开始描线', g.pad.left + g.plotW / 2, g.pad.top + g.plotH / 2);
      return;
    }

    // 曲线
    const palette = colors();
    seriesOf(name).forEach((series, index) => {
      if (series.no_data) return;
      const active = index === state.series;
      ctx.strokeStyle = palette[index] || '#2b4f9e';
      ctx.lineWidth = active ? 2.4 : 1.8;
      ctx.globalAlpha = active ? 1 : 0.55;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      (series.strokes || []).forEach((stroke) => {
        if (!stroke.length) return;
        ctx.beginPath();
        stroke.forEach(([ts, value], pointIndex) => {
          if (ts < g.t0 || ts > g.t1) return;
          const x = tsToX(g, ts);
          const y = valueToY(g, value, yMax);
          if (pointIndex === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.stroke();
      });
      ctx.globalAlpha = 1;
    });

    // 正在画的那一笔：两张图共享同一份数据，所以同时跟手显示
    if (state.drawing && state.drawing.stroke.length) {
      ctx.strokeStyle = palette[state.drawing.series] || '#2b4f9e';
      ctx.lineWidth = 2.4;
      ctx.beginPath();
      state.drawing.stroke.forEach(([ts, value], i) => {
        const x = tsToX(g, ts);
        const y = valueToY(g, value, yMax);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
  }

  function redrawAll() {
    CHARTS.forEach(drawChart);
  }

  // 拖动过程中两张图一起刷新，用 rAF 合并重绘，避免每个 pointermove 都重画两次
  let redrawPending = false;
  function scheduleRedraw() {
    if (redrawPending) return;
    redrawPending = true;
    requestAnimationFrame(() => {
      redrawPending = false;
      redrawAll();
    });
  }

  /* ------------------------------------------------------------ 渲染 */

  // 7 天与 30 天是同一组曲线的两个视角：把两边合成一份数据，避免重复绘制
  function unifySeries() {
    if (!state.task || !state.task.charts) return;
    for (const rk of ['round1', 'round2']) {
      const rnd = state.task.charts[rk];
      if (!rnd || !rnd.charts) continue;
      const d7 = rnd.charts.d7;
      const d30 = rnd.charts.d30;
      if (!d7 || !d30 || !Array.isArray(d7.series)) continue;
      d7.series.forEach((s7, index) => {
        const s30 = (d30.series || [])[index] || {};
        if (s7 !== s30) {
          const base = (s30.points || []).length > (s7.points || []).length ? s30 : s7;
          s7.strokes = (base.strokes || []).map((stroke) => stroke.slice());
          s7.points = [].concat(...s7.strokes);
          s7.done = !!(s7.done || s30.done);
          s7.no_data = s7.points.length ? false : !!(s7.no_data || s30.no_data);
        }
        d30.series[index] = s7;
      });
    }
  }

  function chartWord(series) {
    if (!series) return '待画';
    if (series.done) return '已完成';
    if (series.no_data) return '无数据';
    return '待画';
  }

  function renderLegend() {
    const palette = colors();
    const words = keywords();
    const series = seriesOf('d7');
    el.legend.innerHTML = words.map((word, index) => {
      const item = series[index] || {};
      let note = '待画';
      if (item.no_data) note = '无指数数据';
      else if (item.done) note = '已完成';
      else if ((item.points || []).length) note = '已画，待完成';
      const cls = item.no_data ? 'is-missing' : (item.done ? 'is-done' : '');
      return `<span class="iv-legend-item ${cls}">
        <span class="iv-legend-dot" style="background:${palette[index] || '#2b4f9e'}"></span>
        ${escapeHtml(word)}<span class="iv-hint">· ${note}</span>
      </span>`;
    }).join('') || '<span class="iv-hint">这一轮还没有关键词</span>';
  }

  function markActiveChart() {
    CHARTS.forEach((name) => {
      const figure = el.canvases[name] && el.canvases[name].closest('.iv-chart');
      if (figure) figure.classList.toggle('is-active', name === state.activeChart);
    });
  }

  function renderBrushes() {
    const palette = colors();
    const words = keywords();
    el.brushes.innerHTML = words.map((word, index) => `
      <button type="button" class="iv-brush" data-series="${index}"
        aria-pressed="${index === state.series}">
        <span class="dot" style="background:${palette[index] || '#2b4f9e'}"></span>${escapeHtml(word)}
      </button>`).join('');
    el.brushes.querySelectorAll('.iv-brush').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.series = Number(btn.dataset.series) || 0;
        renderBrushes();
        renderLegend();
        redrawAll();
        updateYmax();
      });
    });
  }

  function updateYmax() {
    CHARTS.forEach((name) => {
      const input = el.ymaxes[name];
      const chart = chartData(name);
      if (input) input.value = (chart && chart.y_max) || '';
    });
  }

  function renderStatus() {
    const series = seriesOf('d7');
    const total = series.length || 3;
    const done = series.filter((s) => s && s.done).length;
    const missing = series.filter((s) => s && s.no_data).length;
    const word = currentKeyword();

    const parts = [`本轮 ${total} 条曲线：${done} 条已完成、${missing} 条标记无数据`];

    let tail = '';
    if (done + missing >= total && total > 0) {
      tail = '　都已完成，可以进入下一步。';
    } else if (word) {
      tail = `　当前画笔：${word}。画完一条点「完成当前曲线」。`;
    }
    tail += `　当前画布：${state.activeChart === 'd7' ? '7 天' : '30 天'}（两张图是同一组数据的两个视角，画一次两边都有）`;
    el.status.textContent = parts.join('　') + tail;
    el.status.classList.toggle('is-warn', false);
  }

  function renderRounds() {
    el.rounds.forEach((btn) => {
      btn.setAttribute('aria-selected', String(btn.dataset.round === state.round));
    });
  }

  function renderHints() {
    CHARTS.forEach((name) => {
      const chart = chartData(name);
      const days = name === 'd7' ? 7 : 30;
      const t0 = state.nowMs - days * DAY;
      let outside = 0;
      ((chart && chart.series) || []).forEach((series) => {
        (series.points || []).forEach(([ts]) => {
          if (ts < t0 || ts > state.nowMs) outside += 1;
        });
      });
      const yMax = Number(chart && chart.y_max) || 0;
      const bits = [];
      if (!yMax) bits.push('还没设 Y 轴上限');
      if (outside) bits.push(`${outside} 个旧数据点不在当前 ${days} 天范围`);
      el.hints[name].textContent = bits.join('；');
    });
  }

  function renderFinal() {
    if (!el.final) return;
    const final = (state.task && state.task.final) || {};
    const titles = final.titles || [];
    const generated = !!final.generated_at;

    if (!generated) {
      el.final.hidden = true;
      el.final.innerHTML = '';
      if (el.regen) el.regen.hidden = true;
      return;
    }

    if (el.regen) el.regen.hidden = false;

    const history = final.history || [];
    const earlier = history.length
      ? history.filter((item) => (item.titles || []).length).slice(-1)[0]
      : null;

    if (!titles.length) {
      el.final.hidden = false;
      const earlierHtml = earlier ? `
        <h4 style="margin-top:14px">上一次生成（${(earlier.titles || []).length} 个，已存档）</h4>
        <ul class="iv-title-list">${(earlier.titles || []).map((row, index) => `
          <li class="iv-title-item"><b>${index + 1}. ${escapeHtml(row.title)}</b>
          <div class="iv-title-meta">${escapeHtml(row.event_summary || '')}</div></li>`).join('')}</ul>` : '';
      el.final.innerHTML = `
        <h4>本次没有新的合格选题</h4>
        <p class="iv-title-meta">联网核查后没有找到可核实、且未被推荐过的事件。按设计宁缺毋滥，不编造、不重复已推荐事件。</p>
        ${earlierHtml}`;
      return;
    }

    const items = titles.map((row, index) => {
      const sources = (row.sources || []).slice(0, 3).map((s) => {
        let label = s.title || '';
        if (!label) {
          try { label = new URL(s.url).hostname.replace(/^www\./, ''); } catch (_) { label = '来源'; }
        }
        return `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
      }).join(' · ');
      return `<li class="iv-title-item">
        <b>${index + 1}. ${escapeHtml(row.title)}</b>
        <div class="iv-title-meta">${escapeHtml(row.keyword || '')}${row.angle ? `　${escapeHtml(row.angle)}` : ''}</div>
        <div class="iv-title-meta">${escapeHtml(row.event_summary || '')}</div>
        ${sources ? `<div class="iv-title-src">${sources}</div>` : ''}
      </li>`;
    }).join('');

    el.final.hidden = false;
    el.final.innerHTML = `
      <h4>本期选题（${titles.length} 个）</h4>
      <ul class="iv-title-list">${items}</ul>
      <p class="iv-title-meta">生成于 ${escapeHtml(final.generated_at || '')}${final.excel_appended ? `　已写入去重库 ${final.excel_appended} 条` : ''}${(final.usage && final.usage.total_tokens) ? `　${final.usage.total_tokens} Token` : ''}</p>`;
  }

  function regenerate() {
    const text = '会用已有的 4 张图重新联网搜索、核查并按事件去重，再生成一批标题（可能产生 Token 费用）。旧结果会保留。';
    if (window.ConfirmAsk) {
      window.ConfirmAsk('重新生成选题', text, () => runRegenerate());
    } else {
      runRegenerate();
    }
  }

  async function runRegenerate() {
    el.progress.textContent = '已提交，正在重新生成…';
    state.jobActive = true;
    renderSubmit();
    try {
      const res = await fetch('/api/index/regenerate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account: state.ctx.account, period: state.ctx.periodDir }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error((data && data.error) || '重新生成失败');
      pollJob('regen');
    } catch (err) {
      state.jobActive = false;
      renderSubmit();
      el.progress.textContent = String(err.message || err);
    }
  }

  function render() {
    if (!state.task) return;
    renderRounds();
    renderLegend();
    renderBrushes();
    updateYmax();
    renderHints();
    renderStatus();
    renderSubmit();
    renderAnalysis();
    renderFinal();
    redrawAll();
    markActiveChart();
  }

  // 完成度按本地数据实时算：保存响应不再回填 state.task，不能依赖服务端缓存的状态
  function roundComplete() {
    const series = seriesOf('d7');
    if (!series.length) return false;
    return series.every((s) => s && (s.done || s.no_data));
  }

  function renderSubmit() {
    if (!el.submit) return;
    const complete = roundComplete();
    const busy = !!state.jobActive;
    el.submit.disabled = !complete || busy;
    el.submit.textContent = '提交 AI 分析';
    el.submit.title = complete ? '' : '还有曲线没完成：每条都要点「完成当前曲线」或标记「无指数数据」';
  }

  function renderAnalysis() {
    if (!el.analysis) return;
    const rnd = roundData();
    let analysis = (rnd && rnd.analysis) || null;
    let label = state.round === 'round1' ? '第一轮分析结论' : '第二轮分析结论';

    // 第一轮的图与结论要保留供回看：切到第二轮时若本轮还没有结论，继续显示第一轮
    if (!analysis && state.round === 'round2') {
      const first = state.task && state.task.charts && state.task.charts.round1;
      if (first && first.analysis) {
        analysis = first.analysis;
        label = '第一轮结论（保留供第二轮参考）';
      }
    }

    if (!analysis) {
      el.analysis.hidden = true;
      el.analysis.innerHTML = '';
      return;
    }
    const trends = (analysis.per_keyword || []).map((row) => `
      <li class="iv-trend-item"><b>${escapeHtml(row.keyword)}</b>
        <span class="iv-trend-tag">${escapeHtml(row.trend || '')}</span>
        ${escapeHtml(row.reason || '')}</li>`).join('');
    const nexts = (analysis.next_keywords || []).map((row) => `
      <li>${escapeHtml(row.keyword)}</li>`).join('');
    const sources = (analysis.sources || []).slice(0, 8).map((row) => `
      <li><a href="${escapeHtml(row.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.title || row.url)}</a></li>`).join('');

    el.analysis.hidden = false;
    el.analysis.innerHTML = `
      <h4>${escapeHtml(label)}</h4>
      <p class="iv-trend-item">${escapeHtml(analysis.assessment || '')}</p>
      ${trends ? `<ul class="iv-trend-list">${trends}</ul>` : ''}
      ${nexts ? `<h4 style="margin-top:14px">第二轮关键词</h4><ul class="iv-next">${nexts}</ul>` : ''}
      ${sources ? `<h4 style="margin-top:14px">联网来源</h4><ul class="iv-sources">${sources}</ul>` : ''}
      ${(analysis.usage && analysis.usage.total_tokens) ? `<p class="iv-hint" style="margin-top:10px">本次调用 ${analysis.usage.total_tokens} Token</p>` : ''}
    `;
  }

  function jobParams(stage) {
    const params = new URLSearchParams({
      account: state.ctx.account,
      period: state.ctx.periodDir,
      stage,
    });
    return params.toString();
  }

  function submitRound() {
    if (!roundComplete()) return;
    const isRound1 = state.round === 'round1';
    const text = isRound1
      ? '提交后会联网检索近期资讯，并调用一次 DeepSeek（可能产生 Token 费用），然后给出 3 个全新的第二轮关键词。'
      : '提交后会联网搜索真实事件、打开原文核查并按事件去重，最多给出 3 个标题（可能产生 Token 费用）。';
    if (window.ConfirmAsk) {
      window.ConfirmAsk(isRound1 ? '提交第一轮分析' : '提交第二轮分析', text,
        () => runSubmit(isRound1));
    } else {
      runSubmit(isRound1);
    }
  }

  async function runSubmit(isRound1) {
    const stage = isRound1 ? 'round1' : 'round2';
    el.progress.textContent = '已提交，正在执行…';
    state.jobActive = true;
    renderSubmit();
    try {
      const res = await fetch(`/api/index/submit-${stage}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account: state.ctx.account, period: state.ctx.periodDir }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error((data && data.error) || '提交失败');
      pollJob(stage);
    } catch (err) {
      state.jobActive = false;
      renderSubmit();
      el.progress.textContent = String(err.message || err);
    }
  }

  function pollJob(stage) {
    clearInterval(state.jobTimer);
    state.jobTimer = setInterval(async () => {
      try {
        const res = await fetch(`/api/index/job?${jobParams(stage)}`);
        const data = await res.json();
        const job = (data && data.job) || {};
        if (job.status === 'queued' || job.status === 'running') {
          el.progress.textContent = job.detail || job.step || '执行中…';
          return;
        }
        clearInterval(state.jobTimer);
        state.jobActive = false;
        if (job.status === 'done' && job.result) {
          state.task = job.result;
          el.progress.textContent = '已完成';
          unifySeries();
          if (stage === 'round1') {
            state.round = 'round2';
            state.series = 0;
          }
          render();
          fillPeriods(state.ctx, state.ctx.periodDir);
        } else {
          el.progress.textContent = `失败：${job.error || '未知原因'}`;
          renderSubmit();
        }
      } catch (err) {
        clearInterval(state.jobTimer);
        state.jobActive = false;
        renderSubmit();
        el.progress.textContent = '读取进度失败，请刷新页面查看结果';
      }
    }, 1500);
  }

  async function fillPeriods(ctx, current) {
    if (!el.periodSelect) return;
    const params = new URLSearchParams({ account: ctx.account });
    if (ctx.sandbox) params.set('sandbox', '1');
    try {
      const res = await fetch(`/api/index/tasks?${params.toString()}`);
      const data = await res.json();
      if (!res.ok || !data.ok) return;
      const rows = data.tasks || [];
      el.periodSelect.innerHTML = rows.map((row) => {
        const p = row.period || {};
        const span = p.start && p.end
          ? `${String(p.start).slice(0, 10)} ~ ${String(p.end).slice(0, 10)}`
          : row.period_dir;
        return `<option value="${escapeHtml(row.period_dir)}">${escapeHtml(`${span} · ${row.stage || ''}`)}</option>`;
      }).join('');
      if (current) el.periodSelect.value = current;
      el.periodSelect.onchange = () => {
        const next = el.periodSelect.value;
        if (next) open({ ...ctx, periodDir: next });
      };
    } catch (_) {
      /* 周期列表失败不影响当前任务使用 */
    }
  }

  /* ------------------------------------------------------------ 保存 */

  // 两张图是同一组曲线的两个视角：任何改动都要把两张图一起写回
  function scheduleSave() {
    el.saving.textContent = '改动待保存…';
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(saveAll, 700);
  }

  async function saveAll() {
    if (!state.ctx || !state.task) return;
    state.saving = true;
    el.saving.textContent = '正在保存…';
    try {
      let savedAt = '';
      for (const name of CHARTS) {
        const chart = chartData(name);
        if (!chart) continue;
        const res = await fetch('/api/index/draft', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            account: state.ctx.account,
            period: state.ctx.periodDir,
            round: state.round,
            chart: name,
            chart_data: {
              y_max: chart.y_max,
              series: chart.series.map((s) => ({
                strokes: s.strokes || [],
                done: !!s.done,
                no_data: !!s.no_data,
              })),
            },
          }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error((data && data.error) || '保存失败');
        savedAt = data.saved_at || savedAt;
      }
      // 不用响应覆盖 state.task：避免把未合并的改动冲掉
      el.saving.textContent = `已保存 ${String(savedAt || '').slice(11)}`;
      renderLegend();
      renderStatus();
      renderSubmit();
      renderHints();
    } catch (err) {
      el.saving.textContent = '保存失败，请检查服务是否在运行';
    } finally {
      state.saving = false;
    }
  }

  /* ------------------------------------------------------------ 交互 */

  function bindCanvas(name) {
    const canvas = el.canvases[name];
    if (!canvas) return;
    const days = name === 'd7' ? 7 : 30;

    const pointFromEvent = (event) => {
      const rect = canvas.getBoundingClientRect();
      const g = geometry(canvas, days);
      const chart = chartData(name);
      const yMax = Number(chart && chart.y_max) || 0;
      if (yMax <= 0) return null;
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      if (x < g.pad.left || x > g.pad.left + g.plotW) return null;
      if (y < g.pad.top || y > g.pad.top + g.plotH) return null;
      return [xToTs(g, x), Math.round(yToValue(g, y, yMax))];
    };

    canvas.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return; // 只响应鼠标左键
      state.activeChart = name;
      markActiveChart();
      const point = pointFromEvent(event);
      if (!point) {
        if (!(Number((chartData(name) || {}).y_max) > 0)) {
          el.status.textContent = '先在「Y 轴上限」里填一个数值，再开始描线。';
          el.status.classList.add('is-warn');
        }
        return;
      }
      canvas.setPointerCapture(event.pointerId);
      state.drawing = { chart: name, series: state.series, stroke: [point] };
      scheduleRedraw();
    });

    canvas.addEventListener('pointermove', (event) => {
      if (!state.drawing || state.drawing.chart !== name) return;
      const point = pointFromEvent(event);
      if (!point) return;
      const stroke = state.drawing.stroke;
      const last = stroke[stroke.length - 1];
      if (last && Math.abs(last[0] - point[0]) < 60000 && Math.abs(last[1] - point[1]) < 20) return;
      stroke.push(point);
      scheduleRedraw();
    });

    const finish = (event) => {
      if (!state.drawing || state.drawing.chart !== name) return;
      const stroke = state.drawing.stroke;
      state.drawing = null;
      try { canvas.releasePointerCapture(event.pointerId); } catch (_) {}
      if (stroke.length < 2) { redrawAll(); return; }
      const series = seriesOf(name)[state.series];
      if (!series) return;
      series.strokes = series.strokes || [];
      series.strokes.push(stroke);
      series.points = (series.points || []).concat(stroke);
      series.no_data = false;
      scheduleSave();
      renderLegend();
      renderStatus();
      renderSubmit();
      renderHints();
      redrawAll();
    };
    canvas.addEventListener('pointerup', finish);
    canvas.addEventListener('pointercancel', finish);
  }

  function bindTools() {
    CHARTS.forEach((name) => {
      const input = el.ymaxes[name];
      if (!input) return;
      input.addEventListener('change', () => {
        const chart = chartData(name);
        const raw = Number(input.value);
        if (chart) chart.y_max = raw > 0 ? raw : null;
        renderHints();
        drawChart(name);
        scheduleSave();
      });
    });

    const activeSeries = () => seriesOf('d7')[state.series];

    $('#iv-undo').addEventListener('click', () => {
      const series = activeSeries();
      if (!series) return;
      if ((series.strokes || []).length) {
        series.strokes.pop();
        series.points = [].concat(...series.strokes);
        series.done = false;
      }
      renderLegend();
      renderStatus();
      renderSubmit();
      redrawAll();
      scheduleSave();
    });

    $('#iv-clear').addEventListener('click', () => {
      const series = activeSeries();
      if (!series) return;
      series.strokes = [];
      series.points = [];
      series.done = false;
      renderLegend();
      renderStatus();
      renderSubmit();
      redrawAll();
      scheduleSave();
    });

    $('#iv-done').addEventListener('click', () => {
      const series = activeSeries();
      if (!series) return;
      if ((series.points || []).length >= 2) series.done = true;
      const next = keywords().findIndex((_, index) => {
        const s = seriesOf('d7')[index] || {};
        return !s.done && !s.no_data;
      });
      if (next >= 0) state.series = next;
      renderLegend();
      renderBrushes();
      updateYmax();
      renderStatus();
      renderSubmit();
      redrawAll();
      scheduleSave();
    });

    $('#iv-nodata').addEventListener('click', () => {
      const series = activeSeries();
      if (!series) return;
      series.no_data = !series.no_data;
      if (series.no_data) {
        series.strokes = [];
        series.points = [];
        series.done = false;
      }
      renderLegend();
      renderStatus();
      renderSubmit();
      redrawAll();
      scheduleSave();
    });

    el.rounds.forEach((btn) => {
      btn.addEventListener('click', () => {
        state.round = btn.dataset.round;
        state.series = 0;
        render();
      });
    });

    if (el.submit) el.submit.addEventListener('click', submitRound);
    if (el.regen) el.regen.addEventListener('click', regenerate);

    window.addEventListener('resize', redrawAll);
  }

  async function resumeJob() {
    for (const stage of ['round1', 'round2']) {
      try {
        const res = await fetch(`/api/index/job?${jobParams(stage)}`);
        const data = await res.json();
        const job = (data && data.job) || {};
        if (job.status === 'queued' || job.status === 'running') {
          state.jobActive = true;
          renderSubmit();
          el.progress.textContent = job.detail || '继续执行中…';
          pollJob(stage);
          return;
        }
      } catch (_) {
        /* 忽略：不影响当前任务 */
      }
    }
  }

  /* ------------------------------------------------------------ 打开 */

  async function open(ctx) {
    if (!el.root) cacheEls();
    state.ctx = ctx;
    state.round = 'round1';
    state.series = 0;
    el.root.hidden = false;
    el.saving.textContent = '正在读取任务…';
    try {
      const params = new URLSearchParams({
        account: ctx.account,
        period: ctx.periodDir,
      });
      if (ctx.sandbox) params.set('sandbox', '1');
      const res = await fetch(`/api/index/task?${params.toString()}`);
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error((data && data.error) || '读取任务失败');
      state.task = data.task;
      state.nowMs = data.now_ms || Date.now();
      unifySeries();
      el.saving.textContent = '已读取';
      render();
      requestAnimationFrame(redrawAll);
      fillPeriods(ctx, ctx.periodDir);
      resumeJob();
    } catch (err) {
      el.saving.textContent = '';
      el.status.textContent = String(err.message || err);
      el.status.classList.add('is-warn');
    }
  }

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  document.addEventListener('DOMContentLoaded', () => {
    cacheEls();
    if (!el.root) return;
    bindCanvas('d7');
    bindCanvas('d30');
    bindTools();
  });

  window.IndexVerify = { open };
})();
