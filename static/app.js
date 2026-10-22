/* Job Screen v2.1 - single-page app.  Routes are #/... so the logo, Back and bookmarks all work.
   Sections:  1 helpers  2 data  3 header  4 landing+auth  5 pages  6 job drawer  7 profile  8 admin  9 router  */
"use strict";

// =============================================================== 1. helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const main = () => $("#main");
async function api(path, body) {
  const opt = body === undefined ? {credentials: "same-origin"} :
    {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-JS": "1"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  let d = {};
  try { d = await r.json(); } catch (e) { /* not json */ }
  if (r.status === 401 && !path.startsWith("/api/auth/")) { ME = null; S = null; go("#/"); throw new Error("Please log in again"); }
  if (!r.ok) throw new Error(d.error || ("HTTP " + r.status));
  return d;
}
function toast(msg, kind = "", action = null, ms = 4500) {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.innerHTML = `<span>${msg}</span>`;
  if (action) {
    const b = document.createElement("button"); b.className = "btn sm"; b.textContent = action.label;
    b.onclick = () => { t.remove(); action.run(); }; t.appendChild(b);
  }
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), ms);
}
const fail = e => toast(esc(e.message || e), "bad");
function go(h) { if (location.hash === h) render(); else location.hash = h; }
function dl(j) {
  if (j.days_left === null || j.days_left === undefined) return "";
  if (j.days_left < 0) return `<span class="tag">closed</span>`;
  if (j.days_left <= 7) return `<span class="tag dl">${j.days_left === 0 ? "today" : j.days_left + " d left"}</span>`;
  return `<span class="tag">${j.days_left} d left</span>`;
}
const scoreCls = (s, min) => s >= min + 30 ? "hi" : s >= min ? "mid" : "lo";
const ago = ts => {
  if (!ts) return "never";
  const m = Math.round((Date.now() - new Date(ts.replace(" ", "T"))) / 60000);
  if (m < 2) return "just now"; if (m < 60) return m + " min ago";
  if (m < 48 * 60) return Math.round(m / 60) + " h ago"; return Math.round(m / 1440) + " days ago";
};
function modal(html, onMount) {
  $("#modalCard").innerHTML = html; $("#modal").classList.remove("hidden");
  if (onMount) onMount($("#modalCard"));
  const f = $("#modalCard input"); if (f) f.focus();
}
const closeModal = () => $("#modal").classList.add("hidden");
const closeDrawer = () => $("#drawer").classList.add("hidden");
document.addEventListener("click", e => {
  if (e.target.matches("[data-close]")) { closeModal(); closeDrawer(); }
  if (!e.target.closest(".menu-wrap")) $$(".menu").forEach(m => m.classList.add("hidden"));
});
document.addEventListener("keydown", e => { if (e.key === "Escape") { closeModal(); closeDrawer(); } });

// =============================================================== 2. data
let ME = null, S = null, INFO = null, PROFILE_DRAFT = null;
const UI = {f: {q: "", country: "", src: "", status: "", sort: "score", closed: false}, all: {n: 60}, nw: {just: false, n: 60}};
const ACTIVE = ["interested", "applying", "applied", "interview", "offer"];
const ttl = j => j.title_en || j.title;                                   // English title when the ad is in another language
const SL = st => (S && S.status_labels && S.status_labels[st]) || st;     // friendly status names (Saved, Preparing, ...)
const isNew = j => j.status === "new" && j.score >= S.min_score && !j.gone && (j.days_left === null || j.days_left >= 0);
const newMatches = () => S.jobs.filter(isNew).sort((a, b) => b.score - a.score);
const jobById = id => S.jobs.find(j => j.id === id);
const lastNew = () => new Set(S.last_new || []);
async function loadState() { S = await api("/api/state"); return S; }
async function patchJob(id, fields, quiet) {
  await api("/api/job", {id, ...fields});
  const j = jobById(id); if (j) Object.assign(j, fields);
  if (!quiet) toast(fields.status ? `Moved to <b>${esc(fields.status)}</b>` : "Saved", "ok", null, 2200);
}

// =============================================================== 3. header
function renderHeader() {
  const r = route();
  $("#ver").textContent = "v" + ((S && S.version) || (INFO && INFO.version) || "2");
  $("#footver").textContent = (S && S.version) || (INFO && INFO.version) || "";
  const ann = (S && S.announcement) || (INFO && INFO.announcement);
  $("#banner").innerHTML = ann ? `<div class="banner">${esc(ann)}</div>` : "";
  if (!ME) {
    $("#nav").innerHTML = "";
    $("#topright").innerHTML = `<a class="btn ghost" href="#/login">Log in</a><a class="btn primary" href="#/register">Create account</a>`;
    return;
  }
  const nNew = S ? newMatches().length : 0, nTodo = S ? S.auto_todos.length + S.todos.filter(t => !t.done).length : 0;
  const items = [["", "Home"], ["new", "New matches", nNew], ["jobs", "All ads"], ["pipeline", "My applications"],
    ["todo", "To-do", nTodo], ["funding", "Funding"], ["sites", "Other job sites"], ["profile", "Search profile"]];
  if (S && S.is_owner) items.push(["folders", "Folders"]);
  let nav = items.map(([k, l, c]) => `<a href="#/${k}" class="${r.page === k ? "on" : ""}">${l}${c ? `<span class="count">${c}</span>` : ""}</a>`).join("");
  if (S && S.is_admin) nav += `<a href="#/admin" class="admin-link ${r.page === "admin" ? "on" : ""}">Admin</a>`;
  $("#nav").innerHTML = nav;
  const p = S ? S.progress : {};
  const upd = p.running
    ? `<span class="small muted">Updating ${esc(p.current || "")}</span><div class="progress"><i style="width:${p.total ? Math.round(100 * p.done / p.total) : 5}%"></i></div>`
    : `<span class="small muted nowrap" title="${esc(S && S.last_update)}">Updated ${ago(S && S.last_update)}</span>
       <button class="btn sm" id="btnUpdate" title="Fetch the newest ads now">&#x21bb; Update</button>`;
  $("#topright").innerHTML = `${upd}
    <div class="menu-wrap"><button class="avatar" id="btnMe" title="${esc(ME.user)}">${esc(ME.user[0].toUpperCase())}</button>
      <div class="menu hidden" id="meMenu">
        <div class="who"><b>${esc(ME.user)}</b> <span class="role ${esc(ME.role)}">${esc(ME.role)}</span></div>
        <a href="#/profile">Search profile</a>
        <a href="/api/export.md">Download my dashboard (.md)</a>
        ${S && S.is_admin ? `<a href="#/admin">Admin page</a>` : ""}
        <a id="mPw">Change password</a>
        <hr>
        ${ME.role !== "owner" ? `<a id="mDel" class="danger">Delete my account</a>` : ""}
        <a id="mOut">Log out</a>
      </div></div>`;
  $("#btnMe").onclick = e => { e.stopPropagation(); $("#meMenu").classList.toggle("hidden"); };
  $("#mOut").onclick = async () => { await api("/api/auth/logout", {}); ME = null; S = null; go("#/"); };
  $("#mPw").onclick = changePassword;
  if ($("#mDel")) $("#mDel").onclick = deleteAccount;
  if ($("#btnUpdate")) $("#btnUpdate").onclick = startUpdate;
}
async function startUpdate() {
  try {
    const r = await api(S.is_admin && route().page === "admin" ? "/api/admin/update" : "/api/update", {});
    if (r.started === false) { toast(esc(r.message || "An update is already running"), ""); if (!r.message) pollProgress(); return; }
    toast("Fetching new ads ... this takes about a minute", ""); pollProgress();
  } catch (e) { fail(e); }
}
let polling = false;
async function pollProgress() {
  if (polling) return; polling = true;
  try {
    for (;;) {
      const p = await api("/api/progress");
      if (S) { S.progress = p; renderHeader(); }
      if (!p.running) break;
      await new Promise(r => setTimeout(r, 1500));
    }
    await loadState(); render();
    toast(`Job list updated - <b>${S.last_new.length}</b> new ads, ${newMatches().length} match your profile`, "ok",
      {label: "See matches", run: () => go("#/new")}, 7000);
  } catch (e) { /* ignore */ } finally { polling = false; }
}
function changePassword() {
  modal(`<h3>Change password</h3>
    <label class="f">Current password</label><input type="password" id="pw0" autocomplete="current-password">
    <label class="f">New password (8+ characters)</label><input type="password" id="pw1" autocomplete="new-password">
    <label class="f">New password again</label><input type="password" id="pw2" autocomplete="new-password">
    <div class="err" id="pwErr"></div>
    <div class="row" style="justify-content:flex-end;margin-top:10px"><button class="btn" data-close>Cancel</button><button class="btn primary" id="pwGo">Change password</button></div>`,
  m => $("#pwGo", m).onclick = async () => {
    if ($("#pw1").value !== $("#pw2").value) { $("#pwErr").textContent = "The new passwords differ"; return; }
    try { await api("/api/auth/password", {password: $("#pw0").value, new_password: $("#pw1").value}); closeModal(); toast("Password changed", "ok"); }
    catch (e) { $("#pwErr").textContent = e.message; }
  });
}
function deleteAccount() {
  modal(`<h3>Delete my account</h3><p class="muted">This permanently deletes your account and everything you saved (jobs, notes, to-dos, funding list).</p>
    <label class="f">Your password</label><input type="password" id="dpw">
    <div class="err" id="dErr"></div>
    <div class="row" style="justify-content:flex-end;margin-top:10px"><button class="btn" data-close>Cancel</button><button class="btn danger" id="dGo">Delete permanently</button></div>`,
  m => $("#dGo", m).onclick = async () => {
    try { await api("/api/auth/delete", {password: $("#dpw").value}); closeModal(); ME = null; S = null; go("#/"); toast("Account deleted"); }
    catch (e) { $("#dErr").textContent = e.message; }
  });
}

// =============================================================== 4. landing + auth
function renderLanding(tab) {
  const i = INFO || {};
  main().innerHTML = `
  <section class="hero hero-photo">
    <div class="hero-text">
      <h1>Research jobs across Europe,<br><span class="grad">ranked for your field.</span></h1>
      <p class="lead">Job Screen collects research and academic ads from universities and public job portals in Sweden, the UK,
        France, Spain, Italy, Finland, Norway and Denmark several times a day, shows every title in English, scores each ad against
        the keywords you choose, and keeps your applications, deadlines and to-dos in one private place.</p>
      <div class="row"><a class="btn primary lg" href="#/register">Create a free account</a><a class="btn lg onphoto" href="#/login">Log in</a></div>
    </div>
    <div class="card authcard" id="auth">
      <div class="tabs2"><button data-t="login" class="${tab !== "register" ? "on" : ""}">Log in</button><button data-t="register" class="${tab === "register" ? "on" : ""}">Create account</button></div>
      <form id="authForm" autocomplete="on">
        <label class="f">Username</label><input type="text" name="username" autocomplete="username" autocapitalize="none" required>
        <label class="f">Password</label><input type="password" name="password" autocomplete="${tab === "register" ? "new-password" : "current-password"}" required>
        ${tab === "register" ? `<label class="f">Password again</label><input type="password" name="password2" autocomplete="new-password" required>
          ${i.invite_required ? `<label class="f">Invite code</label><input type="text" name="invite">` : ""}
          <div class="hp"><input type="text" name="website" tabindex="-1" autocomplete="off"></div>
          <p class="small muted">3-32 letters/digits. Your data is visible only to you.${i.registration_open === false ? " <b>Registration is closed at the moment.</b>" : ""}</p>` : ""}
        <div class="err" id="authErr"></div>
        <button class="btn primary lg" style="width:100%;justify-content:center;margin-top:8px">${tab === "register" ? "Create account" : "Log in"}</button>
      </form>
    </div>
  </section>
  <section class="statstrip">
    <div><b>${i.n_open ?? i.n_ads ?? "-"}</b><span>open ads right now</span></div>
    <div><b>${Object.keys(i.by_country || {}).filter(c => c !== "?").length || "-"}</b><span>countries</span></div>
    <div><b>${i.visits ? i.visits.total : "-"}</b><span>visits${i.visits ? ` (${i.visits.last30} in the last 30 days)` : ""}</span></div>
    <div><b>${i.n_users ?? "-"}</b><span>registered users</span></div>
    <div><b>${i.last_update ? ago(i.last_update) : "-"}</b><span>last update${i.last_update ? ` &middot; ${esc(i.last_update.slice(0, 16))}` : ""}</span></div>
  </section>
  ${i.by_country && Object.keys(i.by_country).length ? `<div class="countrychips">${Object.entries(i.by_country).filter(([c]) => c !== "?").map(([c, n]) => `<span class="chip">${esc(c)} <b>${n}</b></span>`).join("")}</div>` : ""}
  <section class="features">
    <div class="feature"><div class="ic">&#x1F3AF;</div><h3>Matched to you</h3><p>Pick your field, the kinds of position you want and a few keywords - every ad gets a match score, best first.</p></div>
    <div class="feature"><div class="ic">&#x1F5C2;</div><h3>Track applications</h3><p>Move jobs from Saved to Preparing, Applied and Interview. Notes and your own deadlines included.</p></div>
    <div class="feature"><div class="ic">&#x23F0;</div><h3>Never miss a deadline</h3><p>An automatic to-do list for closing ads, follow-ups and fellowship calls.</p></div>
  </section>`;
  $$(".tabs2 button").forEach(b => b.onclick = () => go("#/" + b.dataset.t));
  $("#authForm").onsubmit = async e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    if (tab === "register" && f.password !== f.password2) { $("#authErr").textContent = "The passwords differ"; return; }
    try {
      ME = await api(tab === "register" ? "/api/auth/register" : "/api/auth/login", f);
      await loadState();
      if (tab === "register") { go("#/profile"); toast("Welcome! Start by choosing your field and keywords.", "ok", null, 6000); }
      else go("#/");
    } catch (err) { $("#authErr").textContent = err.message; }
  };
}

// =============================================================== 5. pages
function jobCard(j) {
  const nw = lastNew().has(j.id) && j.status === "new";
  const tags = [nw ? `<span class="tag new">new today</span>` : "", dl(j),
    ...(j.matches || []).slice(0, 4).map(m => `<span class="tag match">${esc(m)}</span>`),
    ...(j.flags || []).slice(0, 3).map(f => `<span class="tag flag">${esc(f)}</span>`),
    j.gone ? `<span class="tag">removed by employer</span>` : ""].join("");
  const quick = j.status === "new"
    ? `<button class="btn sm" data-act="interested" title="Save to My applications">+ Save</button><button class="btn sm ghost" data-act="not relevant" title="Not for me - hide it">Hide</button>`
    : `<span class="pill ${esc(j.status.replace(" ", "-"))}">${esc(SL(j.status))}</span>`;
  return `<div class="job ${j.gone ? "gone" : ""}" data-id="${esc(j.id)}">
    <div class="score ${scoreCls(j.score || 0, S.min_score)}" title="match score">${j.score ?? ""}</div>
    <div><div class="t">${esc(ttl(j))}</div>${j.title_en ? `<div class="orig">${esc(j.title)}</div>` : ""}
      <div class="meta"><span>${esc(j.employer)}</span>${j.city || j.country ? `<span>${esc([j.city, j.country].filter(Boolean).join(", "))}</span>` : ""}${j.deadline ? `<span>Deadline ${esc(j.deadline)}</span>` : ""}<span>${esc(j.source)}</span></div>
      <div class="tags">${tags}</div></div>
    <div class="acts"><button class="star ${j.starred ? "on" : ""}" data-act="star" title="Star">&#9733;</button>${quick}</div></div>`;
}
function bindJobs(root, rerender) {
  $$(".job,.bcard,.mini-job", root).forEach(el => el.onclick = async e => {
    const id = el.dataset.id, act = e.target.closest("[data-act]");
    if (!act) return openJob(id);
    e.stopPropagation();
    try {
      if (act.dataset.act === "star") await patchJob(id, {starred: !jobById(id).starred}, true);
      else await patchJob(id, {status: act.dataset.act});
      rerender(); renderHeader();
    } catch (err) { fail(err); }
  });
}
function listPage(title, sub, jobs, st, extra, rerender) {
  const shown = jobs.slice(0, st.n);
  main().innerHTML = `<div class="page-head"><div><h2>${title}</h2><div class="sub">${sub}</div></div><div class="right">${extra.right || ""}</div></div>
    ${extra.toolbar || ""}
    <div class="jobs" id="jobs">${shown.map(jobCard).join("") || `<div class="card empty"><div class="big">&#x1F50E;</div>${extra.empty || "Nothing here."}</div>`}</div>
    ${jobs.length > st.n ? `<div class="more"><button class="btn" id="more">Show more (${jobs.length - st.n} left)</button></div>` : ""}`;
  bindJobs(main(), rerender);
  if ($("#more")) $("#more").onclick = () => { st.n += 60; rerender(); };
  if (extra.bind) extra.bind();
}

function pageHome() {
  const nm = newMatches(), pipe = S.jobs.filter(j => ACTIVE.includes(j.status));
  const soon = S.jobs.filter(j => ACTIVE.includes(j.status) && j.days_left !== null && j.days_left >= 0 && j.days_left <= 14);
  const fundSoon = S.funding.filter(f => f.days_left !== null && f.days_left >= 0 && f.days_left <= 90 && !["skip", "submitted"].includes(f.status));
  const todos = [...S.auto_todos, ...S.todos.filter(t => !t.done)];
  const hr = new Date().getHours(), hi = hr < 10 ? "Good morning" : hr < 18 ? "Hello" : "Good evening";
  const noProfile = S.profile.mode !== "advanced" && !(S.profile.simple.topics.length || S.profile.simple.roles.length);
  main().innerHTML = `
  <div class="card welcome"><div style="flex:1"><h2>${hi}, ${esc(S.user)}</h2>
    <p>${noProfile ? "Set up your search profile so Job Screen can rank ads for you." :
      `${nm.length} new ads match <b>${esc(S.profile.field || "your profile")}</b>. Job list updated ${ago(S.last_update)}.`}</p></div>
    ${noProfile ? `<a class="btn lg" href="#/profile">Set up my search profile &rarr;</a>` : `<a class="btn lg" href="#/new">See new matches &rarr;</a>`}</div>
  <div class="kpis">
    <div class="kpi accent" data-go="#/new"><div class="n">${nm.length}</div><div class="l">new matches</div></div>
    <div class="kpi" data-go="#/pipeline"><div class="n">${pipe.length}</div><div class="l">in My applications</div></div>
    <div class="kpi warn" data-go="#/todo"><div class="n">${todos.length}</div><div class="l">to-dos</div></div>
    <div class="kpi ok" data-go="#/funding"><div class="n">${fundSoon.length}</div><div class="l">funding calls in 90 days</div></div>
  </div>
  <div class="cols">
    <div class="card"><div class="row" style="justify-content:space-between"><h3>Best new matches</h3><a href="#/new">all ${nm.length} &rarr;</a></div>
      ${nm.slice(0, 6).map(j => `<div class="mini-job" data-id="${esc(j.id)}"><div class="score ${scoreCls(j.score, S.min_score)}">${j.score}</div>
        <div style="flex:1;min-width:0"><div class="t">${esc(ttl(j))}</div><div class="small muted">${esc(j.employer)}${j.deadline ? " - deadline " + esc(j.deadline) : ""}</div></div></div>`).join("")
        || `<div class="empty">No new matches right now.</div>`}</div>
    <div><div class="card"><div class="row" style="justify-content:space-between"><h3>To-do</h3><a href="#/todo">open &rarr;</a></div>
      ${todos.slice(0, 6).map(t => `<div class="todo"><div class="ic">${todoIcon(t.kind)}</div><div class="tx">${esc(t.text)}${t.due ? `<div class="small muted">due ${esc(t.due)}</div>` : ""}</div></div>`).join("")
        || `<div class="empty">All done.</div>`}</div>
      ${soon.length ? `<div class="card"><h3>Deadlines in the next 14 days</h3>${soon.sort((a, b) => a.days_left - b.days_left).map(j =>
        `<div class="mini-job" data-id="${esc(j.id)}"><div style="flex:1"><div class="t">${esc(ttl(j))}</div><div class="small muted">${esc(j.employer)}</div></div>${dl(j)}</div>`).join("")}</div>` : ""}
    </div></div>`;
  $$("[data-go]").forEach(k => k.onclick = () => go(k.dataset.go));
  bindJobs(main(), pageHome);
}

// One filter bar shared by New matches and All ads; choices are kept when switching between the two pages.
function applyFilter(jobs, f, page) {
  const q = f.q.toLowerCase().trim();
  return jobs.filter(j => (!f.country || (j.country || "") === f.country) && (!f.src || j.source === f.src)
    && (page === "new" || !f.status || j.status === f.status)
    && (page === "new" || f.closed || ((j.days_left === null || j.days_left >= 0) && !j.gone))
    && (!q || (ttl(j) + " " + j.title + " " + j.employer + " " + (j.city || "") + " " + (j.country || "") + " " + (j.department || "")).toLowerCase().includes(q)));
}
function sortJobs(jobs, k) {
  const by = {score: (a, b) => b.score - a.score, deadline: (a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999"),
    newest: (a, b) => (b.first_seen || "").localeCompare(a.first_seen || "")};
  return jobs.sort(by[k] || by.score);
}
function filterBar(page, rerender) {
  const f = UI.f;
  const countries = S.countries || [...new Set(S.jobs.map(j => j.country).filter(Boolean))].sort();
  const srcs = [...new Set(S.jobs.filter(j => !f.country || j.country === f.country).map(j => j.source))].sort();
  if (f.src && !srcs.includes(f.src)) f.src = "";
  const html = `<div class="toolbar"><input type="search" id="fq" placeholder="Search title, employer, city ..." value="${esc(f.q)}">
    <select id="fco"><option value="">All countries</option>${countries.map(c => `<option ${c === f.country ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
    <select id="fsrc"><option value="">All sources</option>${srcs.map(x => `<option ${x === f.src ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
    ${page === "all" ? `<select id="fst"><option value="">Any stage</option>${S.statuses.map(x => `<option value="${esc(x)}" ${x === f.status ? "selected" : ""}>${esc(SL(x))}</option>`).join("")}</select>` : ""}
    <div class="seg">${["score", "deadline", "newest"].map(k => `<button data-s="${k}" class="${f.sort === k ? "on" : ""}">${k === "score" ? "Best match" : k === "deadline" ? "Deadline" : "Newest"}</button>`).join("")}</div>
    ${page === "all" ? `<label class="switch small"><input type="checkbox" id="fcl" ${f.closed ? "checked" : ""}> show closed</label>` : ""}
    ${f.q || f.country || f.src || f.status ? `<button class="btn sm ghost" id="fclr">Clear filters</button>` : ""}</div>`;
  const bind = () => {
    const again = () => { UI.nw.n = 60; UI.all.n = 60; rerender(); };
    let tm; $("#fq").oninput = e => { clearTimeout(tm); tm = setTimeout(() => { f.q = e.target.value; again(); $("#fq").focus(); $("#fq").setSelectionRange(99, 99); }, 250); };
    $("#fco").onchange = e => { f.country = e.target.value; f.src = ""; again(); };
    $("#fsrc").onchange = e => { f.src = e.target.value; again(); };
    if ($("#fst")) $("#fst").onchange = e => { f.status = e.target.value; again(); };
    if ($("#fcl")) $("#fcl").onchange = e => { f.closed = e.target.checked; again(); };
    if ($("#fclr")) $("#fclr").onclick = () => { Object.assign(f, {q: "", country: "", src: "", status: ""}); again(); };
    $$("[data-s]").forEach(b => b.onclick = () => { f.sort = b.dataset.s; rerender(); });
  };
  return {html, bind};
}

function pageNew() {
  const st = UI.nw, ln = lastNew();
  const all = newMatches();
  let jobs = st.just ? all.filter(j => ln.has(j.id)) : all;
  jobs = sortJobs(applyFilter(jobs, UI.f, "new"), UI.f.sort);
  const fb = filterBar("new", pageNew);
  listPage("New matches", `Open ads scoring ${S.min_score}+ for your search profile that you have not sorted yet. <b>Save</b> the ones you like or <b>Hide</b> the rest.`,
    jobs, st, {
      toolbar: `<div class="toolbar"><div class="seg"><button data-j="0" class="${!st.just ? "on" : ""}">All new (${all.length})</button>
        <button data-j="1" class="${st.just ? "on" : ""}">From the latest update</button></div>
        <span class="muted small">${jobs.length} shown. Not finding the right jobs? <a href="#/profile">Adjust your search profile</a></span></div>` + fb.html,
      empty: st.just ? "No new matches from the latest update." : (UI.f.q || UI.f.country || UI.f.src ? "No new matches with these filters." : "You have sorted every matching ad. New ones arrive with each update."),
      bind: () => { fb.bind(); $$("[data-j]").forEach(b => b.onclick = () => { st.just = b.dataset.j === "1"; st.n = 60; pageNew(); }); }
    }, pageNew);
}

function pageAll() {
  const st = UI.all;
  const jobs = sortJobs(applyFilter(S.jobs, UI.f, "all"), UI.f.sort);
  const fb = filterBar("all", pageAll);
  listPage("All ads", `${jobs.length} of ${S.jobs.length} ads`, jobs, st, {
    right: `<button class="btn" id="addJob">+ Add a job I found elsewhere</button>`,
    toolbar: fb.html,
    bind: () => { fb.bind(); $("#addJob").onclick = addJobModal; }
  }, pageAll);
}

function pagePipeline() {
  const cols = ACTIVE.map(s => {
    const js = S.jobs.filter(j => j.status === s).sort((a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999"));
    return `<div class="col"><h3>${esc(SL(s))}<span class="c">${js.length}</span></h3>${js.map(j => `<div class="bcard" data-id="${esc(j.id)}">
      <div class="t">${j.starred ? "&#9733; " : ""}${esc(ttl(j))}</div><div class="m">${esc(j.employer)}</div>
      <div class="m">${j.deadline ? "Deadline " + esc(j.deadline) + " " : ""}${dl(j)}</div></div>`).join("") || `<div class="small muted" style="padding:6px">empty</div>`}</div>`;
  }).join("");
  const done = S.jobs.filter(j => ["rejected", "closed"].includes(j.status));
  main().innerHTML = `<div class="page-head"><div><h2>My applications</h2><div class="sub">Every job you saved, grouped by stage. Click a card to change its stage, add notes or set your own deadline.</div></div>
    <div class="right"><a class="btn" href="/api/export.md">Download as Markdown</a><button class="btn primary" id="addJob">+ Add job</button></div></div>
    <div class="board">${cols}</div>
    ${done.length ? `<div class="card" style="margin-top:16px"><h3>Finished (${done.length})</h3>${done.map(j => `<div class="mini-job" data-id="${esc(j.id)}">
      <span class="pill ${esc(j.status.replace(" ", "-"))}">${esc(SL(j.status))}</span><div style="flex:1">${esc(ttl(j))} <span class="muted small">- ${esc(j.employer)}</span></div></div>`).join("")}</div>` : ""}`;
  bindJobs(main(), pagePipeline);
  $("#addJob").onclick = addJobModal;
}
function addJobModal() {
  modal(`<h3>Add a job</h3><p class="small muted">For ads from LinkedIn, EURAXESS, e-mail etc. Only you can see it.</p>
    <label class="f">Position title *</label><input type="text" id="aj_t">
    <div class="grid2"><div><label class="f">Employer</label><input type="text" id="aj_e"></div><div><label class="f">City</label><input type="text" id="aj_c"></div></div>
    <label class="f">Link</label><input type="url" id="aj_u" placeholder="https://...">
    <div class="grid2"><div><label class="f">Deadline</label><input type="date" id="aj_d"></div>
      <div><label class="f">Status</label><select id="aj_s">${ACTIVE.map(s => `<option>${s}</option>`).join("")}</select></div></div>
    <label class="f">Notes</label><textarea id="aj_n"></textarea><div class="err" id="ajErr"></div>
    <div class="row" style="justify-content:flex-end;margin-top:8px"><button class="btn" data-close>Cancel</button><button class="btn primary" id="ajGo">Add</button></div>`,
  m => $("#ajGo", m).onclick = async () => {
    try {
      await api("/api/job/add", {title: $("#aj_t").value, employer: $("#aj_e").value, city: $("#aj_c").value, url: $("#aj_u").value,
        deadline: $("#aj_d").value, status: $("#aj_s").value, notes: $("#aj_n").value});
      closeModal(); await loadState(); render(); toast("Job added to My applications", "ok");
    } catch (e) { $("#ajErr").textContent = e.message; }
  });
}

const todoIcon = k => ({screen: "&#x1F50E;", apply: "&#x270D;", follow: "&#x1F4E8;", interview: "&#x1F3A4;", funding: "&#x1F4B6;",
  links: "&#x1F310;", setup: "&#x2699;"}[k] || "&#x2714;");
function pageTodo() {
  const man = S.todos.slice().sort((a, b) => (a.done - b.done) || (a.due || "9999").localeCompare(b.due || "9999"));
  main().innerHTML = `<div class="page-head"><div><h2>To-do</h2><div class="sub">Automatic reminders from your jobs, deadlines and funding list, plus your own tasks.</div></div></div>
  <div class="cols"><div class="card"><h3>Suggested for you</h3>
    ${S.auto_todos.map(t => `<div class="todo"><div class="ic">${todoIcon(t.kind)}</div><div class="tx"><a data-k="${esc(t.key)}" class="tgo" style="cursor:pointer;color:inherit">${esc(t.text)}</a>
      ${t.due ? `<div class="small muted">due ${esc(t.due)}</div>` : ""}</div><button class="btn sm ghost" data-dis="${esc(t.key)}" title="Hide this reminder">Dismiss</button></div>`).join("")
      || `<div class="empty">Nothing to do right now.</div>`}</div>
  <div class="card"><h3>My tasks</h3>
    <div class="row" style="flex-wrap:nowrap"><input type="text" id="tdT" placeholder="e.g. Ask Prof. X for a reference letter"><input type="date" id="tdD" style="width:160px"><button class="btn primary" id="tdAdd">Add</button></div>
    <div style="margin-top:10px">${man.map(t => `<div class="todo ${t.done ? "done" : ""}"><input type="checkbox" data-tg="${esc(t.id)}" ${t.done ? "checked" : ""}>
      <div class="tx">${esc(t.text)}${t.due ? `<div class="small muted">due ${esc(t.due)}</div>` : ""}</div><button class="btn sm ghost" data-del="${esc(t.id)}">&#x2715;</button></div>`).join("")
      || `<div class="empty small">No own tasks yet.</div>`}</div>
    ${man.some(t => t.done) ? `<button class="btn sm" id="tdClear">Remove done tasks</button>` : ""}</div></div>`;
  const reload = async () => { await loadState(); pageTodo(); renderHeader(); };
  $("#tdAdd").onclick = async () => { if (!$("#tdT").value.trim()) return; try { await api("/api/todo", {action: "add", text: $("#tdT").value, due: $("#tdD").value}); reload(); } catch (e) { fail(e); } };
  $("#tdT").onkeydown = e => { if (e.key === "Enter") $("#tdAdd").click(); };
  $$("[data-tg]").forEach(b => b.onchange = async () => { await api("/api/todo", {action: "toggle", id: b.dataset.tg}); reload(); });
  $$("[data-del]").forEach(b => b.onclick = async () => { await api("/api/todo", {action: "delete", id: b.dataset.del}); reload(); });
  $$("[data-dis]").forEach(b => b.onclick = async () => { await api("/api/todo", {action: "dismiss", key: b.dataset.dis}); reload(); });
  if ($("#tdClear")) $("#tdClear").onclick = async () => { await api("/api/todo", {action: "clear_done"}); reload(); };
  $$(".tgo").forEach(a => a.onclick = () => {
    const t = S.auto_todos.find(x => x.key === a.dataset.k);
    if (t.job_id) openJob(t.job_id); else go({screen: "#/new", funding: "#/funding", links: "#/sites", setup: "#/profile"}[t.kind] || "#/");
  });
}

function pageFunding() {
  const fl = S.funding.slice().sort((a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999"));
  main().innerHTML = `<div class="page-head"><div><h2>Funding & fellowships</h2><div class="sub">Your own list of calls. Deadlines within ${S.profile.funding_warn_days || 90} days appear in To-do.</div></div>
    <div class="right">${S.presets.funding_catalog && S.presets.funding_catalog.length ? `<button class="btn" id="fImp">+ Add common postdoc fellowships</button>` : ""}<button class="btn primary" id="fAdd">+ Add a call</button></div></div>
  <div class="card" style="padding:6px 10px">${fl.length ? `<table class="t"><tr><th>Call</th><th>Deadline</th><th>Status</th><th>Notes</th><th></th></tr>
    ${fl.map(f => `<tr><td><b>${f.url ? `<a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.name)}</a>` : esc(f.name)}</b><div class="small muted">${esc(f.funder || "")}</div></td>
      <td class="nowrap">${esc(f.deadline || "-")}${f.estimated ? ` <span class="tag" title="estimated from last year">est.</span>` : ""}<div>${f.days_left !== null && f.days_left >= 0 ? `<span class="tag ${f.days_left <= 30 ? "dl" : ""}">${f.days_left} d</span>` : ""}</div></td>
      <td><select data-fs="${esc(f.id)}">${S.fund_statuses.map(s => `<option ${s === f.status ? "selected" : ""}>${s}</option>`).join("")}</select></td>
      <td><input type="text" data-fn="${esc(f.id)}" value="${esc(f.notes || "")}" placeholder="notes"></td>
      <td><button class="btn sm ghost" data-fd="${esc(f.id)}">&#x2715;</button></td></tr>`).join("")}</table>`
    : `<div class="empty"><div class="big">&#x1F4B6;</div>No calls yet - add the common postdoc fellowships with one click, or add your own.</div>`}</div>`;
  const reload = async () => { await loadState(); pageFunding(); };
  $$("[data-fs]").forEach(s => s.onchange = async () => { await api("/api/funding", {id: s.dataset.fs, status: s.value}); toast("Saved", "ok", null, 1500); });
  $$("[data-fn]").forEach(s => s.onchange = async () => { await api("/api/funding", {id: s.dataset.fn, notes: s.value}); toast("Saved", "ok", null, 1500); });
  $$("[data-fd]").forEach(s => s.onclick = async () => { if (confirm("Remove this call from your list?")) { await api("/api/funding/delete", {id: s.dataset.fd}); reload(); } });
  if ($("#fImp")) $("#fImp").onclick = async () => { await api("/api/funding/import", {}); reload(); toast("Added - deadlines marked est. are estimates, please check them", "ok"); };
  $("#fAdd").onclick = () => modal(`<h3>Add a funding call</h3><label class="f">Name *</label><input type="text" id="fa_n">
    <label class="f">Funder</label><input type="text" id="fa_f"><div class="grid2"><div><label class="f">Deadline</label><input type="date" id="fa_d"></div>
    <div><label class="f">&nbsp;</label><label class="switch"><input type="checkbox" id="fa_e"> estimated</label></div></div>
    <label class="f">Link</label><input type="url" id="fa_u"><div class="err" id="faErr"></div>
    <div class="row" style="justify-content:flex-end;margin-top:8px"><button class="btn" data-close>Cancel</button><button class="btn primary" id="faGo">Add</button></div>`,
  m => $("#faGo", m).onclick = async () => {
    try { await api("/api/funding/add", {name: $("#fa_n").value, funder: $("#fa_f").value, deadline: $("#fa_d").value, estimated: $("#fa_e").checked, url: $("#fa_u").value}); closeModal(); reload(); }
    catch (e) { $("#faErr").textContent = e.message; }
  });
}

function pageSites() {
  main().innerHTML = `<div class="page-head"><div><h2>Other job sites</h2><div class="sub">Sites Job Screen cannot read automatically. Links open with your keywords filled in;
    opening one marks it as checked (reminder after ${S.profile.manual_check_days || 7} days).</div></div><div class="right"><button class="btn" id="allChk">Mark all as checked</button></div></div>
  <div class="features" style="margin-top:0">${S.links.map(l => {
    const c = S.links_checked[l.name];
    return `<div class="feature"><h3>${esc(l.name)}</h3><p>${esc(l.note || "")}</p>
      <div class="row" style="margin-top:10px;justify-content:space-between"><span class="small muted">${c ? "checked " + esc(c) : "not checked yet"}</span>
      <a class="btn sm primary" href="${esc(l.url)}" target="_blank" rel="noopener" data-ln="${esc(l.name)}">Open &#x2197;</a></div></div>`;
  }).join("") || `<div class="card empty">No links configured yet.</div>`}</div>`;
  $$("[data-ln]").forEach(a => a.addEventListener("click", async () => { await api("/api/link_checked", {name: a.dataset.ln}); S.links_checked[a.dataset.ln] = S.today; setTimeout(pageSites, 300); }));
  $("#allChk").onclick = async () => { await api("/api/link_checked", {all: true}); await loadState(); pageSites(); renderHeader(); };
}

function pageFolders() {
  main().innerHTML = `<div class="page-head"><div><h2>Application folders</h2><div class="sub">Folders in ${esc(S.job_root)}. Create one from a job's details ("Create application folder").</div></div></div>
  <div class="card" style="padding:6px 10px">${S.folders.length ? `<table class="t"><tr><th>#</th><th>Folder</th><th>Job</th><th>Modified</th><th></th></tr>
    ${S.folders.map(f => { const j = f.job_id && jobById(f.job_id);
      return `<tr><td>${f.num}</td><td><b>${esc(f.name)}</b></td><td>${j ? `<a data-oj="${esc(j.id)}" style="cursor:pointer">${esc(ttl(j))}</a>` : `<span class="muted small">-</span>`}</td>
      <td class="small muted">${esc(f.modified)}</td><td>${S.local ? `<button class="btn sm" data-of="${esc(f.path)}">Open in Finder</button>` : ""}</td></tr>`; }).join("")}</table>`
    : `<div class="empty">No folders yet.</div>`}</div>`;
  $$("[data-of]").forEach(b => b.onclick = () => api("/api/open", {path: b.dataset.of}).catch(fail));
  $$("[data-oj]").forEach(b => b.onclick = () => openJob(b.dataset.oj));
}

// =============================================================== 6. job drawer
async function openJob(id) {
  const j = jobById(id); if (!j) return;
  const p = $("#drawerPanel");
  p.innerHTML = `<button class="btn ghost close" data-close>&#x2715;</button>
    <div class="row" style="gap:8px"><div class="score ${scoreCls(j.score || 0, S.min_score)}">${j.score ?? ""}</div><span class="pill ${esc(j.status.replace(" ", "-"))}">${esc(SL(j.status))}</span>${dl(j)}</div>
    <h2 style="margin-top:12px">${esc(ttl(j))}</h2>${j.title_en ? `<div class="orig">Original title: ${esc(j.title)}</div>` : ""}
    <div class="muted">${esc(j.employer)}${j.department ? " - " + esc(j.department) : ""}${j.city ? " - " + esc(j.city) : ""}${j.country ? " - " + esc(j.country) : ""}</div>
    <div class="small muted" style="margin-top:4px">Source: ${esc(j.source)}${j.published ? " - published " + esc(j.published) : ""}${j.deadline ? " - <b>deadline " + esc(j.deadline) + "</b>" : ""}</div>
    <div class="tags" style="margin-top:8px;display:flex;gap:5px;flex-wrap:wrap">${(j.matches || []).map(m => `<span class="tag match">${esc(m)}</span>`).join("")}${(j.flags || []).map(f => `<span class="tag flag">${esc(f)}</span>`).join("")}</div>
    <div class="row" style="margin:14px 0">${j.url ? `<a class="btn primary" href="${esc(j.url)}" target="_blank" rel="noopener">Open ad / apply &#x2197;</a>` : ""}
      ${j.ad_url && j.ad_url !== j.url ? `<a class="btn" href="${esc(j.ad_url)}" target="_blank" rel="noopener">${String(j.id).startsWith("wpjm:") ? esc(j.source) + " ad" : "Platsbanken ad"}</a>` : ""}
      ${S.is_owner ? (j.folder ? (S.local ? `<button class="btn" id="dOpen">Open folder</button>` : `<span class="small muted">folder: ${esc(j.folder.split("/").pop())}</span>`) : `<button class="btn" id="dFolder">Create application folder</button>`) : ""}
      <button class="star ${j.starred ? "on" : ""}" id="dStar" title="Star">&#9733;</button></div>
    <label class="f">Stage</label><div class="statusbar">${S.statuses.map(s => `<button data-st="${s}" class="${s === j.status ? "on" : ""}">${esc(SL(s))}</button>`).join("")}</div>
    <div class="grid2"><div><label class="f">My deadline (optional)</label><input type="date" id="dDl" value="${esc(j.deadline || "")}"></div><div></div></div>
    <label class="f">My notes (private)</label><textarea id="dNotes" rows="4" placeholder="contact person, salary, what to emphasise ...">${esc(j.notes || "")}</textarea>
    <h3 style="margin-top:18px">Ad text</h3><div class="desc" id="dDesc">Loading ...</div>
    ${j.manual ? `<div style="margin-top:16px"><button class="btn danger" id="dDelJob">Delete this job</button></div>` : ""}`;
  $("#drawer").classList.remove("hidden");
  const refresh = () => { render(); renderHeader(); };
  $$("[data-st]", p).forEach(b => b.onclick = async () => {
    try { await patchJob(id, {status: b.dataset.st}); $$("[data-st]", p).forEach(x => x.classList.toggle("on", x === b)); refresh(); } catch (e) { fail(e); }
  });
  $("#dNotes").onchange = async e => { try { await patchJob(id, {notes: e.target.value}); } catch (err) { fail(err); } };
  $("#dDl").onchange = async e => { try { await api("/api/job", {id, deadline: e.target.value}); await loadState(); refresh(); toast("Deadline saved", "ok", null, 2000); } catch (err) { fail(err); } };
  $("#dStar").onclick = async e => { await patchJob(id, {starred: !j.starred}, true); e.target.classList.toggle("on"); refresh(); };
  if ($("#dFolder")) $("#dFolder").onclick = async () => {
    try { const r = await api("/api/job/folder", {id}); toast("Folder created: " + esc(r.path.split("/").pop()), "ok"); await loadState(); openJob(id); refresh(); } catch (e) { fail(e); }
  };
  if ($("#dOpen")) $("#dOpen").onclick = () => api("/api/open", {path: j.folder}).catch(fail);
  if ($("#dDelJob")) $("#dDelJob").onclick = async () => { if (confirm("Delete this job?")) { await api("/api/job/delete", {id}); closeDrawer(); await loadState(); refresh(); } };
  try {
    const d = await api("/api/ad?id=" + encodeURIComponent(id) + "&en=1");
    const orig = d.description || "", en = d.description_en || "";
    const box = $("#dDesc"); if (!box) return;
    if (!orig) { box.textContent = "No ad text stored - open the ad link above."; return; }
    if (!en) { box.textContent = orig; if (d.translate_error && !d.is_english) box.insertAdjacentHTML("beforebegin", `<div class="small muted">Automatic English translation is not available right now - showing the original text.</div>`); return; }
    let showEn = true;
    const draw = () => { box.textContent = showEn ? en : orig; $("#dLang").textContent = showEn ? "Show original language" : "Show English translation"; };
    box.insertAdjacentHTML("beforebegin", `<div class="small muted" style="margin-bottom:6px">Machine translation into English. The original ad is the binding version.
      <button class="btn sm ghost" id="dLang"></button></div>`);
    $("#dLang").onclick = () => { showEn = !showEn; draw(); };
    draw();
  } catch (e) { const box = $("#dDesc"); if (box) box.textContent = "Ad text not available - open the ad link above."; }
}

// =============================================================== 7. search profile
function pageProfile() {
  const P = S.profile, pre = S.presets || {};
  if (!PROFILE_DRAFT) PROFILE_DRAFT = JSON.parse(JSON.stringify({field: P.field || "", preset: P.preset || "", simple: P.simple,
    follow_up_days: P.follow_up_days, funding_warn_days: P.funding_warn_days, manual_check_days: P.manual_check_days, mode: P.mode}));
  const D = PROFILE_DRAFT, sm = D.simple;
  const field = (pre.fields || []).find(f => f.id === D.preset);
  const topicOpts = [...new Set([...(field ? field.topics : []), ...sm.topics])];
  const roleOpts = [...new Set([...(pre.roles || []), ...sm.roles])];
  sm.exclude = [];                                     // v2.2: the "hide words" step was removed
  const chips = (opts, sel, key, cls = "") => opts.map(o => `<span class="chip ${cls} ${sel.map(s => s.toLowerCase()).includes(o.toLowerCase()) ? "on" : ""}" data-k="${key}" data-v="${esc(o)}">${esc(o)}</span>`).join("");
  const adv = S.is_owner && D.mode === "advanced";
  main().innerHTML = `<div class="page-head"><div><h2>Search profile</h2><div class="sub">Tell Job Screen what you are looking for. Click an option to select it, click again to remove it. All options are in English - ads in other languages are matched too.</div></div></div>
  ${adv ? `<div class="card" style="border-color:#c7cbf5;background:var(--brand-soft)"><b>Advanced scoring is on</b> (your weighted rules imported from v1). The keyword choices below are kept but not used.
    <button class="btn sm" id="toSimple" style="margin-left:8px">Switch to keyword scoring</button></div>` : ""}
  <div class="card"><div class="step"><div class="num">1</div><div class="body"><h3>Your field</h3>
    <div class="fields">${(pre.fields || []).map(f => `<div class="fieldcard ${f.id === D.preset ? "on" : ""}" data-f="${esc(f.id)}">${esc(f.name)}</div>`).join("")}</div>
    <label class="f">Describe it in your own words (optional)</label><input type="text" id="pField" value="${esc(D.field)}" placeholder="e.g. single-cell genomics of the immune system"></div></div></div>
  <div class="card"><div class="step"><div class="num">2</div><div class="body"><h3>Topics you work on</h3>
    <p class="small muted" style="margin-top:-4px">Ads that mention these get a higher score (title matches count double). Field and topics are used loosely: an ad does not need to mention them to be shown. ${field ? "Suggestions for " + esc(field.name) + "." : "Pick a field above to see suggestions."}</p>
    <div class="chips">${chips(topicOpts, sm.topics, "topics")}</div>
    <div class="addchip"><input type="text" id="addTopic" placeholder="add your own topic, e.g. metabolomics"><button class="btn" data-add="topics">Add</button></div></div></div></div>
  <div class="card"><div class="step"><div class="num">3</div><div class="body"><h3>Positions you want</h3>
    <p class="small muted" style="margin-top:-4px">This one is strict: an ad that is clearly a different kind of position (for example a postdoc when Postdoc is not selected) is left out of New matches. It is still listed under All ads. Ads in Swedish, French, Spanish, Italian, Finnish, Norwegian and Danish are recognised.</p>
    <div class="chips">${chips(roleOpts, sm.roles, "roles")}</div>
    <div class="addchip"><input type="text" id="addRole" placeholder="add another job title"><button class="btn" data-add="roles">Add</button></div></div></div></div>
  <div class="card"><details class="adv"><summary>Reminder settings</summary>
    <div class="grid2" style="grid-template-columns:repeat(3,1fr)">
      <div><label class="f">Follow up after applying (days)</label><input type="number" id="pFu" min="1" value="${D.follow_up_days || 21}"></div>
      <div><label class="f">Warn before funding deadlines (days)</label><input type="number" id="pFw" min="1" value="${D.funding_warn_days || 90}"></div>
      <div><label class="f">Remind me to check other job sites (days)</label><input type="number" id="pMc" min="1" value="${D.manual_check_days || 7}"></div></div></details>
  ${S.is_owner ? `<details class="adv" style="margin-top:6px"><summary>Owner settings (advanced scoring, extra searches, own links)</summary>
    <label class="switch" style="margin-top:8px"><input type="checkbox" id="pAdv" ${D.mode === "advanced" ? "checked" : ""}> use advanced scoring rules</label>
    <label class="f">Advanced scoring rules (JSON: role/topic/negative regex &rarr; weight, min_score, gate ...)</label>
    <textarea id="pJson" rows="10" style="font-family:ui-monospace,Menlo,monospace;font-size:.82rem">${esc(JSON.stringify(P.scoring || {}, null, 1))}</textarea>
    <label class="f">Extra Platsbanken (Sweden) searches (one per line)</label><textarea id="pQ" rows="4">${esc((P.queries || []).join("\n"))}</textarea>
    <label class="f">My own saved-search links (Name | https://..., one per line - only you see them)</label>
    <textarea id="pL" rows="4">${esc((P.manual_sources || []).map(l => l.name + " | " + l.url).join("\n"))}</textarea></details>` : ""}</div>
  <div class="sticky-save"><span class="small muted">${sm.topics.length} topics - ${sm.roles.length} positions</span>
    <button class="btn" id="pReset">Undo changes</button><button class="btn primary lg" id="pSave">Save profile</button></div>`;
  $$(".fieldcard").forEach(c => c.onclick = () => {
    D.preset = c.dataset.f; const f = pre.fields.find(x => x.id === D.preset);
    if (!D.field || pre.fields.some(x => x.name === D.field)) D.field = f.name;
    if (!sm.topics.length) sm.topics = f.topics.slice(0, 6);
    if (!sm.roles.length) sm.roles = f.roles.slice();
    pageProfile();
  });
  $("#pField").oninput = e => { D.field = e.target.value; };
  $$(".chip").forEach(c => c.onclick = () => {
    const arr = sm[c.dataset.k], v = c.dataset.v, i = arr.findIndex(x => x.toLowerCase() === v.toLowerCase());
    if (i >= 0) arr.splice(i, 1); else arr.push(v);
    c.classList.toggle("on", i < 0);
    $(".sticky-save .small").textContent = `${sm.topics.length} topics - ${sm.roles.length} positions`;
  });
  const ids = {topics: "#addTopic", roles: "#addRole"};
  Object.entries(ids).forEach(([k, sel]) => {
    const add = () => { const v = $(sel).value.trim(); if (!v) return; v.split(",").map(x => x.trim()).filter(Boolean).forEach(x => { if (!sm[k].some(y => y.toLowerCase() === x.toLowerCase())) sm[k].push(x); }); pageProfile(); $(sel).focus(); };
    $(`[data-add="${k}"]`).onclick = add; $(sel).onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); add(); } };
  });
  ["pFu", "pFw", "pMc"].forEach((id, i) => $("#" + id).onchange = e => { D[["follow_up_days", "funding_warn_days", "manual_check_days"][i]] = +e.target.value; });
  if ($("#toSimple")) $("#toSimple").onclick = () => { D.mode = "simple"; pageProfile(); };
  if ($("#pAdv")) $("#pAdv").onchange = e => { D.mode = e.target.checked ? "advanced" : "simple"; pageProfile(); };
  $("#pReset").onclick = () => { PROFILE_DRAFT = null; pageProfile(); };
  $("#pSave").onclick = saveProfile;
}
async function saveProfile() {
  const D = PROFILE_DRAFT, body = {field: D.field, preset: D.preset, simple: D.simple, follow_up_days: D.follow_up_days,
    funding_warn_days: D.funding_warn_days, manual_check_days: D.manual_check_days};
  if (S.is_owner) {
    body.mode = D.mode;
    if ($("#pJson")) {
      body.scoring_json = $("#pJson").value.trim();
      body.queries = $("#pQ").value.split("\n").map(x => x.trim()).filter(Boolean);
      body.manual_sources = $("#pL").value.split("\n").map(x => x.split("|")).filter(x => x.length >= 2).map(x => ({name: x[0].trim(), url: x.slice(1).join("|").trim()}));
    }
  }
  const btn = $("#pSave"); btn.disabled = true; btn.textContent = "Saving ...";
  try {
    const r = await api("/api/profile", body);
    PROFILE_DRAFT = null; await loadState(); renderHeader(); pageProfile();
    modal(`<div style="text-align:center"><div style="font-size:2.6rem">&#x2705;</div><h3>Profile saved</h3>
      <p class="muted">All ads were re-scored for your profile.<br><b style="font-size:1.25rem;color:var(--brand)">${r.new_matches}</b> new ads match it now.</p>
      <div class="row" style="justify-content:center;margin-top:14px"><button class="btn" data-close>Stay here</button>
      <button class="btn primary lg" id="goNew">See ${r.new_matches} new matches &rarr;</button></div></div>`,
    m => $("#goNew", m).onclick = () => { closeModal(); go("#/new"); });
  } catch (e) { fail(e); btn.disabled = false; btn.textContent = "Save profile"; }
}

// =============================================================== 8. admin
let ADM = null, ADM_Q = "";
async function pageAdmin() {
  if (!S.is_admin) return go("#/");
  try { ADM = await api("/api/admin/overview"); } catch (e) { return fail(e); }
  const A = ADM, me = ME.user, q = ADM_Q.toLowerCase();
  const users = A.users.filter(u => !q || u.username.includes(q));
  const roleBtn = u => u.role === "owner" ? "" : u.role === "admin"
    ? `<button class="btn sm" data-role="user" data-u="${esc(u.username)}">Remove admin</button>`
    : `<button class="btn sm" data-role="admin" data-u="${esc(u.username)}">Set as admin</button>`;
  const ok = Object.values(A.sources).filter(s => s.status && s.status.ok).length;
  main().innerHTML = `<div class="page-head"><div><h2>Admin</h2><div class="sub">Manage the website. Admins never see other accounts' jobs, notes or to-dos.</div></div>
    <div class="right"><button class="btn" id="aUpd">&#x21bb; Update job list now</button></div></div>
  <div class="kpis"><div class="kpi"><div class="n">${A.users.length}</div><div class="l">accounts (max ${A.max_users})</div></div>
    <div class="kpi"><div class="n">${A.n_ads}</div><div class="l">ads in the pool</div></div>
    <div class="kpi ${ok < A.sources.filter(s => s.enabled).length ? "warn" : "ok"}"><div class="n">${ok}/${A.sources.filter(s => s.enabled).length}</div><div class="l">sources working</div></div>
    <div class="kpi"><div class="n" style="font-size:1.2rem;padding:6px 0">${ago(A.last_update)}</div><div class="l">last update (every ${S.auto_update_hours} h)</div></div></div>
  <div class="cols"><div class="card"><h3>Website address</h3>
    ${A.public_url ? `<div class="copy"><input type="text" readonly value="${esc(A.public_url)}" id="pubUrl"><button class="btn" id="cpy">Copy</button></div>
      <p class="small muted">Share this link. It changes each time the app is restarted (free Cloudflare tunnel); the Terminal window must stay open.</p>`
      : `<p class="muted">No public address yet - start the app with the Desktop launcher (JobScreen_v2.command).</p>`}</div>
    <div class="card"><h3>Registration</h3>
      <label class="switch"><input type="checkbox" id="regOpen" ${A.registration_open ? "checked" : ""}> new people can create accounts</label>
      ${A.invite_required ? `<p class="small muted">An invite code is required (config.json).</p>` : ""}
      <label class="f">Announcement banner (shown to everyone, empty = none)</label>
      <div class="row" style="flex-wrap:nowrap"><input type="text" id="ann" value="${esc(A.announcement)}" placeholder="e.g. Maintenance tonight 22:00"><button class="btn" id="annSave">Save</button></div></div></div>
  <div class="card"><div class="row" style="justify-content:space-between"><h3>Accounts</h3><input type="search" id="uq" placeholder="find account" value="${esc(ADM_Q)}" style="max-width:240px"></div>
    <table class="t"><tr><th>Account</th><th>Role</th><th>Created</th><th>Last login</th><th>Status</th><th style="text-align:right">Actions</th></tr>
    ${users.slice(0, 300).map(u => `<tr><td><b>${esc(u.username)}</b>${u.me ? ' <span class="small muted">(you)</span>' : ""}</td>
      <td><span class="role ${esc(u.role)}">${esc(u.role)}</span></td><td class="small muted">${esc((u.created || "").slice(0, 10))}</td>
      <td class="small muted">${esc((u.last_login || "").slice(0, 16)) || "-"}</td><td>${u.disabled ? `<span class="tag dl">blocked</span>` : `<span class="tag">active</span>`}</td>
      <td style="text-align:right"><div class="row" style="justify-content:flex-end;gap:6px">${roleBtn(u)}
        ${u.role === "user" ? `<button class="btn sm" data-blk="${u.disabled ? 0 : 1}" data-u="${esc(u.username)}">${u.disabled ? "Unblock" : "Block"}</button>
          <button class="btn sm" data-rst="${esc(u.username)}">Reset password</button>
          <button class="btn sm danger" data-del="${esc(u.username)}">Delete</button>` : ""}</div></td></tr>`).join("")}</table>
    ${users.length > 300 ? `<p class="small muted">Showing 300 of ${users.length} - use the search box.</p>` : ""}</div>
  <div class="card"><h3>Other job sites (shown to every account)</h3>
    <p class="small muted">Write <code>{q}</code> in a link and it is replaced by each user's own keywords.</p>
    <div id="links">${A.links.map(l => linkRow(l)).join("")}</div>
    <div class="row" style="margin-top:10px"><button class="btn" id="lAdd">+ Add link</button><button class="btn primary" id="lSave">Save links</button></div></div>
  <div class="card"><h3>Job sources</h3><p class="small muted">Switch a source off if it keeps failing. University lists: sources.json.</p>
    <table class="t"><tr><th></th><th>Source</th><th>Type</th><th>Last result</th><th>On</th></tr>
    ${A.sources.map(s => `<tr><td><span class="dot ${!s.enabled ? "off" : s.status.ok ? "ok" : s.status.at ? "bad" : ""}"></span></td>
      <td><a href="${esc(s.site)}" target="_blank" rel="noopener">${esc(s.name)}</a></td><td class="small muted">${({varbi: "Varbi", employer: "Platsbanken employer", platsbanken: "Platsbanken search", euraxess: "EURAXESS", eures: "EURES", jobsac: "jobs.ac.uk search", wpjm: "Job board"}[s.kind] || esc(s.kind))}</td>
      <td class="small">${s.status.ok ? s.status.n + " ads" + (s.status.skipped ? " (refreshed in rotation)" : "") : s.status.error ? `<span style="color:var(--bad)">${esc(s.status.error)}</span>` : "<span class='muted'>not run yet</span>"}</td>
      <td><label class="switch"><input type="checkbox" data-src="${esc(s.name)}" ${s.enabled ? "checked" : ""}></label></td></tr>`).join("")}</table></div>`;
  const after = r => { ADM = r; pageAdmin(); };
  $("#aUpd").onclick = startUpdate;
  if ($("#cpy")) $("#cpy").onclick = () => { navigator.clipboard.writeText(A.public_url); toast("Address copied", "ok", null, 1800); };
  $("#regOpen").onchange = async e => { await api("/api/admin/settings", {registration_open: e.target.checked}); toast(e.target.checked ? "Registration open" : "Registration closed", "ok"); };
  $("#annSave").onclick = async () => { await api("/api/admin/settings", {announcement: $("#ann").value}); S.announcement = $("#ann").value; renderHeader(); toast("Saved", "ok"); };
  let tm; $("#uq").oninput = e => { clearTimeout(tm); tm = setTimeout(() => { ADM_Q = e.target.value; pageAdmin().then(() => { $("#uq").focus(); $("#uq").setSelectionRange(99, 99); }); }, 250); };
  $$("[data-role]").forEach(b => b.onclick = async () => {
    const msg = b.dataset.role === "admin" ? `Make ${b.dataset.u} an admin? Admins can manage accounts, registration, links and sources (but cannot see other people's data).` : `Remove admin rights from ${b.dataset.u}?`;
    if (!confirm(msg)) return;
    try { after(await api("/api/admin/role", {username: b.dataset.u, role: b.dataset.role})); toast("Role changed", "ok"); } catch (e) { fail(e); }
  });
  $$("[data-blk]").forEach(b => b.onclick = async () => { try { after(await api("/api/admin/block", {username: b.dataset.u, blocked: b.dataset.blk === "1"})); } catch (e) { fail(e); } });
  $$("[data-rst]").forEach(b => b.onclick = async () => {
    if (!confirm(`Give ${b.dataset.rst} a new temporary password?`)) return;
    try { const r = await api("/api/admin/reset_password", {username: b.dataset.rst});
      modal(`<h3>Temporary password for ${esc(b.dataset.rst)}</h3><div class="copy"><input type="text" readonly value="${esc(r.temp_password)}"></div>
        <p class="small muted">Send it to the person; they should change it under the account menu. It is shown only once.</p><div class="row" style="justify-content:flex-end"><button class="btn primary" data-close>Done</button></div>`);
    } catch (e) { fail(e); }
  });
  $$("[data-del]").forEach(b => b.onclick = async () => {
    if (!confirm(`Delete the account ${b.dataset.del} and all its data? This cannot be undone.`)) return;
    try { after(await api("/api/admin/user/delete", {username: b.dataset.del})); toast("Account deleted", "ok"); } catch (e) { fail(e); }
  });
  $("#lAdd").onclick = () => { $("#links").insertAdjacentHTML("beforeend", linkRow({name: "", url: "", note: ""})); bindLinkRows(); };
  $("#lSave").onclick = async () => {
    const links = $$(".lrow").map(r => ({name: $(".ln", r).value, url: $(".lu", r).value, note: $(".lt", r).value})).filter(l => l.name && l.url);
    try { after(await api("/api/admin/links", {links})); await loadState(); toast("Links saved", "ok"); } catch (e) { fail(e); }
  };
  bindLinkRows();
  $$("[data-src]").forEach(c => c.onchange = async () => { try { after(await api("/api/admin/source", {name: c.dataset.src, enabled: c.checked})); } catch (e) { fail(e); } });
}
const linkRow = l => `<div class="row lrow" style="flex-wrap:nowrap;margin-top:6px"><input type="text" class="ln" placeholder="Name" value="${esc(l.name)}" style="max-width:200px">
  <input type="text" class="lu" placeholder="https://... ({q} = keywords)" value="${esc(l.url)}"><input type="text" class="lt" placeholder="note" value="${esc(l.note || "")}" style="max-width:200px">
  <button class="btn sm ghost lx">&#x2715;</button></div>`;
const bindLinkRows = () => $$(".lx").forEach(b => b.onclick = () => b.closest(".lrow").remove());

// =============================================================== 9. router
function route() {
  const h = location.hash.replace(/^#\/?/, "").split("?")[0];
  return {page: h.split("/")[0] || ""};
}
const PAGES = {"": pageHome, new: pageNew, jobs: pageAll, pipeline: pagePipeline, todo: pageTodo, funding: pageFunding,
  sites: pageSites, profile: pageProfile, folders: pageFolders, admin: pageAdmin};
function render() {
  const r = route();
  closeDrawer();
  if (r.page !== "profile") PROFILE_DRAFT = null;
  if (!ME || !S) { renderHeader(); return renderLanding(r.page === "register" ? "register" : "login"); }
  if (r.page === "login" || r.page === "register") { location.hash = "#/"; return; }
  renderHeader();
  (PAGES[r.page] || pageHome)();
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", render);
(async function boot() {
  try { INFO = await api("/api/public_info"); } catch (e) { INFO = {}; }
  try { const m = await api("/api/me"); if (m.user) { ME = m; await loadState(); } } catch (e) { ME = null; }
  render();
  if (S && S.progress && S.progress.running) pollProgress();
  setInterval(async () => {   // refresh silently every 10 min so the page never gets stale
    if (ME && !polling && $("#modal").classList.contains("hidden") && $("#drawer").classList.contains("hidden") && route().page !== "profile") {
      try { await loadState(); render(); } catch (e) { /* ignore */ }
    }
  }, 600000);
})();
