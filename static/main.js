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
let GRAD_STUDENT = null;

// ---------------------------------------------------------- 狀態儲存
function saveState() {
  const state = {
    dept: $("#u-dept").value,
    grade: $("#u-grade").value,
    completed: $("#u-completed").value,
    prefText: $("#pref-text").value,
    fixed: FIXED,
    graduation: $("#grad-program").value && GRAD_STUDENT
      ? { program_id: $("#grad-program").value, student: GRAD_STUDENT } : null,
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
  if (s.graduation && (META.graduation_programs || []).some(p => p.id === s.graduation.program_id)) {
    $("#grad-program").value = s.graduation.program_id;
    GRAD_STUDENT = s.graduation.student;
  }
  $("#grad-controls").classList.toggle("hidden", !$("#grad-program").value);
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
  // 系所下拉：學士班 / 研究所分組（後端已排除行政單位與學院本身）
  const groups = m.dept_groups || {};
  [["undergrad", "學士班"], ["graduate", "研究所／學位學程"]].forEach(([key, label]) => {
    if (!(groups[key] || []).length) return;
    const og = document.createElement("optgroup"); og.label = label;
    groups[key].forEach(d => {
      const o = document.createElement("option"); o.value = d; o.textContent = d; og.appendChild(o);
    });
    $("#u-dept").appendChild(og);
  });
  // 類別 / 通識領域篩選
  fillSelect("#f-category", m.categories);
  fillSelect("#f-gedomain", m.ge_domains);
  (m.graduation_programs || []).forEach(p => {
    const option = document.createElement("option");
    option.value = p.id; option.textContent = p.label;
    $("#grad-program").appendChild(option);
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
  if (GRAD_STUDENT) await checkGraduation();
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
  $("#grad-program").onchange = () => {
    GRAD_STUDENT = null;
    $("#grad-controls").classList.toggle("hidden", !$("#grad-program").value);
    $("#grad-report").replaceChildren();
    $("#grad-status").textContent = $("#grad-program").value ? "請載入案例，或匯入自己的主系JSON。" : "";
    $("#grad-file").value = "";
    saveState();
  };
  $("#grad-demo").onclick = loadGraduationDemo;
  $("#grad-check").onclick = checkGraduation;
  $("#grad-file").onchange = importGraduationRecord;
  $("#fixed-search-btn").onclick = searchFixed;
  $("#fixed-search").addEventListener("keydown", e => { if (e.key === "Enter") searchFixed(); });
  $("#parse-btn").onclick = parsePrefs;
  $("#gen-btn").onclick = generate;
  ["#u-dept","#u-grade","#u-completed","#pref-text"].forEach(s =>
    $(s).addEventListener("change", saveState));
}

// 畢業規則對應的系所（取自 /api/meta 的 graduation_programs）
function programDept(programId) {
  const p = (META.graduation_programs || []).find(x => x.id === programId);
  return p ? p.department : "";
}

function graduationPayload() {
  const program = $("#grad-program").value;
  if (!program) return null;
  if (!GRAD_STUDENT) throw new Error("請先載入77學分案例，或匯入自己的主系JSON。");
  return { program_id: program, student: GRAD_STUDENT };
}

async function loadGraduationDemo() {
  const program = $("#grad-program").value;
  if (!program) return;
  $("#grad-demo").disabled = true;
  try {
    const j = await api(`/api/graduation/demo/${encodeURIComponent(program)}`);
    if ($("#grad-program").value !== program) return;
    GRAD_STUDENT = j.student;
    $("#u-dept").value = programDept(program);
    $("#u-grade").value = "3";
    saveState();
    await checkGraduation();
  } catch (e) { $("#grad-status").textContent = e.message; }
  finally { $("#grad-demo").disabled = false; }
}

async function importGraduationRecord() {
  const file = $("#grad-file").files[0];
  if (!file) return;
  const program = $("#grad-program").value;
  try {
    if (file.size > 1024 * 1024) throw new Error("JSON請小於1MB。");
    const student = JSON.parse(await file.text());
    const j = await api("/api/graduation/check", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({program_id: program, student}),
    });
    if ($("#grad-program").value !== program) return;
    GRAD_STUDENT = student;
    $("#u-dept").value = programDept(program);
    saveState(); renderGraduationReport(j.report);
  } catch (e) { $("#grad-status").textContent = `匯入失敗：${e.message}`; }
}

async function checkGraduation() {
  const status = $("#grad-status");
  try {
    const payload = graduationPayload();
    if (!payload) return;
    status.textContent = "檢查中…";
    const j = await api("/api/graduation/check", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if ($("#grad-program").value === payload.program_id) renderGraduationReport(j.report);
  } catch (e) { status.textContent = `無法檢查：${e.message}`; }
}

function renderGraduationReport(report) {
  const total = report.total_credits;
  $("#grad-status").textContent = `已完成 ${total.completed}／${total.required} 學分，尚差 ${total.remaining}；在修 ${report.in_progress_credits} 學分另列。`;
  const box = $("#grad-report"); box.replaceChildren();
  const summary = document.createElement("p");
  summary.className = "graduation-summary";
  summary.textContent = `${total.completed} / ${total.required} 學分`;
  box.appendChild(summary);
  const progress = document.createElement("progress");
  progress.max = total.required; progress.value = total.completed;
  progress.setAttribute("aria-label", "畢業總學分進度");
  box.appendChild(progress);
  const table = document.createElement("table"); table.className = "graduation-table";
  const head = table.createTHead().insertRow();
  ["條件", "完成／要求", "尚差"].forEach(label => { const th = document.createElement("th"); th.textContent = label; head.appendChild(th); });
  const body = table.createTBody();
  report.requirement_rows.forEach(item => {
    const row = body.insertRow();
    [item.label, `${item.completed}／${item.required}`, `${item.remaining} ${item.unit}`]
      .forEach(value => { row.insertCell().textContent = value; });
  });
  box.appendChild(table);
  const notes = ["各列不能直接相加；核心與系所選修的重疊採認仍待確認。", ...report.data_notes];
  const nc = report.noncredit_requirements;
  notes.push(`學習護照：${nc.learning_passport.hours ?? "未知"} 小時，門檻待查；英文能力：${nc.english_proficiency.status === "passed" ? "已完成" : "待確認"}。`);
  if (report.unavailable_needed_course_codes.length) notes.push(`本資料學期未找到的缺額選項：${report.unavailable_needed_course_codes.join("、")}。請查後續開課。`);
  const ul = document.createElement("ul"); ul.className = "graduation-notes";
  notes.forEach(note => { const li = document.createElement("li"); li.textContent = note; ul.appendChild(li); });
  box.appendChild(ul);
  const details = document.createElement("details");
  const label = document.createElement("summary"); label.textContent = "採認及規則待確認事項";
  details.appendChild(label);
  const list = document.createElement("ul");
  report.to_verify.forEach(note => { const li = document.createElement("li"); li.textContent = note; list.appendChild(li); });
  details.appendChild(list); box.appendChild(details);
}

// ---------------------------------------------------------- 步驟1：固定課
async function searchFixed() {
  const q = $("#fixed-search").value.trim();
  const cat = $("#f-category").value;
  const ge = $("#f-gedomain").value;
  if (!q && !cat && !ge) {
    $("#fixed-search-status").textContent = "請輸入關鍵字，或選擇類別／通識領域。";
    $("#fixed-search-status").classList.remove("status-error");
    return;
  }
  const btn = $("#fixed-search-btn");
  const status = $("#fixed-search-status");
  const ul = $("#fixed-search-results");
  btn.disabled = true; status.textContent = "搜尋中…"; status.classList.remove("status-error");
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
    status.classList.add("status-error");
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
  if (!text) { status.textContent = "請先輸入白話需求。"; status.classList.remove("status-error"); return; }
  saveState();
  btn.disabled = true; status.textContent = "解析中…"; status.classList.remove("status-error");
  try {
    const j = await api("/api/parse_preferences", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    fillPrefForm(j.preferences);
    status.textContent = "已解析，可在下方確認或修改。";
  } catch (e) {
    status.textContent = "解析失敗：" + e.message;
    status.classList.add("status-error");
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
  let graduation;
  try { graduation = graduationPayload(); }
  catch (e) { $("#gen-status").textContent = e.message; return; }
  saveState();
  const btn = $("#gen-btn");
  const status = $("#gen-status");
  btn.disabled = true;
  status.textContent = "計算中…";
  status.classList.remove("status-error");
  // 顯示計算中 skeleton，並清掉上一次的結果，避免看起來像卡住
  $("#schedule-empty").classList.add("hidden");
  $("#scheme-tabs").innerHTML = "";
  $("#schedule-courses").innerHTML = "";
  $("#schedule-grid").innerHTML =
    '<div class="loading"><span class="spinner" aria-hidden="true"></span>排課計算中…</div>';
  const body = {
    fixed_ids: Object.keys(FIXED),
    user: {
      dept: $("#u-dept").value || null,
      grade: $("#u-grade").value || null,
      completed_courses: splitList($("#u-completed").value),
    },
    preferences: $("#pref-parsed").classList.contains("hidden") ? {} : readSpec(),
    max_schedules: 3,
    graduation,
  };
  try {
    const j = await api("/api/schedules", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    SCHEDULES = j.schedules;
    if (j.graduation_report) renderGraduationReport(j.graduation_report);
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
      ? `；未設學分上限，預設最多加 ${s.added_cap} 門候選課（可在偏好設定學分上限調整）`
      : "";
    status.textContent = `產生 ${SCHEDULES.length} 組（搜尋 ${s.nodes} 節點、${s.elapsed}s${s.hit_limit ? "，已達上限回傳最佳結果" : ""}）${capNote}`;
    ACTIVE = 0; renderTabs(); renderScheme();
    // 手機單欄時，產生後自動捲到結果區（桌機結果已在右側 sticky 欄可見）
    if (window.matchMedia("(max-width: 899px)").matches) {
      $("#step3").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (e) {
    status.textContent = "發生錯誤：" + e.message;
    status.classList.add("status-error");
    $("#schedule-grid").innerHTML = "";
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
    if (c.graduation && c.graduation.requirements.length) {
      const note = document.createElement("div");
      note.textContent = `畢業缺額：${c.graduation.requirements.join("、")}；通過並經採認後計入。`;
      note.className = "graduation-match";
      html += note.outerHTML;
    }
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
