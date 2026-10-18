"use strict";
// 中大 AI 選課助理 — 前端邏輯（階段 5）
// 使用者資料只存於瀏覽器 sessionStorage，不傳回伺服器保存。

const $ = (sel) => document.querySelector(sel);
const api = async (url, opts) => {
  const r = await fetch(url, opts);
  const j = await r.json();
  if (!j.success) throw new Error(j.error || "請求失敗");
  return j;
};
const splitList = (s) => (s || "").split(/[,，\n]/).map(x => x.trim()).filter(Boolean);

let META = null;
let FIXED = {};        // id -> course brief
let AVOID = [];        // [[day, period], ...]（不含早八，早八以 checkbox 控制）
let SCHEDULES = [];    // 最近一次產生的課表
let ACTIVE = 0;

// ---------------------------------------------------------- 狀態儲存
function saveState() {
  const state = {
    dept: $("#u-dept").value,
    grade: $("#u-grade").value,
    completed: $("#u-completed").value,
    prefText: $("#pref-text").value,
    fixed: FIXED,
  };
  try { sessionStorage.setItem("ncu_planner", JSON.stringify(state)); } catch (e) {}
}
function loadState() {
  let s;
  try { s = JSON.parse(sessionStorage.getItem("ncu_planner") || "{}"); } catch (e) { s = {}; }
  if (s.dept) $("#u-dept").value = s.dept;
  if (s.grade) $("#u-grade").value = s.grade;
  if (s.completed) $("#u-completed").value = s.completed;
  if (s.prefText) $("#pref-text").value = s.prefText;
  if (s.fixed) { FIXED = s.fixed; renderFixedChips(); }
}

// ---------------------------------------------------------- 初始化
async function init() {
  let m;
  try {
    m = await api("/api/meta");
  } catch (e) {
    $("#meta-status").innerHTML =
      `<span class="pill pill-warn">無法載入課程資料：${e.message}</span>`;
    return;
  }
  META = m;
  renderMetaStatus(m);
  // 系所自動完成
  const dl = $("#dept-list");
  (m.depts || []).forEach(d => {
    const o = document.createElement("option"); o.value = d; dl.appendChild(o);
  });
  // 類別 / 通識領域篩選
  fillSelect("#f-category", m.categories);
  fillSelect("#f-gedomain", m.ge_domains);
  // 想空的星期 checkboxes
  const fd = $("#p-freedays");
  (m.default_days || [1,2,3,4,5]).forEach(d => {
    const lab = document.createElement("label");
    lab.innerHTML = `<input type="checkbox" value="${d}"> 週${m.weekdays[d]}`;
    fd.appendChild(lab);
  });
  loadState();
  bindEvents();
}

function fillSelect(sel, items) {
  const el = $(sel);
  (items || []).forEach(v => {
    const o = document.createElement("option"); o.value = v; o.textContent = v;
    el.appendChild(o);
  });
}

// 顯示資料來源 / 語意索引 / LLM 模式，讓使用者知道推薦品質
function renderMetaStatus(m) {
  const box = $("#meta-status");
  const pills = [];
  if (m.semester_label) {
    const upd = m.data_updated ? `・更新 ${m.data_updated}` : "";
    pills.push(`<span class="pill pill-info">${m.semester_label}${upd}</span>`);
  }
  pills.push(`<span class="pill">課程 ${m.course_count} 門</span>`);
  if (!m.semantic_available) {
    pills.push(`<span class="pill pill-warn">語意索引未建立，推薦改用關鍵字比對</span>`);
  }
  if (m.llm_provider === "mock") {
    pills.push(`<span class="pill pill-warn">LLM 為 mock 模式，偏好解析與推薦理由為示範用假資料</span>`);
  }
  box.innerHTML = pills.join("");
}

function bindEvents() {
  $("#fixed-search-btn").onclick = searchFixed;
  $("#fixed-search").addEventListener("keydown", e => { if (e.key === "Enter") searchFixed(); });
  $("#parse-btn").onclick = parsePrefs;
  $("#gen-btn").onclick = generate;
  ["#u-dept","#u-grade","#u-completed","#pref-text"].forEach(s =>
    $(s).addEventListener("change", saveState));
}

// ---------------------------------------------------------- 步驟1：固定課
async function searchFixed() {
  const q = $("#fixed-search").value.trim();
  const cat = $("#f-category").value;
  const ge = $("#f-gedomain").value;
  if (!q && !cat && !ge) {
    $("#fixed-search-status").textContent = "請輸入關鍵字，或選擇類別／通識領域。";
    return;
  }
  const btn = $("#fixed-search-btn");
  const status = $("#fixed-search-status");
  const ul = $("#fixed-search-results");
  btn.disabled = true; status.textContent = "搜尋中…";
  const limit = 30;
  const params = new URLSearchParams({ limit: String(limit) });
  if (q) params.set("q", q);
  if (cat) params.set("category", cat);
  if (ge) params.set("ge_domain", ge);
  let j;
  try {
    j = await api(`/api/courses?${params.toString()}`);
  } catch (e) {
    status.textContent = "搜尋失敗：" + e.message;
    btn.disabled = false;
    return;
  }
  btn.disabled = false;
  ul.innerHTML = "";
  if (!j.courses.length) {
    status.textContent = "查無符合條件的課程。";
    return;
  }
  status.textContent = j.count >= limit
    ? `符合條件者眾，僅顯示前 ${limit} 筆，請再加上關鍵字縮小範圍。`
    : `共 ${j.count} 筆`;
  j.courses.forEach(c => {
    const li = document.createElement("li");
    li.innerHTML = `<span><b>${c.name}</b> <span class="meta">${c.code}・${c.dept}・${c.credits}學分・${fmtTimes(c.times)}</span></span>`;
    const b = document.createElement("button");
    b.className = "btn"; b.textContent = FIXED[c.id] ? "已加入" : "加入";
    b.disabled = !!FIXED[c.id];
    b.onclick = () => { FIXED[c.id] = c; renderFixedChips(); b.textContent = "已加入"; b.disabled = true; saveState(); };
    li.appendChild(b); ul.appendChild(li);
  });
}
function renderFixedChips() {
  const box = $("#fixed-chips"); box.innerHTML = "";
  Object.values(FIXED).forEach(c => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = `${c.name} <span class="meta">${fmtTimes(c.times)}</span>`;
    const x = document.createElement("button"); x.textContent = "×";
    x.setAttribute("aria-label", `移除固定課 ${c.name}`);
    x.onclick = () => { delete FIXED[c.id]; renderFixedChips(); saveState(); };
    chip.appendChild(x); box.appendChild(chip);
  });
}

// ---------------------------------------------------------- 步驟2：偏好
async function parsePrefs() {
  const text = $("#pref-text").value.trim();
  const btn = $("#parse-btn");
  const status = $("#parse-status");
  if (!text) { status.textContent = "請先輸入白話需求。"; return; }
  saveState();
  btn.disabled = true; status.textContent = "解析中…";
  try {
    const j = await api("/api/parse_preferences", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    fillPrefForm(j.preferences);
    status.textContent = "已解析，可在下方確認或修改。";
  } catch (e) {
    status.textContent = "解析失敗：" + e.message;
  } finally {
    btn.disabled = false;
  }
}
function fillPrefForm(p) {
  $("#pref-parsed").classList.remove("hidden");
  $("#p-min").value = p.min_credits ?? "";
  $("#p-max").value = p.max_credits ?? "";
  $("#p-topics").value = (p.topics || []).join(", ");
  $("#p-gedomains").value = (p.ge_domains || []).join(", ");
  $("#p-langs").value = (p.languages || []).join(", ");
  // free days
  document.querySelectorAll("#p-freedays input").forEach(cb =>
    cb.checked = (p.free_days || []).includes(Number(cb.value)));
  // avoid：早八獨立處理，其餘列成 chips
  const days = META.default_days || [1,2,3,4,5];
  const isEarly8 = days.every(d => (p.avoid_periods || []).some(a => a[0] === d && a[1] === "1"));
  $("#p-early8").checked = isEarly8;
  AVOID = (p.avoid_periods || []).filter(a => !(isEarly8 && a[1] === "1"));
  renderAvoidChips();
}
function renderAvoidChips() {
  const box = $("#p-avoid"); box.innerHTML = "";
  AVOID.forEach((a, i) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = slotLabel(a[0], a[1]);
    const x = document.createElement("button"); x.textContent = "×";
    x.setAttribute("aria-label", `移除避開時段 ${slotLabel(a[0], a[1])}`);
    x.onclick = () => { AVOID.splice(i, 1); renderAvoidChips(); };
    chip.appendChild(x); box.appendChild(chip);
  });
}
function readSpec() {
  const freeDays = [...document.querySelectorAll("#p-freedays input:checked")].map(cb => Number(cb.value));
  let avoid = AVOID.slice();
  if ($("#p-early8").checked) (META.default_days || [1,2,3,4,5]).forEach(d => avoid.push([d, "1"]));
  const num = (v) => v === "" ? null : Number(v);
  return {
    min_credits: num($("#p-min").value),
    max_credits: num($("#p-max").value),
    free_days: freeDays,
    avoid_periods: avoid,
    topics: splitList($("#p-topics").value),
    ge_domains: splitList($("#p-gedomains").value),
    languages: splitList($("#p-langs").value),
  };
}

// ---------------------------------------------------------- 步驟3：產生課表
async function generate() {
  saveState();
  const btn = $("#gen-btn");
  const status = $("#gen-status");
  btn.disabled = true;
  status.textContent = "計算中…";
  const body = {
    fixed_ids: Object.keys(FIXED),
    user: {
      dept: $("#u-dept").value || null,
      grade: $("#u-grade").value || null,
      completed_courses: splitList($("#u-completed").value),
    },
    preferences: $("#pref-parsed").classList.contains("hidden") ? {} : readSpec(),
    max_schedules: 3,
  };
  try {
    const j = await api("/api/schedules", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    SCHEDULES = j.schedules;
    window._freeSlots = j.free_slots;
    window._avoidSet = buildAvoidSet(j.preferences_used);
    if (!SCHEDULES.length) {
      status.textContent = "找不到符合條件的課表，請放寬學分範圍或偏好。";
      $("#scheme-tabs").innerHTML = ""; $("#schedule-grid").innerHTML = ""; $("#schedule-courses").innerHTML = "";
      $("#schedule-empty").classList.remove("hidden");
      return;
    }
    $("#schedule-empty").classList.add("hidden");
    const s = j.stats;
    const capNote = s.added_cap
      ? `；未設學分上限，預設最多加 ${s.added_cap} 門通識/語言課（可在偏好設定學分上限調整）`
      : "";
    status.textContent = `產生 ${SCHEDULES.length} 組（搜尋 ${s.nodes} 節點、${s.elapsed}s${s.hit_limit ? "，已達上限回傳最佳結果" : ""}）${capNote}`;
    ACTIVE = 0; renderTabs(); renderScheme();
  } catch (e) {
    status.textContent = "發生錯誤：" + e.message;
  } finally {
    btn.disabled = false;
  }
}

// 由偏好算出「使用者主動避開」的格子集合（避開節次 + 整天空著的星期）
function buildAvoidSet(prefs) {
  const set = new Set();
  if (!prefs) return set;
  (prefs.avoid_periods || []).forEach(a => set.add(a[0] + "-" + a[1]));
  const periods = META.period_order || [];
  (prefs.free_days || []).forEach(d => periods.forEach(p => set.add(d + "-" + p)));
  return set;
}
function renderTabs() {
  const tabs = $("#scheme-tabs"); tabs.innerHTML = "";
  tabs.setAttribute("role", "tablist");
  SCHEDULES.forEach((s, i) => {
    const t = document.createElement("div");
    t.className = "tab" + (i === ACTIVE ? " active" : "");
    t.textContent = `方案 ${i + 1}（${s.total_credits} 學分）`;
    t.setAttribute("role", "tab");
    t.setAttribute("tabindex", "0");
    t.setAttribute("aria-selected", String(i === ACTIVE));
    const activate = () => { ACTIVE = i; renderTabs(); renderScheme(); };
    t.onclick = activate;
    t.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); } };
    tabs.appendChild(t);
  });
}
function renderScheme() {
  const sch = SCHEDULES[ACTIVE];
  renderGrid(sch);
  renderCourseList(sch);
}

// 週課表格線圖
function renderGrid(sch) {
  const order = META.period_order || [];
  // 建立 (day,period) -> 課程
  const cell = {};
  sch.courses.forEach(c => c.times.forEach(([d, p]) => { cell[d + "-" + p] = c; }));
  const freeSet = new Set((window._freeSlots || []).map(a => a[0] + "-" + a[1]));
  const avoidSet = window._avoidSet || new Set();

  // 要顯示的星期：預設一～五，若有課排在六／日則補上
  const baseDays = META.default_days || [1, 2, 3, 4, 5];
  const usedDays = new Set(sch.courses.flatMap(c => c.times.map(t => t[0])));
  const days = [1, 2, 3, 4, 5, 6, 0].filter(d => baseDays.includes(d) || usedDays.has(d));

  // 要顯示的節次：至少到第 9 節，若有課排到更晚（A–F）則延伸到該節
  const lastIdx = Math.max(
    order.indexOf("9"),
    ...sch.courses.flatMap(c => c.times.map(t => order.indexOf(t[1])))
  );
  const show = order.slice(0, lastIdx + 1);

  let html = '<div class="legend">'
    + '<span><i class="swatch sw-fixed"></i>固定課</span>'
    + '<span><i class="swatch sw-added"></i>推薦課</span>'
    + '<span><i class="swatch sw-free"></i>空堂</span>'
    + '<span><i class="swatch sw-avoid"></i>避開</span></div>';
  html += "<table class='grid'><thead><tr><th>節次</th>";
  days.forEach(d => html += `<th>週${META.weekdays[d]}</th>`);
  html += "</tr></thead><tbody>";

  const skip = new Set();   // 已被上方 rowspan 涵蓋、需跳過的 day-period
  show.forEach((p, pi) => {
    const [t0, t1] = META.period_times[p] || ["", ""];
    html += `<tr><th>${p}<br><span class="ptime">${t0}–${t1}</span></th>`;
    days.forEach(d => {
      const key = d + "-" + p;
      if (skip.has(key)) return;   // 由上一格 rowspan 涵蓋
      const c = cell[key];
      if (c) {
        // 合併同一門課的連續節次
        let span = 1;
        while (pi + span < show.length && (cell[d + "-" + show[pi + span]] || {}).id === c.id) {
          skip.add(d + "-" + show[pi + span]);
          span++;
        }
        const cls = c.role === "fixed" ? "fixed" : "added";
        const room = c.classroom ? `<span class="c-room">${c.classroom}</span>` : "";
        html += `<td class="${cls}" rowspan="${span}" title="${c.name}（${c.code}）">`
          + `<span class="c-name">${c.name}</span>${room}</td>`;
      } else if (avoidSet.has(key)) {
        html += `<td class="avoid"></td>`;
      } else if (freeSet.has(key)) {
        html += `<td class="free"></td>`;
      } else {
        html += `<td></td>`;
      }
    });
    html += "</tr>";
  });
  html += "</tbody></table>";
  $("#schedule-grid").innerHTML = html;
}

let ALT_LOOKUP = {};   // altId -> brief（供「選這門」加入固定課）

function renderCourseList(sch) {
  const box = $("#schedule-courses"); box.innerHTML = "";
  ALT_LOOKUP = {};
  sch.courses.forEach(c => {
    const el = document.createElement("div");
    el.className = "course-item " + c.role;
    const e = c.eligibility || {};
    const badge = c.role === "added"
      ? `<span class="badge ${e.status}">${e.status}</span>` : "";
    let html = `<div class="course-title">${c.name} <span class="course-sub">${c.code}</span>${badge}
      ${c.role === "fixed" ? '<span class="badge" style="background:var(--fixed-bg);color:var(--fixed)">固定課</span>' : ''}</div>
      <div class="course-sub">${c.dept}・${(c.teacher||[]).join("、")}・${c.credits} 學分・${fmtTimes(c.times)}${c.classroom ? "・" + c.classroom : ""}</div>`;
    if (c.reason) html += `<div class="reason">💡 ${c.reason}</div>`;
    // 資格原因
    if (c.role === "added" && e.reasons && e.reasons.length) {
      html += `<div class="course-sub">資格：${e.reasons.join("；")}</div>`;
    }
    // 同時段其他可選（讓使用者在同一時段比較幾門課再決定）
    if (c.role === "added" && c.alternatives && c.alternatives.length) {
      const aid = "alt_" + c.id;
      const n = c.alternatives.length;
      html += `<span class="notes-toggle js-toggle" data-t="${aid}" role="button" tabindex="0"
        aria-expanded="false" aria-controls="${aid}"
        data-show="同時段其他可選（${n}）" data-hide="收合可選清單">同時段其他可選（${n}）</span>
        <ul class="alt-list hidden" id="${aid}">`;
      c.alternatives.forEach(a => {
        ALT_LOOKUP[a.id] = a;
        const tags = [a.ge_domain, a.is_language ? (a.language || "語言") : null]
          .filter(Boolean).join("・");
        const eb = a.eligibility_status
          ? `<span class="badge ${a.eligibility_status}">${a.eligibility_status}</span>` : "";
        html += `<li><span><b>${a.name}</b>${eb}
          <span class="meta">${a.code}・${a.credits}學分・${fmtTimes(a.times)}${tags ? "・" + tags : ""}</span></span>
          <button class="btn btn-sm alt-pick" data-alt="${a.id}">選這門</button></li>`;
      });
      html += `</ul>`;
    }
    // 原始備註（必附，供使用者自行確認）
    if (c.eligibility && c.eligibility.raw_notes) {
      const id = "n_" + c.id;
      html += `<span class="notes-toggle js-toggle" data-t="${id}" role="button" tabindex="0"
        aria-expanded="false" aria-controls="${id}"
        data-show="顯示原始備註" data-hide="隱藏原始備註">顯示原始備註</span>
        <div class="notes-box hidden" id="${id}">${c.eligibility.raw_notes}</div>`;
    }
    el.innerHTML = html;
    box.appendChild(el);
  });
  box.querySelectorAll(".js-toggle").forEach(t => {
    const toggle = () => {
      const b = $("#" + t.dataset.t);
      b.classList.toggle("hidden");
      const hidden = b.classList.contains("hidden");
      t.textContent = hidden ? t.dataset.show : t.dataset.hide;
      t.setAttribute("aria-expanded", String(!hidden));
    };
    t.onclick = toggle;
    t.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } };
  });
  box.querySelectorAll(".alt-pick").forEach(b => {
    b.onclick = () => {
      const a = ALT_LOOKUP[b.dataset.alt];
      if (!a) return;
      FIXED[a.id] = a;
      renderFixedChips();
      saveState();
      b.textContent = "已加入"; b.disabled = true;
      $("#gen-status").textContent = `已把「${a.name}」加入固定課，請再按「產生課表」更新。`;
    };
  });
}

// ---------------------------------------------------------- 小工具
function slotLabel(d, p) {
  const t = (META.period_times[p] || ["", ""])[0];
  return `週${META.weekdays[d]} 第${p}節 ${t}`;
}
function fmtTimes(times) {
  if (!times || !times.length) return "時間未定";
  const byDay = {};
  times.forEach(([d, p]) => { (byDay[d] = byDay[d] || []).push(p); });
  return Object.entries(byDay).map(([d, ps]) => `週${META.weekdays[d]}${ps.join("")}`).join(" ");
}

init();
