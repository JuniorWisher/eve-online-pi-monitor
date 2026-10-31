/* 行星工业监视器 —— 前端逻辑（无框架，原生 JS） */

const S = {
  chars: [],
  active: new Set(),
  config: null,
  overview: null,
  inventory: null,
  filter: 'all',
  search: '',
  invMode: 'types',
  anon: false,
  sim: [],
  simOnly: false,
  rawOverview: null,
  rawChars: null,
  invSearch: '',
  acked: new Map(),
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
  S.rawChars = st.characters || [];
  S.chars = S.rawChars;
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
    S.rawOverview = data;
    applyAnon();
    // 「已处理」只在游戏内数据真的变了之后才失效：按 data_time 比对，F5 不会重置
    computeLowIsk(data.colonies);
    const alive = new Set((data.colonies || []).map((c) => `${c.character_id}:${c.planet_id}`));
    for (const k of [...S.acked.keys()]) if (!alive.has(k)) S.acked.delete(k);
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
    <div class="group" title="按采集器全程外推 × 修正系数，汇总所有殖民地的预估产出。">
      <span class="caption">预估产量</span>
      ${cell('P1', sum.forecast_p1)}
      ${cell('P2', sum.forecast_p2)}
      ${cell('P3', sum.forecast_p3)}
      ${cell('P4', sum.forecast_p4)}
      <span class="item"><span class="n">${sum.forecast_isk_day ? fmtIsk(sum.forecast_isk_day) : '—'}</span><span class="u">ISK/天</span></span>
      ${sum.forecast_net_isk ? `<span class="item"><span class="n">${fmtIsk(sum.forecast_net_isk)}</span><span class="u">加工净收益</span></span>` : ''}
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
      hint: sum.expiring_2h ? `其中 ${sum.expiring_2h} 个 2 小时内` : '采集器即将停摆',
      cls: sum.expiring_24h ? 'warn' : 'ok',
    },
    {
      label: '已到期',
      value: fmtNum(sum.expired),
      hint: sum.expired ? '采集器已停止采集' : '全部运转中',
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
      hint: `采集器 ${sum.extractors} 台`,
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

/* 爆仓判定：已经爆仓（任一仓储 ≥95%），或按预估到本周期结束会超过 95%。
   纯加工 / 带 P3P4 工厂 / 没有采集器的殖民地没有 forecast，不参与判断。 */
/* 「已处理」标记跟着游戏内数据时间走：数据没变就一直有效，变了自动失效 */
function ackKeyOf(c) {
  return `${c.character_id}:${c.planet_id}`;
}
function isAcked(c) {
  if (!c) return false;
  const stamp = S.acked.get(ackKeyOf(c));
  return stamp !== undefined && stamp === (c.data_time || 0);
}

/* 这块殖民地的优化建议（空数组表示没问题） */
function adviceReasons(c) {
  const f = c.forecast;
  if (!f) return [];
  const excess = Number((S.config && S.config.advice_p0_excess) ?? 300000);
  const low = Number((S.config && S.config.advice_p0_low) ?? 400000);
  const out = [];
  if (!f.pure) {
    if ((f.p0_left || 0) > excess) {
      out.push(`P0 剩余 ${fmtNum(f.p0_left)}，超过过剩判断线 ${fmtNum(excess)}`);
    }
    if ((f.p0_total || 0) > 0 && f.p0_total < low) {
      out.push(`采集总量 ${fmtNum(f.p0_total)}，低于产量过低线 ${fmtNum(low)}`);
    }
  }
  if (S.lowIsk && S.lowIsk.has(ackKeyOf(c))) {
    const isk = f.net_isk || f.isk_day || 0;
    out.push(`预估收益偏低（${fmtIsk(isk)}）`);
  }
  return out;
}

/* 预估收益最低的 5 个殖民地（全局排名，每次取数后重算） */
function computeLowIsk(cols) {
  S.lowIsk = new Set(
    (cols || [])
      .filter((c) => c.forecast && (c.forecast.net_isk || c.forecast.isk_day))
      .sort((a, b) => {
        const ia = a.forecast.net_isk || a.forecast.isk_day || 0;
        const ib = b.forecast.net_isk || b.forecast.isk_day || 0;
        return ia - ib;
      })
      .slice(0, 5)
      .map(ackKeyOf)
  );
}

function colonyOverflow(c) {
  const f = c.forecast;
  const storages = (c.storages || []).filter((s) => s.is_storage);
  // 纯合成殖民地是按库存算好的，理论上不会爆，不参与"已经爆仓"判定，只看预估
  const pure = !!(f && f.pure);
  if (!pure && storages.some((s) => (s.fill || 0) >= 0.95)) return 'full';
  if (f && f.overflow) return 'forecast';
  return null;
}

/* 采集器已经到期 */
function colonyExpired(c) {
  return (c.extractors || []).some((e) => e.expired);
}

function colonyNeedsAttention(c) {
  return !!colonyOverflow(c);
}

/* 罗马数字转阿拉伯数字，用于星球名排序 */
const ROMAN_VAL = { I: 1, V: 5, X: 10, L: 50, C: 100, D: 500, M: 1000 };
function romanToInt(str) {
  const t = String(str || '').toUpperCase();
  let n = 0;
  for (let i = 0; i < t.length; i += 1) {
    const v = ROMAN_VAL[t[i]] || 0;
    const next = ROMAN_VAL[t[i + 1]] || 0;
    n += v < next ? -v : v;
  }
  return n;
}
function planetSortKey(name) {
  const m = String(name || '').match(/\s([IVXLCDM]+)$/);
  return m ? [name.slice(0, m.index), romanToInt(m[1])] : [String(name || ''), 0];
}

/* 匿名模式：把角色名 / 星球名 / 安全等级换成占位符 */
function anonymize(ov) {
  const charMap = new Map();
  (ov.characters || []).forEach((ch, i) => charMap.set(ch.id, `角色 ${i + 1}`));
  const planetMap = new Map();
  [...(ov.colonies || [])]
    .sort((a, b) => {
      const byChar = String(a.character_name).localeCompare(String(b.character_name), 'zh-CN');
      if (byChar) return byChar;
      const [na, ra] = planetSortKey(a.planet_name);
      const [nb, rb] = planetSortKey(b.planet_name);
      return na === nb ? ra - rb : na.localeCompare(nb, 'zh-CN');
    })
    .forEach((c, i) => planetMap.set(`${c.character_id}:${c.planet_id}`, `星球 ${i + 1}`));

  const maskChars = (list) =>
    (list || []).map((ch) => ({ ...ch, name: charMap.get(ch.id) || '角色' }));

  return {
    ...ov,
    characters: maskChars(ov.characters),
    assets: ov.assets ? { ...ov.assets, characters: maskChars(ov.assets.characters) } : ov.assets,
    colonies: (ov.colonies || []).map((c) => ({
      ...c,
      character_name: charMap.get(c.character_id) || '角色',
      planet_name: planetMap.get(`${c.character_id}:${c.planet_id}`) || '星球',
      system_name: '星系',
      planet_type: '行星',
      planet_type_raw: '',
      security: null,
      sec_class: '',
      sec_anon: true,
    })),
  };
}

function applyAnon() {
  if (!S.rawOverview) return;
  S.overview = S.anon ? anonymize(S.rawOverview) : S.rawOverview;
  S.chars = S.anon
    ? (S.rawChars || []).map((c, i) => ({ ...c, name: `角色 ${i + 1}` }))
    : S.rawChars || [];
}

function rerenderAll() {
  applyFilter();
  renderChars();
  if (S.times) renderTimes();
  if (!$('#tab-inventory').hidden) renderInventory();
  if (!$('#tab-materials').hidden) renderMaterials();
}

/* 超过预警线的百分比标红 */
function warnPct(v) {
  if (v == null) return '—';
  const txt = fmtPct(v);
  return v > 0.95 ? `<span class="over">${txt}</span>` : txt;
}

function colonyMatches(c) {
  if (S.filter === 'urgent' && !colonyOverflow(c)) return false;
  if (S.filter === 'expired' && !colonyExpired(c)) return false;
  if (S.filter === 'idle' && !(c.forecast && c.forecast.pure)) return false;
  if (S.filter === 'advice' && !adviceReasons(c).length) return false;
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
  // 有采集器的殖民地，仓储超过 80% 直接判红（需处理）；其它情况沿用 80% 黄 / 95% 红
  const cls = hot && pct >= 0.8 ? 'full' : pct >= 0.95 ? 'full' : pct >= 0.8 ? 'warn' : '';
  const title =
    `${s.type_name}：${fmtNum(s.used_m3, 0)} / ${fmtNum(s.capacity_m3, 0)} m³（${fmtPct(s.fill)}）` +
    (hot && pct >= 0.8 ? '　⚠ 仓储已超 80%，采集器会被顶停' : '');
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

/* 爆仓预警 / 已到期 两个按钮：显示数量，有货才高亮 */
function updateChips(list) {
  const all = list || [];
  const overflow = all.filter(
    (c) => colonyOverflow(c) && !isAcked(c)
  ).length;
  const expired = all.filter(colonyExpired).length;

  const setChip = (filter, label, n, cls) => {
    const chip = document.querySelector(`[data-filter="${filter}"]`);
    if (!chip) return;
    chip.textContent = n ? `${label} ${n}` : label;
    chip.classList.toggle(cls, n > 0);
  };
  setChip('urgent', '爆仓预警', overflow, 'alert');
  setChip('expired', '已到期', expired, 'warn');
}

function renderColonies(list) {
  const grid = $('#colonyGrid');
  updateChips(list);
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
      const col = (activeOverview()?.colonies || []).find((x) => ackKeyOf(x) === key);
      if (isAcked(col)) S.acked.delete(key);
      else S.acked.set(key, (col && col.data_time) || 0);
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

/* 这个星球的主产物：合成/采集星球用预估产物，纯采集退回采集器产物 */
function productOf(c) {
  const f = c.forecast;
  if (f && f.output_name) return f.output_name;
  const e = (c.extractors || [])[0];
  return (e && e.product) || '';
}

function colonyRow(c) {
  const storages = (c.storages || [])
    .filter((s) => s.is_storage)
    // 同一个殖民地既有发射平台又有储藏设施时，发射平台排前面
    .sort((a, b) => Number(isLaunchpad(b)) - Number(isLaunchpad(a)));
  const exts = (c.extractors || []).slice().sort((a, b) => (a.expiry || 0) - (b.expiry || 0));
  // 有采集器且仓储占用超过 80% -> 需处理（仓储满了采集器会被顶停）
  const hot = colonyNeedsAttention(c);
  const rings = [
    ...storages.map((s) => storageRing(s, hot)),
    ...exts.map(extractorRing),
  ].join('');

  const bits = [];
  if (c.factories.length) bits.push(`工厂 ${c.factories.length}`);
  if (!exts.length) bits.push('无采集器');

  const ackKey = `${c.character_id}:${c.planet_id}`;
  const acked = hot && isAcked(c);
  const ackHtml = hot
    ? `<span class="pill ${acked ? 'ok' : 'danger'}" title="${acked ? '已标记处理，下次刷新 ESI 后恢复实际状态' : '已经或即将爆仓，建议收菜'}">${acked ? '已处理' : '爆仓预警'}</span>
       <button class="ack-btn ${acked ? 'undo' : ''}" data-ack="${ackKey}">${acked ? '撤销' : '标记已处理'}</button>`
    : '';

  return `<div class="cg-item" data-urgency="${c.urgency}"${hot && !acked ? ' data-hot="1"' : ''}>
    <div class="cg-row">
      <div class="cg-planet">
        <div class="pname">${esc(c.planet_name)}${
          productOf(c) ? `<span class="prod">${esc(productOf(c))}</span>` : ''
        }</div>
        <div class="pmeta">
          ${ackHtml}
          ${c.planet_type ? `<span class="pill" title="${esc(c.planet_type_raw || '')}">${esc(c.planet_type)}</span>` : ''}
          ${c.security != null
            ? `<span class="sec ${c.sec_class}">${c.security.toFixed(2)}</span>`
            : (c.sec_anon ? '<span class="sec anon">███</span>' : '')}
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

/* 详情页排序权重：指挥中心 → 仓储 → 采集器 → 提取程序 → P1/P2/P3/P4 工厂 → 其他 */
const PIN_RANK = { command_center: 0, storage: 1, extractor: 2 };
const EXTRACTOR_PROGRAM_RANK = 2.5;
const FACTORY_RANK = { P1: 3, P2: 4, P3: 5, P4: 6 };

function pinRank(p) {
  if (p.kind === 'factory') return FACTORY_RANK[(p.factory && p.factory.tier) || ''] ?? 7;
  return PIN_RANK[p.kind] ?? 8;
}

function pinRow(p, count = 1) {
  let content = '';
  let qty = '';
  if (p.kind === 'extractor' && p.extractor) {
    const e = p.extractor;
    content = `${esc(e.product)} <span class="muted">· ${e.heads} 个头 · 每 ${Math.round(e.cycle_time / 60)} 分钟一周期</span>`;
    qty = `${fmtNum(e.qty_per_cycle)}/周期`;
  } else if (p.kind === 'factory' && p.factory) {
    const label = p.factory.product
      ? esc(p.factory.product)
      : '<span class="muted">产物未在路由中体现</span>';
    content = count > 1 ? `${label}<span class="muted">×${count}</span>` : label;
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
    '实测某采集器：游戏面板总量比"按该速率 × 总周期数"的线性外推高约 17%。';
  return `<tr>
    <td class="kind">提取程序</td>
    <td>${esc(e.product)} <span class="muted">· ${e.heads} 头</span></td>
    <td class="num" title="${esc(tip)}">剩余 ${fmtNum(e.est_remaining)} <span class="muted">· ${fmtNum(
    e.rate_per_hour
  )}/时</span></td>
  </tr>`;
}

function detailHtml(c) {
  // 按「指挥中心 → 仓储 → 采集器 → 提取程序 → P1~P4 工厂」重排，
  // 同权重内保持 ESI 原顺序（seq），避免每次刷新位置乱跳
  // 同类型工厂合并成一行（产物 + 层级相同算一类）
  const merged = new Map();
  const entries = [];
  c.pins.forEach((p, i) => {
    if (p.kind !== 'factory' || !p.factory) {
      entries.push({ rank: pinRank(p), seq: i, html: pinRow(p) });
      return;
    }
    const key = `${p.factory.product_id || p.factory.product}|${p.factory.tier}`;
    const hit = merged.get(key);
    if (hit) {
      hit.count += 1;
      return;
    }
    const item = { p, count: 1, rank: pinRank(p), seq: i };
    merged.set(key, item);
    entries.push(item);
  });
  c.extractors.forEach((e, i) =>
    entries.push({ rank: EXTRACTOR_PROGRAM_RANK, seq: c.pins.length + i, html: extractorProgramRow(e) })
  );
  entries.sort((a, b) => a.rank - b.rank || a.seq - b.seq);
  const rows = entries.map((x) => (x.html ? x.html : pinRow(x.p, x.count))).join('');

  const f = c.forecast;
  let fcRow = '';
  if (f) {
    const p0parts = Object.entries(f.p0_by_product || {}).map(([tid, amt]) => {
      const e = (c.extractors || []).find((x) => String(x.product_id) === String(tid));
      return `${esc(e ? e.product : 'P0')} ${fmtNum(amt)}`;
    });
    const outLabel = `预估${f.output_tier || 'P1'}产量`;
    const sub = f.pure
      ? '<span class="muted">· 纯合成，按最高级产物满负荷估算</span>'
      : `<b>预估P0产量</b> ${p0parts.length ? p0parts.join(' + ') : '—'}` +
        `<span class="muted">· 结束时仓储 ${f.end_fill == null ? '—' : fmtPct(f.end_fill)}</span>`;
    const bins = (f.storages || [])
      .map((b) => {
        const hot = (b.end_fill || 0) > 0.95;
        return `<div class="bin-line${hot ? ' hot' : ''}">
          ${esc(b.name || '仓储')}
          <span class="muted">${fmtNum(b.used_m3, 0)} →</span> <b>${fmtNum(b.end_used_m3, 0)}</b>
          <span class="muted">/ ${fmtNum(b.capacity_m3, 0)} m³
            （${b.fill == null ? '—' : fmtPct(b.fill)} → ${warnPct(b.end_fill)}）</span>
          ${b.peak_fill != null && b.end_fill != null && b.peak_fill > b.end_fill + 0.001
            ? `<span class="muted">· 峰值 </span>${warnPct(b.peak_fill)}`
            : ''}
          ${b.is_target ? '<span class="muted">· 产物入库</span>' : ''}
        </div>`;
      })
      .join('');
    fcRow = `<tr class="forecast-row">
      <td class="kind">预估</td>
      <td>
        <b>${outLabel}</b> ${fmtNum(f.output)} ${esc(f.output_name || '')}
        <span class="muted">· ${f.pure ? '加工完库存原料' : `周期 ${Math.round(f.duration / 3600)} 小时`}</span><br>
        ${sub}
        ${f.peak_fill != null && f.end_fill != null && f.peak_fill > f.end_fill + 0.001
          ? `<br><span class="muted">整体峰值 </span>${warnPct(f.peak_fill)}<span class="muted">（第 ${Math.round(f.peak_hour || 0)} 小时）</span>`
          : ''}
        ${bins ? `<div class="bin-list">${bins}</div>` : ''}
      </td>
      <td class="num"><b>${(f.pure ? f.net_isk : f.isk_day) ? fmtIsk(f.pure ? f.net_isk : f.isk_day) : '—'}</b>
        <span class="muted" style="display:block;font-size:10.5px">${f.pure ? '净收益 ISK' : 'ISK/天'}</span></td>
    </tr>`;
  }

  const tips = adviceReasons(c);
  const tipHtml = tips.length
    ? `<div class="advice-inline"><b>优化建议</b><ul>${tips.map((t) => `<li>${esc(t)}</li>`).join('')}</ul></div>`
    : '';

  return `<h4>建筑与内容（${c.pin_count} 个引脚 / ${c.link_count} 条链接 / ${c.route_count} 条路由）</h4>
    ${tipHtml}
    <table class="pin-table">${fcRow}${rows}</table>
    <p class="muted" style="font-size:11px;margin:10px 0 0;line-height:1.6">
      采集器产量按程序开始时的速率估算，实际会随矿脉衰减而下降。<br>
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
  if ($('#setOverviewTtl')) {
    const sel = $('#setOverviewTtl');
    sel.value = String(cfg.overview_ttl ?? 0);
    if (sel.selectedIndex < 0) sel.value = '0'; // 旧配置里可能是已移除的选项
  }
  if ($('#setCorrection')) {
    $('#setCorrection').value = Math.round((cfg.p0_correction ?? 0.17) * 100);
  }
  if ($('#setDecay')) $('#setDecay').value = String(cfg.p0_decay ?? 0.4);
  if ($('#setAdviceExcess')) $('#setAdviceExcess').value = String(Math.round(cfg.advice_p0_excess ?? 300000));
  if ($('#setAdviceLow')) $('#setAdviceLow').value = String(Math.round(cfg.advice_p0_low ?? 400000));
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

/* 每周期产出（用于把产物数量换算成周期数） */
const TIER_RANK = { P0: 0, P1: 1, P2: 2, P3: 3, P4: 4 };

/* 模拟：从产物往下展开到指定层级，算出跑一个周期该层要消耗多少 */
function simConsume(productId, baseTier, recipes) {
  const out = {};
  const rank = TIER_RANK[baseTier] || 1;

  const walk = (pid, qty) => {
    const rec = recipes[String(pid)];
    if (!rec) return;
    for (const [inp, need] of Object.entries(rec.in || {})) {
      const amount = (qty * need) / (rec.out || 1);
      const t = (recipes[String(inp)] || {}).tier;
      if (!t || TIER_RANK[t] < rank) continue; // 比起始层级低的不算
      if (TIER_RANK[t] === rank) out[inp] = (out[inp] || 0) + amount;
      else walk(inp, amount);
    }
  };

  const rec = recipes[String(productId)];
  if (rec) walk(productId, rec.out || 1);
  return out;
}

/* 起始层级只能比产物低：P4 可选 P1~P3，P3 可选 P1~P2，P2 只能 P1 */
function baseTiers(productId) {
  const rec = ((S.overview || {}).summary || {}).recipes || {};
  const rank = TIER_RANK[(rec[String(productId)] || {}).tier] || 4;
  return ['P1', 'P2', 'P3'].filter((t) => TIER_RANK[t] < rank);
}

/* 合成原料核算：合成工厂 / 模拟 / 原料平衡 */
function renderMaterials() {
  const ov = S.overview;
  if (!ov) return;
  const sum = ov.summary || {};
  const recipes = sum.recipes || {};
  const names = sum.mat_names || {};
  const cols = ov.colonies || [];
  const pures = cols.filter((c) => c.forecast && c.forecast.pure);

  // ---- 表1：合成殖民地一览 ----
  const rows1 = pures.map(
    (c) => `<tr>
      <td>${esc(c.character_name)}</td>
      <td>${esc(c.planet_name)}</td>
      <td>${esc(c.forecast.output_name || '—')}</td>
      <td class="num">${c.forecast.cycles_day ? fmtNum(c.forecast.cycles_day, 0) : '—'}</td>
      <td class="num">${fmtNum(c.forecast.output)}</td>
    </tr>`
  );
  $('#matColonies').innerHTML = pures.length
    ? `<table class="mat-table">
        <thead><tr>
          <th>人物</th><th>星球</th><th>产物</th>
          <th class="num">每天运行周期</th><th class="num">数量</th>
        </tr></thead>
        <tbody>${rows1.join('')}</tbody>
      </table>`
    : '<p class="muted">没有合成工厂。</p>';

  // ---- 表2：模拟添加合成工厂 ----
  if (!S.sim.length) S.sim.push({ product: '', base: 'P1', cyclesDay: 0 });

  const productOpts = Object.entries(recipes)
    .filter(([, r]) => r.tier !== 'P1') // P1 没有模拟的必要
    .sort((a, b) => {
      const ta = TIER_RANK[a[1].tier] || 0;
      const tb = TIER_RANK[b[1].tier] || 0;
      return ta === tb ? a[1].name.localeCompare(b[1].name) : tb - ta; // P4 → P3 → P2
    })
    .map(
      ([tid, r]) =>
        `<option value="${tid}">${esc(r.tier)} · ${esc(names[tid] || r.name)}</option>`
    )
    .join('');

  const simRows = S.sim
    .map((row, i) => {
      const perCycle = row.product ? simConsume(row.product, row.base, recipes) : {};
      const detail = Object.entries(perCycle)
        .map(([tid, amt]) => `${esc(names[tid] || tid)} ${fmtNum(amt * (row.cyclesDay || 0))}`)
        .join('、');
      return `<tr>
        <td>
          <select data-sim="${i}" data-k="product">
            <option value="">— 选择产物 —</option>${productOpts}
          </select>
        </td>
        <td>
          <select data-sim="${i}" data-k="base">
            ${baseTiers(row.product)
              .map((t) => `<option value="${t}"${row.base === t ? ' selected' : ''}>${t}</option>`)
              .join('')}
          </select>
        </td>
        <td class="num"><input data-sim="${i}" data-k="cyclesDay" type="number" min="0" step="1"
              value="${row.cyclesDay || 0}" style="width:90px;text-align:right"></td>
        <td>${detail || '<span class="muted">—</span>'}</td>
        <td><button class="chip" data-simdel="${i}">删除</button></td>
      </tr>`;
    })
    .join('');

  $('#matSim').innerHTML = `<table class="mat-table">
      <thead><tr>
        <th>产物</th><th>起始层级</th><th class="num">每天运转周期</th><th>每天消耗</th><th></th>
      </tr></thead>
      <tbody>${simRows}</tbody>
    </table>
    <div style="margin-top:10px;display:flex;gap:8px;align-items:center">
      <button class="chip" id="simAdd">＋ 添加一行</button>
      <span class="muted" style="font-size:11.5px">
        按「每天运转周期」填：半速 P4 工厂 12 个周期/天、满速 24 个/天，填 120 就是 10 个满速工厂
      </span>
    </div>`;

  // 回填下拉的当前值
  $('#matSim').querySelectorAll('select[data-k="product"]').forEach((el) => {
    const row = S.sim[Number(el.dataset.sim)];
    if (row && row.product) el.value = row.product;
  });

  // 事件
  const onlyBtn = $('#simOnly');
  if (onlyBtn) {
    onlyBtn.classList.toggle('active', !!S.simOnly);
    onlyBtn.onclick = () => {
      S.simOnly = !S.simOnly;
      renderMaterials();
    };
  }
  $('#simAdd').onclick = () => {
    S.sim.push({ product: '', base: 'P1', cyclesDay: 0 });
    renderMaterials();
  };
  $('#matSim').querySelectorAll('[data-simdel]').forEach((btn) => {
    btn.onclick = () => {
      S.sim.splice(Number(btn.dataset.simdel), 1);
      renderMaterials();
    };
  });
  $('#matSim').querySelectorAll('[data-sim]').forEach((el) => {
    el.onchange = () => {
      const row = S.sim[Number(el.dataset.sim)];
      if (!row) return;
      const k = el.dataset.k;
      row[k] = k === 'cyclesDay' ? Math.max(0, Number(el.value || 0)) : el.value;
      renderMaterials();
    };
  });

  // ---- 表3：实际 + 模拟 ----
  const agg = {};
  const bump = (tid, key, v) => {
    const k = String(tid);
    if (!agg[k]) agg[k] = { used: 0, made: 0, rateUsed: 0, rateMade: 0, stock: 0 };
    agg[k][key] += v;
  };

  // 库存总量：直接取「物资汇总」的口径（海关 + 殖民地 + 建筑）
  const stockByType = {};
  if (S.inventory && S.inventory.rows) {
    for (const r of S.inventory.rows) {
      stockByType[r.type_id] = (stockByType[r.type_id] || 0) + r.amount;
    }
  }

  for (const c of cols) {
    const f = c.forecast;
    if (!f || !f.mat) continue;
    // 只看模拟：忽略现有合成工厂（它们被模拟的那批替代），采集类殖民地照常计入
    if (S.simOnly && f.pure) continue;
    const days = (f.duration || 0) / 86400;
    Object.entries(f.mat.consumed || {}).forEach(([tid, v]) => {
      bump(tid, 'used', v);
      if (days > 0) bump(tid, 'rateUsed', v / days);
    });
    Object.entries(f.mat.produced || {}).forEach(([tid, v]) => {
      bump(tid, 'made', v);
      if (days > 0) bump(tid, 'rateMade', v / days);
    });
    if (!S.inventory) {
      for (const b of c.storages || []) {
        if (!b.is_storage) continue;
        for (const it of b.items || []) bump(it.type_id, 'stock', it.amount);
      }
    }
  }

  // 叠加模拟：填的是「每天运转周期」，所以直接乘出每天消耗
  const durations = cols.map((c) => (c.forecast || {}).duration || 0).filter((d) => d > 0);
  const refDays = durations.length
    ? Math.max(1, durations.reduce((a, b) => a + b, 0) / durations.length / 86400)
    : 1;

  for (const row of S.sim) {
    if (!row.product || !row.cyclesDay) continue;
    for (const [tid, amt] of Object.entries(simConsume(row.product, row.base, recipes))) {
      const daily = amt * row.cyclesDay;
      bump(tid, 'rateUsed', daily);
      bump(tid, 'used', daily * refDays);
    }
  }

  const rows3 = Object.entries(agg)
    // 配方表里没有的物资（P0）直接排除
    .filter(([tid, a]) => !!recipes[String(tid)] && (a.used > 0 || a.made > 0))
    .sort((a, b) => {
      const ta = TIER_RANK[(recipes[a[0]] || {}).tier] || 0;
      const tb = TIER_RANK[(recipes[b[0]] || {}).tier] || 0;
      if (ta !== tb) return ta - tb;
      return String(names[a[0]] || a[0]).localeCompare(String(names[b[0]] || b[0]), 'zh-CN');
    })
    .map(([tid, a]) => {
      const net = a.rateMade - a.rateUsed;
      const netTxt =
        Math.abs(net) < 1e-9
          ? '<span class="muted">0</span>'
          : net > 0
            ? `<span class="pos">+${fmtNum(net)}</span>`
            : `<span class="neg">${fmtNum(net)}</span>`;
      const stock = stockByType[tid] != null ? stockByType[tid] : a.stock;
      const daysLeft = net < -1e-9 ? (stock > 0 ? `${fmtNum(stock / -net, 1)} 天` : '0 天') : '∞';
      const tier = (recipes[String(tid)] || {}).tier || '';
      return `<tr>
        <td>${tier ? `<span class="tier ${tier}">${tier}</span> ` : ''}${esc(names[tid] || tid)}</td>
        <td class="num">${fmtNum(a.used)}</td>
        <td class="num">${fmtNum(a.made)}</td>
        <td class="num">${fmtNum(a.rateUsed)}</td>
        <td class="num">${fmtNum(a.rateMade)}</td>
        <td class="num">${netTxt}</td>
        <td class="num">${fmtNum(stockByType[tid] != null ? stockByType[tid] : a.stock)}</td>
        <td class="num">${daysLeft}</td>
      </tr>`;
    });

  $('#matBalance').innerHTML = rows3.length
    ? `<table class="mat-table">
        <thead><tr>
          <th>原料种类</th><th class="num">消耗总和</th><th class="num">预估产量总和</th>
          <th class="num">消耗/天</th><th class="num">预估产量/天</th><th class="num">核算 数量/天</th>
          <th class="num">库存总量</th><th class="num">可供消耗天数</th>
        </tr></thead>
        <tbody>${rows3.join('')}</tbody>
      </table>
      <p class="muted" style="font-size:11.5px;margin:8px 0 0">
        核算 = 预估产量/天 − 消耗/天。正数是结余（绿），负数是短缺（红）。
        只统计真正进出仓库的物资，殖民地内部自行合成的中间产物不计，P0 也不计。
      </p>`
    : '<p class="muted">没有可核算的数据。</p>';
}

function switchTab(name) {
  $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  $$('.panel-tab').forEach((p) => (p.hidden = p.id !== `tab-${name}`));
  if (name === 'inventory') loadInventory();
  if (name === 'materials') {
    // 库存总量要用「物资汇总」的数据，没加载过就先拉一次
    if (!S.inventory) loadInventory().then(renderMaterials);
    else renderMaterials();
  }
  if (name === 'chars') renderChars();
}

function bind() {
  $$('.tab').forEach((t) => (t.onclick = () => switchTab(t.dataset.tab)));
  $('#anonBtn').onclick = () => {
    S.anon = !S.anon;
    $('#anonBtn').classList.toggle('on', S.anon);
    applyAnon();
    rerenderAll();
    toast(S.anon ? '已开启匿名模式' : '已关闭匿名模式');
  };
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

  // 优化推荐阈值：只影响判定，改完立即保存并刷新列表
  [['#setAdviceExcess', 'advice_p0_excess'], ['#setAdviceLow', 'advice_p0_low']].forEach(
    ([sel, key]) => {
      const el = $(sel);
      if (!el) return;
      el.onchange = async (e) => {
        const v = Math.max(0, Math.round(Number(e.target.value || 0)));
        e.target.value = v;
        try {
          await api('/api/settings', { method: 'POST', body: JSON.stringify({ [key]: v }) });
          S.config = { ...(S.config || {}), [key]: v };
          toast(`已保存：${fmtNum(v)}`);
          if (S.overview) rerenderAll();
        } catch (err) {
          toast('保存失败：' + err.message, true);
        }
      };
    }
  );

  // 衰减曲线：改完立即保存并重算
  if ($('#setDecay')) {
    $('#setDecay').onchange = async (e) => {
      const v = Math.max(0, Math.min(1, Math.round(Number(e.target.value || 0) * 100) / 100));
      e.target.value = v;
      try {
        await api('/api/settings', { method: 'POST', body: JSON.stringify({ p0_decay: v }) });
        S.config = { ...(S.config || {}), p0_decay: v };
        toast(`已保存：末期产量为初期的 ${Math.round(v * 100)}%`);
        await loadOverview(false, true);
      } catch (err) {
        toast('保存失败：' + err.message, true);
      }
    };
  }

  // 产量修正：改完立即保存并重算（服务端保存时会清掉总览缓存）
  if ($('#setCorrection')) {
    $('#setCorrection').onchange = async (e) => {
      const pct = Math.max(-100, Math.min(100, Math.round(Number(e.target.value || 0))));
      e.target.value = pct;
      try {
        await api('/api/settings', {
          method: 'POST',
          body: JSON.stringify({ p0_correction: pct / 100 }),
        });
        S.config = { ...(S.config || {}), p0_correction: pct / 100 };
        toast(`已保存：${pct > 0 ? '+' : ''}${pct}%，正在按新系数重算…`);
        await loadOverview(false, true);
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

/* 每分钟把采集器圆环按当前时间重算一遍（不请求 ESI） */
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
    const name = el.dataset.product || '采集器';
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
    S.invMode = params.get('inv') === 'rows' ? 'rows' : 'types';
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
