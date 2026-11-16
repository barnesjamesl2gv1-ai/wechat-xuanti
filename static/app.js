/* 公众号爆款选题系统 · 前端交互
   只负责取数、渲染和状态反馈；判断规则一律来自后端脚本。 */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    status: null,
    groups: { 历史总报告: [], 三天周期: [], 炒冷饭: [], AI报告: [] },
    activeTab: 'AI报告',
    activeReport: null,
    topics: null,
    activeAiId: null,
    busy: false,
    entered: false,
    sandbox: false,
  };

  let confirmAction = null;

  const STATE_CLASS = {
    '可分析': 't-ready',
    '已分析': 't-ok',
    '未到期': 't-skip',
    '待检查': 't-skip',
    '无材料/已跳过': 't-skip',
    '已停用': 't-skip',
    '异常': 't-bad',
    '未配置 Key': 't-warn',
  };

  /* ------------------------------------------------------------ 工具 */

  function esc(text) {
    return String(text ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function toast(message, isError = false) {
    const el = $('#toast');
    el.textContent = message;
    el.classList.toggle('is-error', isError);
    el.hidden = false;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { el.hidden = true; }, 4200);
  }

  async function api(url, options) {
    const res = await fetch(url, options);
    let data = null;
    try { data = await res.json(); } catch (_) { data = null; }
    if (!res.ok) {
      throw new Error((data && data.error) || `请求失败（${res.status}）`);
    }
    return data;
  }

  function when(text) {
    if (!text) return '';
    return String(text).replace(/:\d{2}$/, '');
  }

  /* ------------------------------------------------------------ 极简 Markdown */

  function inline(text) {
    return text
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }

  function mdToHtml(raw) {
    const lines = esc(raw).replace(/\r\n/g, '\n').split('\n');
    const out = [];
    let list = null;
    let table = null;

    const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
    const closeTable = () => { if (table) { out.push('</tbody></table>'); table = null; } };

    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      const trimmed = line.trim();

      if (!trimmed) { closeList(); closeTable(); continue; }

      const heading = trimmed.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        closeList(); closeTable();
        const level = Math.min(heading[1].length, 3);
        out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
        continue;
      }

      if (/^(-{3,}|\*{3,})$/.test(trimmed)) {
        closeList(); closeTable();
        out.push('<hr>');
        continue;
      }

      if (trimmed.startsWith('|') && lines[i + 1] && /^\|[\s:-|]+\|$/.test(lines[i + 1].trim())) {
        closeList(); closeTable();
        const cells = (row) => row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
        out.push('<table><thead><tr>');
        cells(trimmed).forEach((c) => out.push(`<th>${inline(c)}</th>`));
        out.push('</tr></thead><tbody>');
        table = true;
        i += 1;
        continue;
      }

      if (table && trimmed.startsWith('|')) {
        const cells = trimmed.replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
        out.push('<tr>');
        cells.forEach((c) => out.push(`<td>${inline(c)}</td>`));
        out.push('</tr>');
        continue;
      }
      closeTable();

      const quote = trimmed.match(/^>\s?(.*)$/);
      if (quote) {
        closeList();
        out.push(`<blockquote>${inline(quote[1])}</blockquote>`);
        continue;
      }

      const bullet = trimmed.match(/^[-*]\s+(.*)$/);
      if (bullet) {
        if (list !== 'ul') { closeList(); out.push('<ul>'); list = 'ul'; }
        out.push(`<li>${inline(bullet[1])}</li>`);
        continue;
      }

      const ordered = trimmed.match(/^\d+\.\s+(.*)$/);
      if (ordered) {
        if (list !== 'ol') { closeList(); out.push('<ol>'); list = 'ol'; }
        out.push(`<li>${inline(ordered[1])}</li>`);
        continue;
      }

      closeList();
      out.push(`<p>${inline(trimmed)}</p>`);
    }
    closeList(); closeTable();
    return out.join('\n');
  }

  /* ------------------------------------------------------------ 账号组合 */

  function renderPairs() {
    const list = $('#pair-list');
    const pairs = (state.status && state.status.pairs) || [];
    $('#accounts-sub').textContent = pairs.length
      ? '自有账号与对标账号一对一绑定，各自独立计算周期'
      : '还没有配置账号组合';

    if (!pairs.length) {
      list.innerHTML = `<div class="pair-empty">
        还没有启用任何账号组合。点右上角“管理账号”填写自有账号与对标账号。
      </div>`;
      return;
    }

    list.innerHTML = pairs.map((pair, index) => {
      const cls = STATE_CLASS[pair.state] || 't-skip';
      const period = pair.period
        ? `${when(pair.period.period_start)} ~ ${when(pair.period.period_end)}`
        : (pair.preview_period || '还没有完成的三天周期');
      const counts = [
        pair.own_articles != null ? `本期自有文章 ${pair.own_articles} 篇` : null,
        pair.benchmark_viral != null ? `对标爆款 ${pair.benchmark_viral} 篇` : null,
      ].filter(Boolean).join('　');
      const quiet = ['未到期', '已分析', '已停用'].includes(pair.state);
      const hint = !quiet && pair.state_hint ? `<div class="pair-hint">${esc(pair.state_hint)}</div>` : '';
      return `<article class="pair ${pair.enabled ? '' : 'is-disabled'} rise" style="--i:${index}">
        <div>
          <div class="pair-names">
            <span class="pair-own">${esc(pair.own_account)}</span>
            <span class="pair-arrow" aria-hidden="true">→</span>
            <span class="pair-bench">${esc(pair.benchmark_account || '未填写对标账号')}</span>
            <span class="tag">${pair.account_type === 'new' ? '新账号' : '老账号'}</span>
            ${pair.enabled ? '' : '<span class="tag">已停用</span>'}
          </div>
          <div class="pair-meta">
            <span>最近周期：${esc(period)}</span>
            ${counts ? `<span>${esc(counts)}</span>` : ''}
          </div>
          ${hint}
        </div>
        <div class="pair-side">
          <span class="status ${cls}">${esc(pair.state_label || pair.state)}</span>
          ${pair.ai ? `<button type="button" class="btn btn-quiet" data-open-ai="${esc(pair.ai.id)}">看报告</button>` : ''}
        </div>
      </article>`;
    }).join('');

    $$('[data-open-ai]', list).forEach((btn) => {
      btn.addEventListener('click', () => openAiReport(btn.getAttribute('data-open-ai')));
    });
  }

  /* ------------------------------------------------------------ 选题方向 */

  function candidateHtml(item, index) {
    return `<article class="candidate rise" style="--i:${index + 1}">
      <div class="candidate-no">方向 ${String(index + 1).padStart(2, '0')}</div>
      <h3 class="candidate-direction">${esc(item.direction)}</h3>
      <span class="candidate-keyword">${esc(item.keyword)}</span>
      <p class="candidate-reason">${esc(item.reason)}</p>
    </article>`;
  }

  function disclosureHtml(label, text) {
    if (!text) return '';
    return `<div class="disclosure">
      <button type="button" class="disclosure-btn" aria-expanded="false">
        ${esc(label)}<span class="caret" aria-hidden="true">▶</span>
      </button>
      <div class="disclosure-body"><div>${esc(text)}</div></div>
    </div>`;
  }

  function renderTopics() {
    const body = $('#topic-body');
    const data = state.topics;
    if (!data) {
      body.innerHTML = `<div class="empty">
        <p class="empty-title">还没有可展示的选题方向</p>
        <p class="empty-text">点「开始 AI 分析」后，系统会把本周期自有表现和同期对标爆款交给模型，<br>固定返回三组方向、关键词和推荐依据。</p>
        <button type="button" class="btn btn-primary" id="empty-run" style="margin-top:16px">开始 AI 分析</button>
      </div>`;
      $('#topics-sub').textContent = '';
      const run = $('#empty-run');
      if (run) run.addEventListener('click', promptRunAi);
      return;
    }

    const analysis = data.analysis || {};
    const pair = data.pair || {};
    const period = data.period || {};
    const counts = data.data_counts || {};
    const usage = data.usage || {};
    const candidates = Array.isArray(analysis.candidates) ? analysis.candidates : [];

    $('#topics-sub').textContent = data.created_at ? `生成于 ${when(data.created_at)}` : '';

    body.innerHTML = `
      <div class="topic-head">
        <span class="who">${esc(pair.own_account || '')}</span>
        <span class="pair-arrow" aria-hidden="true">→</span>
        <span class="period">${esc(pair.benchmark_account || '')}</span>
        <span class="period">${esc(when(period.start))} ~ ${esc(when(period.end))}</span>
        <span class="counts">自有 ${counts.own_articles ?? '—'} 篇 · 对标爆款 ${counts.benchmark_viral ?? '—'} 篇 · ${esc(data.model && data.model.name || '')}</span>
      </div>
      <div class="candidates">${candidates.map(candidateHtml).join('')}</div>
      <div class="topic-foot">
        <span aria-hidden="true">※</span>
        <span><b>这三组是候选方向，不是文章标题。</b>关键词还需要你到微信指数做三轮人工验证后再决定用不用。</span>
      </div>
      <div class="topic-actions">
        <button type="button" class="btn" id="iv-open">开始微信指数验证</button>
      </div>
      ${disclosureHtml('本轮综合分析', analysis.overall_assessment)}
      ${disclosureHtml('对标爆款观察', analysis.benchmark_findings)}
      ${(analysis.strategy_reviews || []).length ? disclosureHtml('上一轮策略复盘', (analysis.strategy_reviews || []).map((r) => `${r.direction || ''}：${r.verdict || r.review || r.reason || ''}`).join('\n')) : ''}
      ${disclosureHtml('下一周期总体策略', analysis.next_strategy)}
      ${disclosureHtml('长期表现参考', analysis.long_term_context)}
      ${usage.total_tokens ? `<p class="panel-sub" style="margin-top:14px">本次调用 ${usage.total_tokens} Token（输入 ${usage.prompt_tokens ?? '—'}，输出 ${usage.completion_tokens ?? '—'}）</p>` : ''}
    `;

    $$('.disclosure-btn', body).forEach((btn) => {
      btn.addEventListener('click', () => {
        const wrap = btn.closest('.disclosure');
        const open = wrap.classList.toggle('is-open');
        btn.setAttribute('aria-expanded', String(open));
        const panel = wrap.querySelector('.disclosure-body');
        if (open) panel.style.maxHeight = `${panel.scrollHeight}px`;
        else panel.style.maxHeight = '0';
      });
    });

    const indexBtn = $('#iv-open');
    if (indexBtn) {
      indexBtn.addEventListener('click', () => {
        if (!window.IndexVerify) return;
        const ctx = indexContext(data);
        if (!ctx.account || !ctx.periodDir) {
          toast('这一期缺少账号或周期信息，无法开始验证', true);
          return;
        }
        window.IndexVerify.open(ctx);
        const panel = $('#index-verify');
        if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }
  }

  function indexContext(data) {
    const pair = data.pair || {};
    const period = data.period || {};
    const account = pair.own_account || '';
    let periodDir = '';
    let sandbox = false;

    const id = state.activeAiId || '';
    if (id) {
      const parts = id.split('/');
      // .../<账号>/<周期目录>/分析结果.json → 周期目录是倒数第二段
      if (parts.length >= 3) periodDir = parts[parts.length - 2];
      sandbox = id.indexOf('_AI联调沙盒') >= 0;
    }
    if (!periodDir && period.start && period.end) {
      const stamp = (value) => {
        const m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/);
        return m ? `${m[1]}${m[2]}${m[3]}_${m[4]}${m[5]}${m[6]}` : '';
      };
      periodDir = `${stamp(period.start)}__${stamp(period.end)}`;
    }
    return { account, periodDir, sandbox };
  }

  function fillPicker(entries) {
    const picker = $('#topic-picker');
    if (!entries.length) { picker.hidden = true; return; }
    picker.hidden = false;
    const sel = $('#sel-account');
    const accounts = [...new Set(entries.map((item) => item.account || '未知账号'))];
    sel.innerHTML = accounts.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join('');
    sel.value = state.topics && state.topics.pair ? state.topics.pair.own_account : accounts[0];
    syncPeriods(entries);
    sel.onchange = () => syncPeriods(entries);
  }

  function compactPeriod(label) {
    const match = String(label).match(/(\d{4})-(\d{2})-(\d{2})\s*~\s*(\d{4})-(\d{2})-(\d{2})/);
    return match ? `${match[2]}-${match[3]} ~ ${match[5]}-${match[6]}` : String(label);
  }

  function syncPeriods(entries) {
    const account = $('#sel-account').value;
    const filtered = entries.filter((item) => (item.account || '未知账号') === account);
    const sel = $('#sel-period');
    sel.innerHTML = filtered.map((item) => {
      const label = compactPeriod(item.period || item.title);
      return `<option value="${esc(item.id)}">${esc(label)}</option>`;
    }).join('');
    const current = state.activeReportId;
    if (current && filtered.some((item) => item.id === current)) sel.value = current;
    sel.onchange = () => loadAiResult(sel.value);
    loadAiResult(sel.value);
  }

  async function loadAiResult(reportId) {
    if (!reportId) return;
    const jsonId = reportId.replace(/分析报告\.md$/, '分析结果.json');
    try {
      const data = await api(`/api/report/${jsonId.split('/').map(encodeURIComponent).join('/')}`);
      if (data.kind === 'json') {
        state.activeAiId = jsonId;
        state.topics = JSON.parse(data.content);
        renderTopics();
      }
    } catch (err) {
      toast(err.message, true);
    }
  }

  function openAiReport(reportId) {
    const jsonId = reportId.replace(/分析结果\.json$/, '分析报告.md');
    setTab('AI报告');
    const found = (state.groups['AI报告'] || []).find((item) => item.id === jsonId);
    if (found) openReport(found.id);
    else openReport(jsonId);
  }

  /* ------------------------------------------------------------ 报告中心 */

  const TAB_NAMES = ['AI报告', '三天周期', '历史总报告', '炒冷饭'];

  function renderTabs() {
    const tabs = $('#report-tabs');
    tabs.innerHTML = `<span class="tab-pill" aria-hidden="true"></span>` +
      TAB_NAMES.map((name) => {
        const count = (state.groups[name] || []).length;
        return `<button type="button" class="tab" role="tab" data-tab="${name}"
          aria-selected="${name === state.activeTab}">${name}${count ? ` ${count}` : ''}</button>`;
      }).join('');

    $$('.tab', tabs).forEach((btn) => {
      btn.addEventListener('click', () => setTab(btn.dataset.tab));
    });
    requestAnimationFrame(movePill);
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(movePill);
    }
  }

  function movePill() {
    const active = $('.tab[aria-selected="true"]');
    const pill = $('.tab-pill');
    if (!active || !pill) return;
    pill.style.width = `${active.offsetWidth}px`;
    pill.style.transform = `translateX(${active.offsetLeft - 4}px)`;
  }

  function setTab(name) {
    state.activeTab = name;
    $$('.tab').forEach((btn) => btn.setAttribute('aria-selected', String(btn.dataset.tab === name)));
    movePill();
    renderReportList();
  }

  function renderReportList() {
    const items = state.groups[state.activeTab] || [];
    const list = $('#report-list');
    if (!items.length) {
      list.innerHTML = `<li><div class="pair-empty" style="border:0;background:none">
        还没有${esc(state.activeTab)}。<br>先点“更新本地数据”或“开始 AI 分析”生成。</div></li>`;
      return;
    }
    list.innerHTML = items.map((item) => {
      const meta = [item.account, item.period].filter(Boolean).join(' · ');
      return `<li><button type="button" class="report-item" data-id="${esc(item.id)}"
        aria-current="${item.id === state.activeReport}">
        <span class="rt">${esc(item.title)}</span>
        ${meta ? `<span class="rm">${esc(meta)}</span>` : ''}
      </button></li>`;
    }).join('');

    $$('.report-item', list).forEach((btn) => {
      btn.addEventListener('click', () => openReport(btn.dataset.id));
    });
  }

  async function openReport(id) {
    state.activeReport = id;
    $$('.report-item').forEach((btn) => btn.setAttribute('aria-current', String(btn.dataset.id === id)));
    const reader = $('#report-reader');
    reader.classList.add('is-loading');
    try {
      const data = await api(`/api/report/${id.split('/').map(encodeURIComponent).join('/')}`);
      if (data.kind === 'json') {
        reader.innerHTML = `<div class="fade-in"><pre class="log" style="max-height:none">${esc(data.content)}</pre></div>`;
      } else {
        reader.innerHTML = `<div class="md fade-in">${mdToHtml(data.content)}</div>`;
      }
      reader.scrollTop = 0;
    } catch (err) {
      reader.innerHTML = `<p class="reader-empty">${esc(err.message)}</p>`;
      toast(err.message, true);
    } finally {
      reader.classList.remove('is-loading');
    }
  }

  /* ------------------------------------------------------------ 执行结果 */

  function renderResult(payload) {
    const body = $('#result-body');
    const log = $('#result-log');
    const last = state.status && state.status.last;

    if (!payload && (!last || !last.action)) {
      body.innerHTML = `<p class="hint-line">点上面的按钮开始。三个账号各自判断周期，未到期的会正常跳过。</p>`;
      $('#result-sub').textContent = '还没有执行过操作';
      $('#btn-log').hidden = true;
      return;
    }

    const data = payload || last;
    const summary = data.summary;
    const labels = { 'update-data': '更新本地数据', 'ai-preview': '预览 AI 分析', 'run-ai': '开始 AI 分析' };
    const action = labels[data.action] || data.action || '操作';

    $('#result-sub').textContent = last && last.at ? `上次执行：${when(last.at)}` : '';

    if (summary) {
      body.innerHTML = `<div class="result-line">
        <span class="result-stat ok"><b>${summary.done ?? 0}</b><span>成功</span></span>
        <span class="result-stat skip"><b>${summary.skip ?? 0}</b><span>正常跳过</span></span>
        <span class="result-stat bad"><b>${summary.bad ?? 0}</b><span>异常</span></span>
        <span class="result-meta">${esc(action)}${data.duration ? ` · ${data.duration} 秒` : ''}</span>
      </div>`;
    } else {
      const ok = data.ok !== false;
      const stats = (data.stats && data.stats.length)
        ? `<div class="update-stats">${data.stats.map((s) => `<span class="tag">${esc(s)}</span>`).join('')}</div>`
        : '';
      body.innerHTML = `<div class="result-line">
        <span class="status ${ok ? 't-ok' : 't-bad'}">${ok ? '执行成功' : '执行失败'}</span>
        <span class="result-meta">${esc(action)}${data.duration ? ` · ${data.duration} 秒` : ''}</span>
      </div>${stats}`;
    }

    if (data.error) {
      body.insertAdjacentHTML('beforeend',
        `<p class="panel-sub" style="margin-top:10px;color:var(--bad)">${esc(data.error)}</p>`);
    }

    const lines = data.lines || [];
    log.textContent = lines.join('\n');
    log.hidden = true;
    $('#btn-log').hidden = !lines.length;
    $('#btn-log').textContent = '查看日志';
  }

  /* ------------------------------------------------------------ 操作 */

  function setBusy(busy) {
    state.busy = busy;
    ['#btn-update', '#btn-preview', '#btn-run', '#btn-manage'].forEach((sel) => {
      const btn = $(sel);
      if (btn) btn.disabled = busy;
    });
    if (busy) {
      $$('#btn-update, #btn-preview, #btn-run').forEach((btn) => {
        if (btn.disabled) btn.classList.add('is-busy');
      });
    } else {
      $$('.btn').forEach((btn) => btn.classList.remove('is-busy'));
    }
  }

  async function doAction(button, url, successText) {
    if (state.busy) return;
    setBusy(true);
    const btn = $(button);
    btn.classList.add('is-busy');
    try {
      const data = await api(url, { method: 'POST' });
      renderResult(data);
      toast(data.ok ? successText : (data.error || '执行失败'), !data.ok);
      await refresh({ quiet: true });
      if (data.rows && data.rows.length) {
        renderPreviewRows(data.rows);
      }
      if (url === '/api/run-ai') {
        await loadGroups();
        await reloadTopics();
      }
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
      btn.classList.remove('is-busy');
    }
  }

  function renderPreviewRows(rows) {
    if (!rows.length) return;
    const body = $('#result-body');
    const items = rows.map((row) => {
      const cls = STATE_CLASS[row.status] || 't-skip';
      return `<div class="pair" style="grid-template-columns:0.9fr 1.6fr;margin-top:10px">
        <div><span class="pair-own">${esc(row.account)}</span></div>
        <div>
          <span class="status ${cls}">${esc(row.status === '跳过' ? '正常跳过' : row.status)}</span>
          <div class="pair-hint" style="text-align:left;max-width:none">${esc(row.detail)}</div>
        </div>
      </div>`;
    }).join('');
    body.insertAdjacentHTML('beforeend', items);
  }

  /* ------------------------------------------------------------ 取数 */

  async function refresh({ quiet = false } = {}) {
    try {
      state.status = await api('/api/status');
      renderPairs();
      renderResult();
      if (!state.topics && state.status.latest_ai) {
        state.topics = state.status.latest_ai.data;
        state.activeAiId = state.status.latest_ai.id || null;
      }
      renderTopics();
      if (!quiet) {
        await loadGroups();
      }
      playEntrance();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function loadGroups() {
    const data = await api(`/api/reports${state.sandbox ? '?sandbox=1' : ''}`);
    state.groups = data.groups;
    if (!(state.groups[state.activeTab] || []).length) {
      const first = TAB_NAMES.find((name) => (state.groups[name] || []).length);
      if (first) state.activeTab = first;
    }
    renderTabs();
    renderReportList();
    const entries = state.groups['AI报告'] || [];
    fillPicker(entries);
  }

  async function reloadTopics() {
    const entries = state.groups['AI报告'] || [];
    if (!entries.length) { state.topics = null; renderTopics(); return; }
    await loadAiResult($('#sel-period') ? $('#sel-period').value : entries[0].id);
  }

  function playEntrance() {
    if (state.entered) return;
    state.entered = true;
    $$('.panel').forEach((panel, index) => {
      panel.classList.add('rise');
      panel.style.setProperty('--i', String(index + 1));
    });
  }

  /* ------------------------------------------------------------ 账号管理 */

  function openPairsDialog() {
    const editor = $('#pairs-editor');
    const pairs = (state.status && state.status.pairs) || [];
    const rows = pairs.length ? pairs : [{ own_account: '', benchmark_account: '', account_type: 'old', enabled: true }];

    editor.innerHTML = rows.map((pair, index) => `
      <div class="pair-editor" data-index="${index}">
        <div class="row-index">第 ${index + 1} 组</div>
        <div class="field"><label>自有账号</label>
          <input type="text" name="own" value="${esc(pair.own_account || '')}" placeholder="与归档名称一致"></div>
        <div class="field"><label>对标账号</label>
          <input type="text" name="bench" value="${esc(pair.benchmark_account || '')}" placeholder="与归档名称一致"></div>
        <div class="field"><label>类型</label>
          <select name="type">
            <option value="old" ${pair.account_type === 'old' ? 'selected' : ''}>老账号</option>
            <option value="new" ${pair.account_type === 'new' ? 'selected' : ''}>新账号</option>
          </select></div>
        <div class="field"><label>启用</label>
          <div class="toggle"><input type="checkbox" name="enabled" ${pair.enabled ? 'checked' : ''}></div></div>
      </div>`).join('');

    $('#pairs-error').hidden = true;
    $('#dialog-pairs').showModal();
  }

  function collectPairs() {
    return $$('.pair-editor').map((row) => ({
      own_account: row.querySelector('[name="own"]').value.trim(),
      benchmark_account: row.querySelector('[name="bench"]').value.trim(),
      account_type: row.querySelector('[name="type"]').value,
      enabled: row.querySelector('[name="enabled"]').checked,
    })).filter((item) => item.own_account || item.benchmark_account);
  }

  async function savePairs() {
    const error = $('#pairs-error');
    error.hidden = true;
    try {
      const data = await api('/api/pairs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pairs: collectPairs() }),
      });
      $('#dialog-pairs').close();
      toast('账号配置已保存');
      await refresh({ quiet: true });
      await loadGroups();
      return data;
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  }

  function askConfirm(title, text, action) {
    $('#confirm-title').textContent = title || '确认';
    $('#confirm-text').textContent = text || '';
    confirmAction = action;
    $('#dialog-confirm').showModal();
  }
  window.ConfirmAsk = askConfirm;

  function promptRunAi() {
    if (state.busy) return;
    const pairs = (state.status && state.status.pairs) || [];
    const ready = pairs.filter((pair) => pair.state === '可分析');
    let text;
    if (ready.length) {
      text = `将调用 DeepSeek（deepseek-flash，低推理强度）分析 ${ready.map((p) => p.own_account).join('、')}。每次成功分析都会产生 Token 费用，同一周期已分析过的不会重复调用。`;
    } else {
      text = '按上次预览，当前没有可分析的账号（通常是三天周期还没完成）。确认后系统会再做一次检查：符合条件的才分析并可能扣费，不符合的会正常跳过，不生成选题、不扣费。';
    }
    askConfirm('开始 AI 分析', text, () =>
      doAction('#btn-run', '/api/run-ai', '分析完成'));
  }

  /* ------------------------------------------------------------ 绑定 */

  function bind() {
    $('#btn-refresh').addEventListener('click', () => location.reload());
    $('#btn-update').addEventListener('click', () =>
      doAction('#btn-update', '/api/update-data', '本地数据已更新'));
    $('#btn-preview').addEventListener('click', () =>
      doAction('#btn-preview', '/api/ai-preview', '预览完成，没有产生 Token 费用'));

    $('#btn-run').addEventListener('click', promptRunAi);

    $('#btn-confirm-run').addEventListener('click', () => {
      const action = confirmAction;
      confirmAction = null;
      $('#dialog-confirm').close();
      if (action) action();
    });

    $('#btn-manage').addEventListener('click', openPairsDialog);
    $('#btn-save-pairs').addEventListener('click', savePairs);

    const sandboxToggle = $('#chk-sandbox');
    if (sandboxToggle) {
      sandboxToggle.addEventListener('change', () => {
        state.sandbox = sandboxToggle.checked;
        state.activeReport = null;
        loadGroups().then(() => {
          if (state.activeTab === 'AI报告') reloadTopics();
        });
      });
    }

    $('#btn-add-pair').addEventListener('click', () => {
      const editor = $('#pairs-editor');
      if ($$('.pair-editor').length >= 3) { toast('最多三组', true); return; }
      const index = $$('.pair-editor').length;
      editor.insertAdjacentHTML('beforeend', `
        <div class="pair-editor" data-index="${index}">
          <div class="row-index">第 ${index + 1} 组</div>
          <div class="field"><label>自有账号</label><input type="text" name="own" placeholder="与归档名称一致"></div>
          <div class="field"><label>对标账号</label><input type="text" name="bench" placeholder="与归档名称一致"></div>
          <div class="field"><label>类型</label><select name="type"><option value="old">老账号</option><option value="new">新账号</option></select></div>
          <div class="field"><label>启用</label><div class="toggle"><input type="checkbox" name="enabled" checked></div></div>
        </div>`);
    });

    $$('[data-close]').forEach((btn) => {
      btn.addEventListener('click', () => btn.closest('dialog').close());
    });

    $('#btn-log').addEventListener('click', () => {
      const log = $('#result-log');
      const hidden = log.hidden;
      log.hidden = !hidden;
      $('#btn-log').textContent = hidden ? '收起日志' : '查看日志';
    });

    window.addEventListener('resize', movePill);
  }

  bind();
  refresh();
})();
