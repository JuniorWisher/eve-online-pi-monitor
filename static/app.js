/* 行星工业监视器 —— 前端逻辑（无框架，原生 JS） */

const S = {
  chars: [],
  active: new Set(),
  config: null,
  overview: null,
  inventory: null,
  filter: 'all',
  search: '',
  invMode: 'rows',
  invSearch: '',
  acked: new Set(),
  times: { gen: null, price: null },
  busy: false,
  expandAll: false,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const fmtNum = (n, digits = 0) =>
  n == null ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });

function fmtDur(sec) {
  if (sec == null) return '—';
  if (sec <= 0) return '已到期';
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d) return `${d} 天 ${h} 小时`;
  if (h) return `${h} 小时 ${m} 分`;
  return `${m} 分钟`;
}

function fmtClock(sec) {
  if (sec == null) return '—';
  if (sec <= 0) return '已到期';
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (d) return `${d}天 ${h}时`;
  const pad = (x) => String(x).padStart(2, '0');
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

function fmtPct(v) {
  if (v == null) return '—';
  const p = v * 100;
  if (p > 0 && p < 0.1) return '<0.1%';
  if (p > 0 && p < 10) return p.toFixed(1) + '%';
  return Math.round(p) + '%';
}

function fmtAgo(sec) {
  if (sec == null || !Number.isFinite(sec)) return '—';
  if (sec < 60) return '刚刚';
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`;
  return `${Math.floor(sec / 86400)} 天前`;
}

function fmtIsk(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (!n) return '0';
  if (n >= 1e8) return (n / 1e8).toFixed(2) + ' 亿';
  if (n >= 1e4) return (n / 1e4).toFixed(1) + ' 万';
  return n.toFixed(0);
}

function fmtTime(iso) {  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return '—';
  return d.toLocaleString('zh-CN', { hour12: false });
}

let toastTimer = null;
function toast(msg, isError = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = 'toast'), 3200);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.payload = data;
    err.status = res.status;
    throw err;
  }
  return data;
}

/* ---------------------------------------------------------------- 状态 */

async function loadState() {
  const st = await api('/api/state');
  S.config = st.config;
  S.chars = st.characters || [];
  S.active = new Set(S.chars.map((c) => c.id));
  renderChars();
  renderSettings(st);
  $('#demoRibbon').hidden = !st.demo;
  $('#loginBtn').style.display = st.demo ? 'none' : '';
  if (!st.config.configured && !st.demo) {
    $('#loginBtn').classList.add('disabled');
    $('#loginBtn').style.opacity = '.45';
  }
}

function renderChars() {
  const box = $('#charManage');
  if (!box) return;
  if (!S.chars.length) {
    box.innerHTML = '<p class="muted" style="font-size:12.5px;margin:0">还没有登录任何角色。</p>';
    return;
  }
  const stats = charStats();
  box.innerHTML = S.chars
    .map((c) => {
      const st = stats[c.id] || {};
      const on = S.active.has(c.id);
      const meta = [
        st.colonies != null ? `${st.colonies} 个殖民地` : '',
        st.next ? `最近到期 ${st.next}` : '',
      ].filter(Boolean).join(' · ');
      return `<div class="char-row ${on ? '' : 'is-off'}">
        <span class="dot ${on ? 'on' : ''}"></span>
        <span class="name">${esc(c.name)}</span>
        <span class="id">#${c.id}</span>
        <span class="cstat">${esc(meta)}</span>
        <label class="switch"><input type="checkbox" data-toggle="${c.id}" ${on ? 'checked' : ''}> 统计</label>
        <button class="btn danger" data-del="${c.id}">移除</button>
      </div>`;
    })
    .join('');

  box.querySelectorAll('[data-toggle]').forEach((el) => {
    el.onchange = () => {
      const cid = Number(el.dataset.toggle);
      if (el.checked) S.active.add(cid);
      else S.active.delete(cid);
      applyFilter();
      renderChars();
    };
  });
  box.querySelectorAll('[data-del]').forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm('移除后需要重新授权才能查看该角色，确定吗？')) return;
      await api(`/api/logout/${btn.dataset.del}`, { method: 'POST' });
      toast('已移除');
      await loadState();
      S.overview = null;
      await loadOverview(false);
    };
  });
}

function charStats() {
  const out = {};
  if (!S.overview) return out;
  const now = Date.now() / 1000;
  for (const c of S.overview.colonies || []) {
    const st = out[c.character_id] || (out[c.character_id] = { colonies: 0, next: '', nextTs: null });
    st.colonies++;
    if (c.next_future_expiry && (st.nextTs == null || c.next_future_expiry < st.nextTs)) {
      st.nextTs = c.next_future_expiry;
      st.next = fmtDur(c.next_future_expiry - now);
    }
  }
  return out;
}

/* 只保留开启的角色；全部开启时直接用原始数据 */
function activeOverview() {
  const ov = S.overview;
  if (!ov) return null;
  if (!S.chars.length || S.active.size === S.chars.length) return ov;
  const ids = S.active;
  const colonies = (ov.colonies || []).filter((c) => ids.has(c.character_id));
  const chars = ((ov.assets && ov.assets.characters) || []).filter((c) => ids.has(c.id));
  const assets = Object.assign({}, ov.assets || {}, { characters: chars, totals: sumAssets(chars) });
  const summary = summarize(colonies);
  summary.stock_value = stockValue(colonies, chars, ov.prices);
  return Object.assign({}, ov, { colonies, assets, summary });
}

/* 按 Jita 最高买单估算价值，P0 不计（和后台 _stock_value 保持一致） */
function stockValue(colonies, assetChars, prices) {
  let total = 0;
  const add = (items) => {
    for (const i of items || []) {
      if (i.tier === 'P0') continue;
      const p = prices && prices[i.type_id];
      if (p && p.buy) total += (i.amount || 0) * p.buy;
    }
  };
  for (const c of colonies || []) for (const st of c.storages || []) add(st.items);
  for (const ch of assetChars || []) if (ch.ok) add(ch.items);
  return total;
}

function sumAssets(chars) {
  const t = {
    types: 0, units: 0, volume_m3: 0, locations: 0,
    customs_units: 0, structure_units: 0, customs_volume_m3: 0, structure_volume_m3: 0,
  };
  for (const c of chars) {
    if (!c.ok) continue;
    t.types += c.types || 0;
    t.units += c.units || 0;
    t.volume_m3 += c.volume_m3 || 0;
    t.locations += c.locations || 0;
    for (const k of ['customs', 'structure']) {
      const b = c.buckets && c.buckets[k];
      if (b) {
        t[k + '_units'] += b.units || 0;
        t[k + '_volume_m3'] += b.volume_m3 || 0;
      }
    }
  }
  return t;
}

function summarize(colonies) {
  const now = Date.now() / 1000;
  const s = {
    characters: S.active.size, colonies: colonies.length, extractors: 0, factories: 0,
    expired: 0, expiring_2h: 0, expiring_24h: 0, storage_m3: 0, capacity_m3: 0, storage_units: 0,
    tier_units: {}, tier_estimate: {}, next_expiry: null, next_expiry_iso: null, fill: null,
  };
  for (const c of colonies) {
    s.extractors += (c.extractors || []).length;
    s.factories += (c.factories || []).length;
    if (c.urgency === 'expired') s.expired++;
    if (c.next_future_expiry) {
      const rem = c.next_future_expiry - now;
      if (rem <= 86400) s.expiring_24h++;
      if (rem <= 7200) s.expiring_2h++;
      if (s.next_expiry == null || c.next_future_expiry < s.next_expiry) s.next_expiry = c.next_future_expiry;
    }
    s.storage_m3 += c.storage_m3 || 0;
    s.capacity_m3 += c.capacity_m3 || 0;
    for (const st of c.storages || []) {
      for (const it of st.items || []) s.storage_units += it.amount || 0;
    }
    for (const [k, v] of Object.entries(c.tier_units || {})) s.tier_units[k] = (s.tier_units[k] || 0) + v;
    for (const [k, v] of Object.entries(c.tier_estimate || {})) s.tier_estimate[k] = (s.tier_estimate[k] || 0) + v;
  }
  s.fill = s.capacity_m3 ? s.storage_m3 / s.capacity_m3 : null;
  s.next_expiry_iso = s.next_expiry ? new Date(s.next_expiry * 1000).toISOString() : null;
  return s;
}

/* 角色开关变化后：本地重算，不发任何请求（库存单独按角色子集取） */
function applyFilter() {
  const ov = activeOverview();
  if (!ov || !ov.summary) return;
  renderKpis(ov.summary, ov.assets);
  renderNotice(ov.assets);
  renderTierStrip(ov.summary);
  renderColonies(ov.colonies);
  if (S.inventory) loadInventory();
}

/* ------------------------------------------------------------ 总览数据 */

async function loadOverview(force, silent) {
  if (S.busy) return;
  S.busy = true;
  const btn = $('#refreshBtn');
  btn.classList.add('loading');

  if (!S.overview) {
    $('#colonyGrid').innerHTML = Array.from({ length: 6 }, () => '<div class="skeleton"></div>').join('');
    $('#overviewEmpty').hidden = true;
  }

  try {
    const data = await api(`/api/overview${force ? '?force=1' : ''}`);
    if (data.error) throw new Error(data.error);
    if (data.needs_login || !data.summary) {
      S.overview = null;
      renderEmpty();
      return;
    }
    S.overview = data;
    S.acked.clear(); // 已处理只是本次查看期间的标记，重新取数后回到实际状态
    $('#overviewEmpty').hidden = true;
    $('#tab-overview').classList.remove('is-empty');
    applyFilter();
    renderChars();

    S.times.gen = data.generated_at || null;
    S.times.price = data.prices_at || null;
    renderTimes();
    if (force && !silent) toast('已刷新');
  } catch (e) {
    if (e.payload && e.payload.needs_login) {
      renderEmpty(e.message);
    } else {
      toast(e.message || '加载失败', true);
      if (!S.overview) renderEmpty(e.message);
    }
  } finally {
    S.busy = false;
    btn.classList.remove('loading');
  }
}

function renderEmpty(msg) {
  const configured = S.config && S.config.configured;
  const box = $('#overviewEmpty');
  box.hidden = false;
  $('#tab-overview').classList.add('is-empty');
  $('#colonyGrid').innerHTML = '';
  $('#kpis').innerHTML = '';
  $('#tierStrip').innerHTML = '';
  box.innerHTML = configured
    ? `<b>还没有可显示的行星工业数据</b>
       ${msg ? esc(msg) + '<br>' : ''}
       点右上角「＋ 添加角色」用 EVE 官方账号授权登录，登录后这里会列出该角色所有殖民地。`
    : `<b>先配置 EVE 应用凭据</b>
       到 <a href="https://developers.eveonline.com/applications" target="_blank" rel="noreferrer">developers.eveonline.com</a>
       创建一个 <em>Authentication &amp; API Access</em> 应用，把 Client ID / Secret 填到「设置」页即可。<br>
       <button class="btn ghost" style="margin-top:14px" onclick="switchTab('settings')">去设置</button>`;
}

function renderNotice(assets) {
  const box = $('#reauthNotice');
  if (!assets) {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  const names = assets.needs_reauth || [];
  const noStruct = assets.structures_available === false;
  if (!names.length && !noStruct) {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  box.hidden = false;
  const parts = [];
  if (names.length) {
    parts.push(`<b>${names.length} 个角色缺少资产读取权限</b>，海关/建筑库存统计不到。`);
  }
  if (noStruct) {
    parts.push(`<b>缺少建筑读取权限</b>，无法区分玩家建筑和海关，两者会被合并计算。`);
  }
  box.innerHTML = `<span>${parts.join('<br>')}
    到 EVE 开发者后台给应用补勾 <code>esi-assets.read_assets.v1</code> 和
    <code>esi-universe.read_structures.v1</code>，然后移除角色重新授权。</span>
    <a class="btn ghost" href="/login" style="margin-left:auto">重新授权</a>`;
}

function renderTierStrip(sum) {
  const tiers = ['P0', 'P1', 'P2', 'P3', 'P4'];
  const stock = sum.tier_units || {};
  const est = sum.tier_estimate || {};
  const cell = (t, v) =>
    `<span class="item"><span class="tier ${t}">${t}</span><span class="n">${v ? fmtNum(v) : '—'}</span></span>`;
  const hasStock = tiers.some((t) => stock[t]);
  const hasEst = tiers.some((t) => est[t]);
  if (!hasStock && !hasEst) {
    $('#tierStrip').innerHTML = '';
    return;
  }
  $('#tierStrip').innerHTML = `
    <div class="group"><span class="caption">殖民地库存</span>${tiers.map((t) => cell(t, stock[t])).join('')}</div>
    <div class="divider"></div>
    <div class="group" title="按提取器最近一次模拟的周期速率 × 全程周期数外推。速率会随矿脉衰减，所以这个数偏保守——实测游戏面板的总量比它高约 17%。">
      <span class="caption">提取预估</span>${tiers.map((t) => cell(t, est[t])).join('')}
    </div>
    <span class="caption" style="margin-left:auto">单位：个</span>`;
}

function renderKpis(sum, assets) {
  const kpis = [
    {
      label: '殖民地',
      value: fmtNum(sum.colonies),
      hint: `${sum.characters} 个角色`,
      cls: '',
    },
    {
      label: '24 小时内到期',
      value: fmtNum(sum.expiring_24h),
      hint: sum.expiring_2h ? `其中 ${sum.expiring_2h} 个 2 小时内` : '提取器即将停摆',
      cls: sum.expiring_24h ? 'warn' : 'ok',
    },
    {
      label: '已到期',
      value: fmtNum(sum.expired),
      hint: sum.expired ? '提取器已停止采集' : '全部运转中',
      cls: sum.expired ? 'danger' : 'ok',
    },
    {
      label: '最近到期',
      value: sum.next_expiry ? fmtClock(sum.next_expiry - Date.now() / 1000) : '—',
      hint: sum.next_expiry ? fmtTime(sum.next_expiry_iso) : '无进行中的提取程序',
      cls: sum.next_expiry && sum.next_expiry - Date.now() / 1000 < 7200 ? 'danger' : '',
    },
    {
      label: '工厂',
      value: fmtNum(sum.factories),
      hint: `提取器 ${sum.extractors} 台`,
      cls: '',
    },
    {
      label: '仓储占用',
      value: fmtPct(sum.fill),
      hint: `${fmtNum(sum.storage_m3, 0)} / ${fmtNum(sum.capacity_m3, 0)} m³`,
      cls: sum.fill != null && sum.fill > 0.85 ? 'warn' : '',
    },
  ];

  if (sum.stock_value > 0) {
    kpis.push({
      label: '库存估值',
      value: fmtIsk(sum.stock_value),
      hint: 'Jita 最高买单 · 不含 P0',
      cls: '',
    });
  }

  if (assets && assets.enabled && (assets.totals.units > 0 || assets.needs_reauth.length)) {
    const t = assets.totals;
    let hint;
    if (assets.needs_reauth.length && !t.units) hint = `${assets.needs_reauth.length} 个角色待授权`;
    else if (t.structure_units > 0) hint = `建筑另有 ${fmtNum(t.structure_units)} 单位`;
    else hint = `${fmtNum(t.customs_volume_m3, 1)} m³ · ${t.locations} 处`;
    kpis.push({
      label: '海关+殖民地',
      value: assets.needs_reauth.length && !t.units ? '—' : fmtNum((sum.storage_units || 0) + t.customs_units),
      hint,
      cls: assets.needs_reauth.length ? 'warn' : '',
    });
  }

  $('#kpis').innerHTML = kpis
    .map(
      (k) => `<div class="kpi ${k.cls}">
        <div class="label">${k.label}</div>
        <div class="value">${k.value}</div>
        <div class="hint">${esc(k.hint)}</div>
      </div>`
    )
    .join('');
}

const P1_UNIT_M3 = 0.19; // P1 物资单体体积
const P1_PER_CYCLE = 20; // 基础工业设施每 30 分钟产出 20 个 P1（消耗 3000 P0）

function isLaunchpad(s) {
  return /launchpad|spaceport/i.test(s.type_name_en || '');
}

function colonyNeedsAttention(c) {
  const exts = c.extractors || [];
  if (!exts.length) return false; // 纯合成加工殖民地：不参与判断

  const storages = (c.storages || [])
    .filter((s) => s.is_storage)
    // 同一个殖民地既有发射平台又有储藏设施时，发射平台排前面
    .sort((a, b) => Number(isLaunchpad(b)) - Number(isLaunchpad(a)));
  const launchpads = storages.filter(isLaunchpad);
  const facs = c.factories || [];
  const p1Count = facs.filter((f) => f.facility === 'basic').length;
  const hasP2 = facs.some((f) => f.facility === 'advanced');

  // 有提取器 + 有 P2 工厂（不管还有没有 P1/P3/P4）：发射平台 ≥80% 就算需处理
  if (hasP2) {
    return launchpads.some((s) => (s.fill || 0) >= 0.8);
  }

  // 有提取器 + 只有 P1 工厂：预测提取器结束前会不会把发射平台撑爆
  if (p1Count > 0) {
    const remainSec = Math.max(...exts.map((e) => (e.expired ? 0 : e.remaining || 0)));
    const blocks = Math.ceil(remainSec / 60 / 30); // 每 30 分钟一批
    const perFactory = blocks * P1_PER_CYCLE * P1_UNIT_M3; // 每个 P1 工厂的产出体积
    return launchpads.some((s) => {
      const cap = s.capacity_m3 || 0;
      if (!cap) return false;
      return p1Count * perFactory + (s.used_m3 || 0) >= cap * 0.95;
    });
  }

  // 只有提取器、没有工厂：沿用任一仓储 ≥80%
  return storages.some((s) => (s.fill || 0) >= 0.8);
}

function colonyMatches(c) {
  if (S.filter === 'urgent') {
    const urgent = ['critical', 'warning', 'expired'].includes(c.urgency);
    if (!urgent && !colonyNeedsAttention(c)) return false;
  }
  if (S.filter === 'expired' && c.urgency !== 'expired') return false;
  if (S.filter === 'idle' && c.urgency !== 'idle') return false;
  if (S.search) {
    const q = S.search.toLowerCase();
    const hay = `${c.planet_name} ${c.system_name} ${c.character_name} ${c.planet_type}`.toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}

const RING_R = 19;
const RING_C = 2 * Math.PI * RING_R;
const ICON_STORAGE =
  '<path d="M12 2.6c2.1 2.2 3.3 4.9 3.3 7.9l-1.7 4.2h-3.2L8.7 10.5c0-3 1.2-5.7 3.3-7.9z"/>' +
  '<circle cx="12" cy="8.2" r="1.4"/>' +
  '<path d="M9.7 13.4 7.2 16.6l.6 3.2 2.3-1.7M14.3 13.4l2.5 3.2-.6 3.2-2.3-1.7"/>';
const ICON_CUBE =
  '<path d="M12 3.1 20 7.5v9L12 20.9 4 16.5v-9z"/>' +
  '<path d="M4 7.5 12 12l8-4.5M12 12v8.9"/>';
const ICON_EXTRACTOR =
  '<path d="M12 4.6v8.6"/><path d="m8.3 8.4 3.7-3.8 3.7 3.8"/><path d="M6.4 14.2a5.6 5.6 0 0 0 11.2 0"/>';

const TIER_LABEL = { P0: '原料', P1: '基础商品', P2: '精炼商品', P3: '特种商品', P4: '高级商品' };

function ring(kind, pct, stateCls, title, icon, attrs = '') {
  const p = Math.max(0, Math.min(1, Number.isFinite(pct) ? pct : 0));
  const off = RING_C * (1 - p);
  return `<div class="ring ${kind} ${stateCls}" title="${esc(title)}"${attrs}>
    <svg class="ring-svg" viewBox="0 0 44 44">
      <circle class="ring-track" cx="22" cy="22" r="${RING_R}"/>
      <circle class="ring-bar" cx="22" cy="22" r="${RING_R}"
        stroke-dasharray="${RING_C.toFixed(2)}" stroke-dashoffset="${off.toFixed(2)}"
        transform="rotate(-90 22 22)"/>
    </svg>
    <svg class="ring-ico" viewBox="0 0 24 24">${icon}</svg>
  </div>`;
}

function storageRing(s, hot) {
  const pct = s.fill == null ? 0 : s.fill;
  // 有提取器的殖民地，仓储超过 80% 直接判红（需处理）；其它情况沿用 80% 黄 / 95% 红
  const cls = hot && pct >= 0.8 ? 'full' : pct >= 0.95 ? 'full' : pct >= 0.8 ? 'warn' : '';
  const title =
    `${s.type_name}：${fmtNum(s.used_m3, 0)} / ${fmtNum(s.capacity_m3, 0)} m³（${fmtPct(s.fill)}）` +
    (hot && pct >= 0.8 ? '　⚠ 仓储已超 80%，提取器会被顶停' : '');
  // 发射平台用火箭，储藏设施用立方体，和游戏内图标一致
  const icon = /storage facility/i.test(s.type_name_en || '') ? ICON_CUBE : ICON_STORAGE;
  return ring('storage', pct, cls, title, icon);
}

function extractorRing(e) {
  const duration = e.duration || 1;
  const remaining = e.expired ? 0 : Math.max(0, e.remaining || 0);
  // 圆环表示"已运行比例"，越跑越满；提示文字仍是倒计时
  const elapsedPct = Math.min(1, Math.max(0, (duration - remaining) / duration));
  const cls = e.expired ? 'dead' : remaining <= 7200 ? 'warn' : '';
  const title = `${e.product || '未知产物'}：${e.expired ? '已到期' : '剩余 ' + fmtDur(remaining)}`;
  const attrs =
    ` data-expiry="${e.expiry || 0}" data-duration="${duration}"` +
    ` data-product="${esc(e.product || '')}"`;
  return ring('extractor', elapsedPct, cls, title, ICON_EXTRACTOR, attrs);
}

/* 需处理按钮：有未处理的殖民地就高亮并显示数量（已手动标记的不算） */
function updateUrgentChip(list) {
  const chip = document.querySelector('[data-filter="urgent"]');
  if (!chip) return;
  const n = (list || []).filter(
    (c) => colonyNeedsAttention(c) && !S.acked.has(`${c.character_id}:${c.planet_id}`)
  ).length;
  chip.textContent = n ? `需处理 ${n}` : '需处理';
  chip.classList.toggle('alert', n > 0);
}

function renderColonies(list) {
  const grid = $('#colonyGrid');
  updateUrgentChip(list);
  const shown = list.filter(colonyMatches);

  if (!shown.length) {
    grid.innerHTML = '';
    const box = $('#overviewEmpty');
    box.hidden = false;
    box.innerHTML = list.length
      ? '<b>没有符合条件的殖民地</b>换个筛选条件或清空搜索试试。'
      : '<b>该角色名下没有殖民地</b>确认角色确实建立了行星殖民地，或换一个角色查看。';
    return;
  }
  $('#overviewEmpty').hidden = true;

  // 一个角色一张卡片，角色名排序
  const groups = new Map();
  for (const c of shown) {
    let g = groups.get(c.character_id);
    if (!g) {
      g = { id: c.character_id, name: c.character_name, colonies: [] };
      groups.set(c.character_id, g);
    }
    g.colonies.push(c);
  }
  // 同一账号的人物排在一起（owner 是 SSO 下发的账号哈希，同账号的人物相同）
  const ownerOf = new Map((S.chars || []).map((c) => [c.id, c.owner || `solo-${c.id}`]));
  const ordered = [...groups.values()].sort((a, b) => {
    const oa = ownerOf.get(a.id) || '';
    const ob = ownerOf.get(b.id) || '';
    if (oa !== ob) return oa < ob ? -1 : 1;
    return String(a.name).localeCompare(String(b.name), 'zh-CN');
  });
  // 只有存在"一个账号多个角色"时才显示账号徽标，否则是噪音
  const owners = [...new Set(ordered.map((g) => ownerOf.get(g.id) || ''))];
  const perOwner = {};
  ordered.forEach((g) => {
    const o = ownerOf.get(g.id) || '';
    perOwner[o] = (perOwner[o] || 0) + 1;
  });
  const showAccount = Object.values(perOwner).some((n) => n > 1);
  ordered.forEach((g) => {
    g.account = showAccount ? owners.indexOf(ownerOf.get(g.id) || '') + 1 : 0;
  });

  grid.innerHTML = ordered.map(characterCard).join('');

  grid.querySelectorAll('[data-ack]').forEach((btn) => {
    btn.onclick = (ev) => {
      ev.stopPropagation();
      const key = btn.dataset.ack;
      if (S.acked.has(key)) S.acked.delete(key);
      else S.acked.add(key);
      const ov = activeOverview();
      if (ov) renderColonies(ov.colonies);
    };
  });

  grid.querySelectorAll('.expand-btn').forEach((btn) => {
    btn.onclick = () => {
      const item = btn.closest('.cg-item');
      const detail = item.querySelector('.detail');
      const open = detail.hidden;
      detail.hidden = !open;
      btn.textContent = open ? '收起' : '详情';
    };
  });

  if (S.expandAll) {
    grid.querySelectorAll('.cg-item').forEach((item) => {
      const d = item.querySelector('.detail');
      if (!d) return;
      d.hidden = false;
      const btn = item.querySelector('.expand-btn');
      if (btn) btn.textContent = '收起';
    });
  }
}

function characterCard(g) {
  const now = Date.now() / 1000;
  let nextTs = null;
  let expired = 0;
  for (const c of g.colonies) {
    if (c.urgency === 'expired') expired++;
    if (c.next_future_expiry && (nextTs == null || c.next_future_expiry < nextTs)) {
      nextTs = c.next_future_expiry;
    }
  }
  const meta = [`${g.colonies.length} 个殖民地`];
  if (expired) meta.push(`${expired} 个已到期`);
  if (nextTs) meta.push(`最近到期 ${fmtDur(nextTs - now)}`);

  return `<article class="cgroup">
    <header class="cg-head">
      <span class="cg-dot"></span>
      <span class="cg-name">${esc(g.name)}</span>
      ${g.account ? `<span class="cg-acc" title="同一账号下的人物">账号 ${g.account}</span>` : ''}
      <span class="cg-meta">${esc(meta.join(' · '))}</span>
    </header>
    ${g.colonies.map(colonyRow).join('')}
  </article>`;
}

/* 游戏内数据时间：取该殖民地最新的「工厂周期开始 / 提取程序安装」时间。
   CCP 只在客户端打开过殖民地时才重算这些值，所以它比 HTTP 缓存时间更接近真实。 */
function dataAgeHtml(c) {
  if (c.data_time == null) return '';
  const age = Date.now() / 1000 - c.data_time;
  const cls = age > 48 * 3600 ? 'stale' : age > 6 * 3600 ? 'old' : '';
  const title =
    `游戏内数据时间 ${fmtTime(c.data_time_iso)}\n` +
    '取 ESI 殖民地列表的 last_update：CCP 最后一次重算这个殖民地的时间。\n' +
    'CCP 只在客户端打开过它时才重算，数字对不上时先进游戏点开一次，再点「刷新」。';
  return `<span class="age ${cls}" title="${esc(title)}">数据 ${fmtAgo(age)}</span>`;
}

function colonyRow(c) {
  const storages = (c.storages || [])
    .filter((s) => s.is_storage)
    // 同一个殖民地既有发射平台又有储藏设施时，发射平台排前面
    .sort((a, b) => Number(isLaunchpad(b)) - Number(isLaunchpad(a)));
  const exts = (c.extractors || []).slice().sort((a, b) => (a.expiry || 0) - (b.expiry || 0));
  // 有提取器且仓储占用超过 80% -> 需处理（仓储满了提取器会被顶停）
  const hot = colonyNeedsAttention(c);
  const rings = [
    ...storages.map((s) => storageRing(s, hot)),
    ...exts.map(extractorRing),
  ].join('');

  const bits = [];
  if (c.factories.length) bits.push(`工厂 ${c.factories.length}`);
  if (!exts.length) bits.push('无提取器');

  const ackKey = `${c.character_id}:${c.planet_id}`;
  const acked = hot && S.acked.has(ackKey);
  const ackHtml = hot
    ? `<span class="pill ${acked ? 'ok' : 'danger'}" title="${acked ? '已标记处理，下次刷新 ESI 后恢复实际状态' : '仓储即将被顶停，建议收菜'}">${acked ? '已处理' : '需处理'}</span>
       <button class="ack-btn ${acked ? 'undo' : ''}" data-ack="${ackKey}">${acked ? '撤销' : '标记已处理'}</button>`
    : '';

  return `<div class="cg-item" data-urgency="${c.urgency}"${hot && !acked ? ' data-hot="1"' : ''}>
    <div class="cg-row">
      <div class="cg-planet">
        <div class="pname">${esc(c.planet_name)}</div>
        <div class="pmeta">
          ${ackHtml}
          ${c.planet_type ? `<span class="pill" title="${esc(c.planet_type_raw || '')}">${esc(c.planet_type)}</span>` : ''}
          ${c.security != null ? `<span class="sec ${c.sec_class}">${c.security.toFixed(2)}</span>` : ''}
          <span class="muted">${esc(bits.join(' · '))}</span>
          ${dataAgeHtml(c)}
        </div>
      </div>
      <div class="cg-rings">${rings || '<span class="muted" style="font-size:11.5px">无仓储</span>'}</div>
      <button class="expand-btn">详情</button>
    </div>
    <div class="detail" hidden>${detailHtml(c)}</div>
  </div>`;
}

/* 详情页排序权重：指挥中心 → 仓储 → 提取器 → 提取程序 → P1/P2/P3/P4 工厂 → 其他 */
const PIN_RANK = { command_center: 0, storage: 1, extractor: 2 };
const EXTRACTOR_PROGRAM_RANK = 2.5;
const FACTORY_RANK = { P1: 3, P2: 4, P3: 5, P4: 6 };

function pinRank(p) {
  if (p.kind === 'factory') return FACTORY_RANK[(p.factory && p.factory.tier) || ''] ?? 7;
  return PIN_RANK[p.kind] ?? 8;
}

function pinRow(p) {
  let content = '';
  let qty = '';
  if (p.kind === 'extractor' && p.extractor) {
    const e = p.extractor;
    content = `${esc(e.product)} <span class="muted">· ${e.heads} 个头 · 每 ${Math.round(e.cycle_time / 60)} 分钟一周期</span>`;
    qty = `${fmtNum(e.qty_per_cycle)}/周期`;
  } else if (p.kind === 'factory' && p.factory) {
    content = p.factory.product
      ? `${esc(p.factory.product)} <span class="muted">· 图纸 #${esc(p.factory.schematic_id)}</span>`
      : `<span class="muted">图纸 #${esc(p.factory.schematic_id)}（产物未在路由中体现）</span>`;
    qty = p.factory.tier !== '?' ? `<span class="tier ${esc(p.factory.tier)}">${esc(p.factory.tier)}</span>` : '';
  } else if (p.contents.length) {
    // 全部列出，不截断——P4 数量小，排序后总在末尾，截断最容易把它藏掉
    content = p.contents
      .map(
        (i) =>
          `<span class="tier ${esc(i.tier)}">${esc(i.tier)}</span> ` +
          `<span title="${esc(i.name_en || '')}">${esc(i.name)}</span> ` +
          `<span class="muted">×${fmtNum(i.amount)}</span>`
      )
      .join('<br>');
    qty = p.used_m3 != null ? `${fmtNum(p.used_m3, 1)} m³` : '';
  } else if (p.kind === 'storage') {
    // 空仓储也照常列出：容量和 0% 占用本身就是有用的信息
    content = '<span class="muted">空仓</span>';
    qty = p.capacity_m3 ? `0 / ${fmtNum(p.capacity_m3, 0)} m³` : '';
  } else {
    content = '<span class="muted">—</span>';
  }
  return `<tr>
    <td class="kind" title="${esc(p.type_name_en || '')}">${esc(p.kind_label)}</td>
    <td>${content}</td>
    <td class="num">${qty}</td>
  </tr>`;
}

function extractorProgramRow(e) {
  // ESI 只给 qty_per_cycle（最近一次模拟那个周期采到多少），既没有全程平均也没有总量。
  // 这里按该速率 × 剩余周期数算"还能采多少"，偏乐观；反过来 × 总周期数算全程会偏保守。
  const tip =
    `当前速率 ${fmtNum(e.rate_per_hour)}/小时（ESI 的 qty_per_cycle × 每小时周期数）\n` +
    'ESI 不给全程平均，也不给总量：qty_per_cycle 只代表最近一次模拟的那个周期，' +
    '会随矿脉衰减一路变小。\n' +
    '实测某提取器：游戏面板总量比"按该速率 × 总周期数"的线性外推高约 17%。';
  return `<tr>
    <td class="kind">提取程序</td>
    <td>${esc(e.product)} <span class="muted">· ${e.heads} 头</span></td>
    <td class="num" title="${esc(tip)}">剩余 ${fmtNum(e.est_remaining)} <span class="muted">· ${fmtNum(
    e.rate_per_hour
  )}/时</span></td>
  </tr>`;
}

function detailHtml(c) {
  // 按「指挥中心 → 仓储 → 提取器 → 提取程序 → P1~P4 工厂」重排，
  // 同权重内保持 ESI 原顺序（seq），避免每次刷新位置乱跳
  const entries = c.pins.map((p, i) => ({ rank: pinRank(p), seq: i, html: pinRow(p) }));
  c.extractors.forEach((e, i) =>
    entries.push({ rank: EXTRACTOR_PROGRAM_RANK, seq: c.pins.length + i, html: extractorProgramRow(e) })
  );
  entries.sort((a, b) => a.rank - b.rank || a.seq - b.seq);
  const rows = entries.map((x) => x.html).join('');

  return `<h4>建筑与内容（${c.pin_count} 个引脚 / ${c.link_count} 条链接 / ${c.route_count} 条路由）</h4>
    <table class="pin-table">${rows}</table>
    <p class="muted" style="font-size:11px;margin:10px 0 0;line-height:1.6">
      提取器产量按程序开始时的速率估算，实际会随矿脉衰减而下降。<br>
      数据来自 ESI，CCP 只在客户端里打开过该殖民地时才重算，对不上时先在游戏里点开一次再刷新。
      <a href="/api/colony/${c.character_id}/${c.planet_id}" target="_blank" rel="noreferrer">查看 ESI 原始数据</a>
    </p>`;
}

/* ------------------------------------------------------------ 库存报表 */

async function loadInventory() {
  if (!S.active.size) {
    S.inventory = null;
    renderInventory();
    return;
  }
  try {
    const ids = [...S.active].join(',');
    const data = await api(`/api/inventory?chars=${ids}`);
    if (data.error) throw new Error(data.error);
    S.inventory = data;
    renderInventory();
  } catch (e) {
    toast(e.message || '库存加载失败', true);
  }
}

function renderInventory() {
  const inv = S.inventory;
  const table = $('#invTable');
  const panels = $('#invPanels');

  if (!inv || !inv.rows || !inv.rows.length) {
    table.innerHTML = '';
    table.hidden = false;
    panels.innerHTML = '';
    panels.hidden = true;
    $('#invEmpty').hidden = false;
    $('#invTotals').textContent = '';
    return;
  }
  $('#invEmpty').hidden = true;

  const t = inv.totals;
  const parts = [`海关+殖民地 ${fmtNum(t.colony_customs_units)}`];
  if (t.structure_units > 0) parts.push(`建筑 ${fmtNum(t.structure_units)}`);
  const totalIsk = t.value ? ` · 估值 ${fmtIsk(t.value)} ISK` : '';
  $('#invTotals').textContent =
    `${t.types} 种物资 · ${fmtNum(t.units)} 单位 · ${fmtNum(t.volume_m3, 1)} m³ （${parts.join(' / ')}）${totalIsk}`;

  const q = (S.invSearch || '').trim().toLowerCase();
  const hit = (r, withChar) => {
    if (!q) return true;
    const hay = `${r.name} ${r.name_en || ''}${withChar ? ' ' + r.character_name : ''}`.toLowerCase();
    return hay.includes(q);
  };
  const deltaCell = (d) => {
    if (d == null) return '<td class="num muted">—</td>';
    if (Math.abs(d) < 0.5) return '<td class="num muted">0</td>';
    return `<td class="num ${d > 0 ? 'up' : 'down'}">${d > 0 ? '+' : ''}${fmtNum(d)}</td>`;
  };
  const srcPill = (src) =>
    src === 'structure'
      ? '<span class="pill src-station">建筑</span>'
      : '<span class="pill src-customs">海关+殖民地</span>';

  if (S.invMode === 'rows') {
    table.hidden = false;
    panels.hidden = true;
    const rows = inv.rows.filter((r) => hit(r, true));
    table.innerHTML = `<thead><tr>
        <th>角色</th><th>位置</th><th>来源</th><th>物资</th><th>层级</th>
        <th class="num">数量</th><th class="num">体积 m³</th><th class="num">较上次</th>
      </tr></thead><tbody>
      ${
        rows.length
          ? rows
              .map(
                (r) => `<tr>
            <td>${esc(r.character_name)}</td>
            <td>${esc(r.planet_name)}</td>
            <td>${srcPill(r.source)}</td>
            <td title="${esc(r.name_en || '')}">${esc(r.name)}</td>
            <td><span class="tier ${esc(r.tier)}">${esc(r.tier)}</span></td>
            <td class="num">${fmtNum(r.amount)}</td>
            <td class="num">${fmtNum(r.volume_m3, 1)}</td>
            ${deltaCell(r.delta)}
          </tr>`
              )
              .join('')
          : '<tr><td colspan="8" class="muted" style="text-align:center;padding:24px">没有匹配的物资</td></tr>'
      }
      </tbody>`;
  } else {
    table.hidden = true;
    panels.hidden = false;
    const html = ['P1', 'P2', 'P3', 'P4', 'P0']
      .map((tier) => {
        const rows = inv.by_type.filter((r) => r.tier === tier && hit(r, false));
        if (!rows.length) return '';
        const units = rows.reduce((n, r) => n + r.amount, 0);
        return `<div class="tier-panel">
          <div class="tp-head">
            <span class="tier ${tier}">${tier}</span>
            <span class="tp-name">${TIER_LABEL[tier] || ''}</span>
            <span class="tp-count">${rows.length} 种 · ${fmtNum(units)} 单位</span>
          </div>
          <table class="data">
            <thead><tr>
              <th>物资 / Jita 买价</th><th class="num">总数量</th><th class="num">海关+殖民地</th>
              <th class="num">建筑</th><th class="num">体积 m³</th><th class="num">估值</th>
              <th class="num">较上次</th>
            </tr></thead>
            <tbody>
              ${rows
                .map(
                  (r) => `<tr>
                <td class="namecell" title="${esc(r.name_en || '')}">
                  <div class="nm">${esc(r.name)}</div>
                  <div class="px">${r.buy_price ? fmtNum(r.buy_price, 2) + ' ISK' : '<span class="muted">无报价</span>'}</div>
                </td>
                <td class="num">${fmtNum(r.amount)}</td>
                <td class="num">${r.colony_customs_amount ? fmtNum(r.colony_customs_amount) : '<span class="muted">—</span>'}</td>
                <td class="num">${r.structure_amount ? fmtNum(r.structure_amount) : '<span class="muted">—</span>'}</td>
                <td class="num">${fmtNum(r.volume_m3, 1)}</td>
                <td class="num isk">${r.value ? fmtIsk(r.value) : '<span class="muted">—</span>'}</td>
                ${deltaCell(r.delta)}
              </tr>`
                )
                .join('')}
            </tbody>
          </table>
        </div>`;
      })
      .filter(Boolean)
      .join('');
    panels.innerHTML = html || '<div class="empty">没有匹配的物资</div>';
  }
}

/* ---------------------------------------------------------------- 设置 */

function renderSettings(st) {
  const cfg = st.config || {};
  $('#setClientId').value = '';
  $('#setRedirect').value = cfg.redirect_uri || '';
  $('#setEmail').value = cfg.contact_email || '';
  $('#cidHint').textContent = cfg.client_id_set ? `（已保存 ${cfg.client_id_hint}，留空表示不修改）` : '（未设置）';
  $('#secretHint').textContent = cfg.client_secret_set ? `（已保存 ${cfg.client_secret_hint}）` : '（未设置）';
  if ($('#setOverviewTtl')) $('#setOverviewTtl').value = String(cfg.overview_ttl ?? 0);
  if (st.about) {
    if ($('#appVersion')) $('#appVersion').textContent = st.about.version || '—';
    if ($('#appWisher')) $('#appWisher').textContent = st.about.wisher || '—';
    if ($('#appCreator')) $('#appCreator').textContent = st.about.creator || '—';
  }
  if ($('#dataDir')) {
    $('#dataDir').textContent = (st.data_dir || '') + (st.portable ? '（便携模式）' : '');
  }
  if ($('#dataWarn')) {
    const warn = $('#dataWarn');
    if (st.onedrive) {
      warn.hidden = false;
      warn.textContent = '⚠ 这个目录在 OneDrive 同步范围内，云同步可能锁住数据库导致读写失败，建议关掉该文件夹的同步。';
    } else {
      warn.hidden = true;
    }
  }
  if ($('#scopeList')) {
    $('#scopeList').innerHTML = (st.scopes || [])
      .map((x) => `<code>${esc(x)}</code>`)
      .join('');
  }
  $('#storeInfo').textContent = `缓存条目 ${st.cache.entries} 条（有效 ${st.cache.fresh} 条）· token 加密：${st.encryption ? '已启用' : '不可用（未安装 cryptography）'}`;


}

/* ---------------------------------------------------------------- 事件 */

function switchTab(name) {
  $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  $$('.panel-tab').forEach((p) => (p.hidden = p.id !== `tab-${name}`));
  if (name === 'inventory') loadInventory();
  if (name === 'chars') renderChars();
}

function bind() {
  $$('.tab').forEach((t) => (t.onclick = () => switchTab(t.dataset.tab)));
  $('#settingsBtn').onclick = () => switchTab('settings');
  $('#refreshBtn').onclick = () => loadOverview(true);

  $$('#filters .chip').forEach((chip) => {
    chip.onclick = () => {
      $$('#filters .chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      S.filter = chip.dataset.filter;
      const ov = activeOverview();
      if (ov) renderColonies(ov.colonies);
    };
  });

  let searchTimer = null;
  $('#search').oninput = (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      S.search = e.target.value.trim();
      const ov = activeOverview();
      if (ov) renderColonies(ov.colonies);
    }, 160);
  };

  if ($('#invSearch')) {
    let invTimer = null;
    $('#invSearch').oninput = (e) => {
      clearTimeout(invTimer);
      invTimer = setTimeout(() => {
        S.invSearch = e.target.value;
        renderInventory();
      }, 120);
    };
  }

  $$('[data-inv]').forEach((chip) => {
    chip.onclick = () => {
      $$('[data-inv]').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      S.invMode = chip.dataset.inv;
      renderInventory();
    };
  });

  if ($('#expandAll')) $('#expandAll').onclick = (e) => {
    S.expandAll = !S.expandAll;
    e.target.classList.toggle('active', S.expandAll);
    e.target.textContent = S.expandAll ? '收起全部' : '展开全部';
    const ov = activeOverview();
    if (ov) renderColonies(ov.colonies);
  };

  // 下拉框改完立即保存，不用再去左边找「保存」按钮
  if ($('#setOverviewTtl')) {
    $('#setOverviewTtl').onchange = async (e) => {
      const v = Number(e.target.value || 0);
      try {
        await api('/api/settings', { method: 'POST', body: JSON.stringify({ overview_ttl: v }) });
        S.config = { ...(S.config || {}), overview_ttl: v };
        const label = e.target.selectedOptions[0]?.textContent || '';
        toast(`已保存：${label.trim()}`);
      } catch (err) {
        toast('保存失败：' + err.message, true);
      }
    };
  }

  $('#saveSettings').onclick = async () => {
    const body = {
      client_id: $('#setClientId').value.trim(),
      client_secret: $('#setSecret').value.trim(),
      contact_email: $('#setEmail').value.trim(),
      overview_ttl: Number($('#setOverviewTtl').value || 0),
    };
    try {
      const res = await api('/api/settings', { method: 'POST', body: JSON.stringify(body) });
      $('#settingsStatus').textContent = '已保存';
      $('#setSecret').value = '';
      $('#setClientId').value = '';
      S.config = res.config;
      renderSettings({ ...(await api('/api/state')) });
      toast('配置已保存，现在可以点「添加角色」登录');
      $('#loginBtn').style.opacity = '1';
      $('#loginBtn').classList.remove('disabled');
    } catch (e) {
      toast(e.message, true);
    }
  };

  $('#copyRedirect').onclick = async () => {
    const v = $('#setRedirect').value;
    try {
      await navigator.clipboard.writeText(v);
      toast('已复制：' + v);
    } catch {
      $('#setRedirect').select();
      document.execCommand('copy');
      toast('已复制');
    }
  };

  if ($('#quitApp')) {
    $('#quitApp').onclick = async () => {
      if (!confirm('确定要关闭程序吗？关掉后需要重新双击启动。')) return;
      try {
        await api('/api/shutdown', { method: 'POST' });
      } catch {}
      document.body.innerHTML =
        '<div style="padding:60px;text-align:center;color:#8fa3b8;font:14px/1.8 system-ui">' +
        '<div style="font-size:18px;color:#45d6c4;margin-bottom:12px">程序已关闭</div>' +
        '可以关掉这个标签页了。下次双击桌面快捷方式即可重新打开。</div>';
    };
  }

  $('#clearCache').onclick = async () => {
    const r = await api('/api/cache/clear', { method: 'POST' });
    S.overview = null;
    S.inventory = null;
    toast(`已清空 ${r.cleared} 条缓存，正在重新拉取…`);
    await loadState();
    await loadOverview(true, true);
  };

  if ($('#selectAllChars')) {
    $('#selectAllChars').onclick = () => {
      S.chars.forEach((c) => S.active.add(c.id));
      applyFilter();
      renderChars();
    };
  }
}

/* -------------------------------------------------------------- 倒计时 */

/* 每分钟把提取器圆环按当前时间重算一遍（不请求 ESI） */
/* 每 5 分钟看一次数据是否超过 TTL，超了就静默重新拉（服务端再按 TTL 决定要不要重建） */
function scheduleAutoRefresh() {
  const CHECK_MS = 5 * 60 * 1000;

  const check = async () => {
    const ttl = Number((S.config && S.config.overview_ttl) || 0);
    if (ttl <= 0) return;          // 手动模式：永远不自动更新
    if (!S.times.gen) return;
    if (document.hidden) return;   // 切到后台先不拉，等回前台再说
    if (S.busy) return;
    if (Date.now() / 1000 - S.times.gen < ttl) return;
    await loadOverview(false, true);
  };

  setInterval(check, CHECK_MS);
  // 从后台切回来时立刻判断一次，不用等下一个 5 分钟
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) check();
  });
}

function renderTimes() {
  const now = Date.now() / 1000;
  const parts = [];
  if (S.times.gen) parts.push(`数据缓存于 ${fmtAgo(now - S.times.gen)}`);
  if (S.times.price) parts.push(`价格更新于 ${fmtAgo(now - S.times.price)}`);
  $('#updatedAt').textContent = parts.join(' · ');
}

function tick() {
  const now = Date.now() / 1000;
  $$('.ring.extractor[data-expiry]').forEach((el) => {
    const expiry = Number(el.dataset.expiry) || 0;
    const duration = Number(el.dataset.duration) || 0;
    if (!expiry || !duration) return;

    const remaining = Math.max(0, expiry - now);
    const pct = Math.min(1, Math.max(0, (duration - remaining) / duration));
    const bar = el.querySelector('.ring-bar');
    if (bar) bar.setAttribute('stroke-dashoffset', (RING_C * (1 - pct)).toFixed(2));

    const dead = remaining <= 0;
    el.classList.toggle('dead', dead);
    el.classList.toggle('warn', !dead && remaining <= 7200);
    const name = el.dataset.product || '提取器';
    el.title = `${name}：${dead ? '已到期' : '剩余 ' + fmtDur(remaining)}`;
  });
  renderTimes();
}

/* ---------------------------------------------------------------- 启动 */

async function boot() {
  bind();
  try {
    await loadState();
  } catch (e) {
    toast('无法连接本地服务', true);
  }

  const params = new URLSearchParams(location.search);
  if (params.get('error')) toast('登录失败：' + params.get('error'), true);
  if (params.get('welcome')) toast(`已登录 ${params.get('welcome')}`);
  if (params.get('need_config')) switchTab('settings');
  if (params.get('tab')) switchTab(params.get('tab'));
  if (params.get('q')) {
    S.invSearch = params.get('q');
    const box = $('#invSearch');
    if (box) box.value = S.invSearch;
  }
  if (params.get('inv')) {
    S.invMode = params.get('inv') === 'types' ? 'types' : 'rows';
    $$('[data-inv]').forEach((c) => c.classList.toggle('active', c.dataset.inv === S.invMode));
  }
  if (params.get('expand')) {
    S.expandAll = true;
    const btn = $('#expandAll');
    btn.classList.add('active');
    btn.textContent = '收起全部';
  }

  await loadOverview(false);
  setInterval(tick, 60000);
  scheduleAutoRefresh();
}

boot();
