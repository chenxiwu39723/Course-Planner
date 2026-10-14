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
  const m = await api("/api/meta");
  META = m;
  // 系所自動完成
  const dl = $("#dept-list");
  (m.depts || []).forEach(d => {
    const o = document.createElement("option"); o.value = d; dl.appendChild(o);
  });
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
  if (!q) return;
  const j = await api(`/api/courses?q=${encodeURIComponent(q)}&limit=30`);
  const ul = $("#fixed-search-results");
  ul.innerHTML = "";
  if (!j.courses.length) { ul.innerHTML = "<li class='meta'>查無課程</li>"; return; }
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
    x.onclick = () => { delete FIXED[c.id]; renderFixedChips(); saveState(); };
    chip.appendChild(x); box.appendChild(chip);
  });
}

// ---------------------------------------------------------- 步驟2：偏好
async function parsePrefs() {
  const text = $("#pref-text").value;
  saveState();
  const j = await api("/api/parse_preferences", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  fillPrefForm(j.preferences);
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
  const status = $("#gen-status");
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
    if (!SCHEDULES.length) {
      status.textContent = "找不到符合條件的課表，請放寬學分範圍或偏好。";
      $("#scheme-tabs").innerHTML = ""; $("#schedule-grid").innerHTML = ""; $("#schedule-courses").innerHTML = "";
      return;
    }
    const s = j.stats;
    status.textContent = `產生 ${SCHEDULES.length} 組（搜尋 ${s.nodes} 節點、${s.elapsed}s${s.hit_limit ? "，已達上限回傳最佳結果" : ""}）`;
    ACTIVE = 0; renderTabs(); renderScheme();
  } catch (e) {
    status.textContent = "發生錯誤：" + e.message;
  }
}
function renderTabs() {
  const tabs = $("#scheme-tabs"); tabs.innerHTML = "";
  SCHEDULES.forEach((s, i) => {
    const t = document.createElement("div");
    t.className = "tab" + (i === ACTIVE ? " active" : "");
    t.textContent = `方案 ${i + 1}（${s.total_credits} 學分）`;
    t.onclick = () => { ACTIVE = i; renderTabs(); renderScheme(); };
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
  const days = META.default_days || [1,2,3,4,5];
  const periods = META.period_order || [];
  // 建立 (day,period) -> 課程角色/代號
  const cell = {};
  sch.courses.forEach(c => c.times.forEach(([d, p]) => { cell[d + "-" + p] = c; }));
  const freeSet = new Set((window._freeSlots || []).map(a => a[0] + "-" + a[1]));

  let html = '<div class="legend">'
    + '<span><i class="swatch" style="background:var(--fixed-bg)"></i>固定課</span>'
    + '<span><i class="swatch" style="background:var(--added-bg)"></i>推薦課</span>'
    + '<span><i class="swatch" style="background:var(--free-bg)"></i>空堂</span>'
    + '<span><i class="swatch" style="background:var(--avoid-bg)"></i>避開/無</span></div>';
  html += "<table class='grid'><thead><tr><th>節次</th>";
  days.forEach(d => html += `<th>週${META.weekdays[d]}</th>`);
  html += "</tr></thead><tbody>";
  // 只顯示到有課或常用節次（1..9 + A..D）
  const show = periods.filter(p => !["E", "F"].includes(p));
  show.forEach(p => {
    const t = (META.period_times[p] || ["", ""])[0];
    html += `<tr><th>${p}<br><span style="font-weight:400;font-size:.7em">${t}</span></th>`;
    days.forEach(d => {
      const key = d + "-" + p;
      const c = cell[key];
      if (c) {
        const cls = c.role === "fixed" ? "fixed" : "added";
        html += `<td class="${cls}" title="${c.name}">${c.code}</td>`;
      } else if (freeSet.has(key)) {
        html += `<td class="free"></td>`;
      } else {
        html += `<td class="avoid"></td>`;
      }
    });
    html += "</tr>";
  });
  html += "</tbody></table>";
  $("#schedule-grid").innerHTML = html;
}

function renderCourseList(sch) {
  const box = $("#schedule-courses"); box.innerHTML = "";
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
    // 原始備註（必附，供使用者自行確認）
    if (c.eligibility && c.eligibility.raw_notes) {
      const id = "n_" + c.id;
      html += `<span class="notes-toggle" data-t="${id}">顯示原始備註</span>
        <div class="notes-box hidden" id="${id}">${c.eligibility.raw_notes}</div>`;
    }
    el.innerHTML = html;
    box.appendChild(el);
  });
  box.querySelectorAll(".notes-toggle").forEach(t =>
    t.onclick = () => { const b = $("#" + t.dataset.t); b.classList.toggle("hidden");
      t.textContent = b.classList.contains("hidden") ? "顯示原始備註" : "隱藏原始備註"; });
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
