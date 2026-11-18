"use strict";
/* Single-screen timing UI: race list, live capture, race review, results (spec section 7). */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const LANE_COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#008080", "#9a6324", "#800000"];

const S = {
  sessionId: null, sessions: [], races: [], raceId: null, race: null, state: {}, settings: {},
  selEntry: null, cursor: null, zx: 1, stripImg: null, stripKey: null, pending: {}, calibration: {},
};

/* ---------- helpers ---------- */
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-User": "operator" } };
  if (opts.body !== undefined) {
    init.body = typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body);
    init.headers["Content-Type"] = opts.type || "application/json";
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* ignore */ }
    toast(msg);
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}
let toastTimer;
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 3200 + msg.length * 40);
}
function fmtTime(ms) {   // rounded up to the next hundredth (same rule as the server)
  if (ms === null || ms === undefined) return "";
  const h = Math.ceil(Math.round(ms * 1e6) / 1e6 / 10 - 1e-9);
  const s = Math.floor(h / 100), hh = h % 100, m = Math.floor(s / 60), ss = s % 60;
  const p2 = (n) => String(n).padStart(2, "0");
  return m ? `${m}:${p2(ss)}.${p2(hh)}` : `${ss}.${p2(hh)}`;
}

/* ---------- sessions ---------- */
async function loadSessions(selectId) {
  S.sessions = await api("/api/sessions");
  if (!S.sessions.length) {
    const d = new Date().toISOString().slice(0, 10);
    await api("/api/sessions", { method: "POST", body: { name: `Training ${d}` } });
    S.sessions = await api("/api/sessions");
  }
  const saved = Number(localStorage.getItem("sessionId"));
  S.sessionId = selectId || (S.sessions.find((s) => s.id === saved) || S.sessions[0]).id;
  $("session").innerHTML = S.sessions.map((s) => `<option value="${s.id}">${esc(s.name)} (${esc(s.date)})</option>`).join("");
  $("session").value = S.sessionId;
  localStorage.setItem("sessionId", S.sessionId);
  $("exp-csv").href = `/api/sessions/${S.sessionId}/export.csv`;
  $("exp-pdf").href = `/api/sessions/${S.sessionId}/export.pdf`;
  await refreshRaces();
}

/* ---------- race list ---------- */
async function refreshRaces() {
  if (!S.sessionId) return;
  const races = await api(`/api/sessions/${S.sessionId}/races`);
  const changed = JSON.stringify(races.map((r) => [r.id, r.status, r.recorded_at])) !==
                  JSON.stringify(S.races.map((r) => [r.id, r.status, r.recorded_at]));
  S.races = races;
  if (!S.raceId || !races.find((r) => r.id === S.raceId)) {
    const pick = currentRace() || races.find((r) => r.status === "scheduled") || races[races.length - 1];
    S.raceId = pick ? pick.id : null;
  }
  if (changed || !S.race || S.race.id !== S.raceId) await loadRace();
  renderRaceList();
}

function currentRace() {
  const st = S.state;
  const active = S.races.find((r) => r.status === "armed" || ((st.state === "recording" || st.state === "analysing") && r.id === st.race_id));
  if (active) return active;
  const rev = S.races.filter((r) => r.status === "review").sort((a, b) => (b.recorded_at || "").localeCompare(a.recorded_at || ""));
  return rev[0] || null;
}

function renderRaceList() {
  const cur = currentRace();
  const next = S.races.find((r) => r.status === "scheduled" && (!cur || r.id !== cur.id));
  const prev = S.races.filter((r) => ["review", "locked"].includes(r.status) && (!cur || r.id !== cur.id));
  const later = S.races.filter((r) => r.status === "scheduled" && (!next || r.id !== next.id));
  let html = "";
  const full = (r, label) => {
    const rows = r.entries.map((e) =>
      `<div class="row"><span><i class="ln" style="--lc:${LANE_COLORS[(e.lane - 1) % 8]}" title="Lane ${e.lane}">${e.lane}</i>${e.place ? `<span class="pl">${e.place}.</span> ` : ""}${esc(e.name || "Lane " + e.lane)}</span><b>${e.status !== "OK" ? e.status : esc(e.time) || "–"}</b></div>`).join("");
    return `<div class="race ${r.id === S.raceId ? "sel" : ""}" data-id="${r.id}">
      <div class="rt"><span>${esc(r.event)} · ${esc(r.heat)}</span><small>${label || r.status}</small></div>${rows}</div>`;
  };
  if (prev.length) html += `<div class="group-title">Previous</div>` + prev.map((r) => full(r, r.status === "locked" ? "locked" : "to check")).join("");
  if (cur) {
    const st = S.state; const live = st.state === "recording" && st.race_id === cur.id ? "recording"
      : st.state === "analysing" && st.race_id === cur.id ? "analysing" : cur.status === "armed" ? "armed" : "to check";
    html += `<div class="group-title">Current</div>` + full(cur, live);
  }
  if (next) html += `<div class="group-title">Next</div>` + full(next, "next");
  if (later.length) html += `<div class="group-title">Later</div>` + later.map((r) =>
    `<div class="race next ${r.id === S.raceId ? "sel" : ""}" data-id="${r.id}"><div class="rt"><span>${esc(r.event)} · ${esc(r.heat)}</span><small>${r.entries.length} athletes</small></div></div>`).join("");
  if (!S.races.length) html = `<div class="muted">No races yet. Import a start list or add a quick race.</div>`;
  $("racelist-body").innerHTML = html;
  document.querySelectorAll("#racelist-body .race").forEach((el) => el.onclick = () => selectRace(Number(el.dataset.id)));
}

async function selectRace(id) { S.raceId = id; S.selEntry = null; S.cursor = null; await loadRace(); renderRaceList(); }

/* ---------- race detail ---------- */
async function loadRace() {
  if (!S.raceId) { S.race = null; renderAll(); return; }
  S.race = await api(`/api/races/${S.raceId}`);
  const key = `${S.race.id}:${S.race.recorded_at}`;
  if (S.race.analysis && S.stripKey !== key) {
    S.stripKey = key; S.stripImg = null;
    const img = new Image();
    img.onload = () => { S.stripImg = img; S.autoView = true; drawStrip(); scrollToFinishes(); };
    img.src = `/api/races/${S.race.id}/strip.jpg?v=${encodeURIComponent(S.race.recorded_at)}`;
  } else if (!S.race.analysis) { S.stripImg = null; S.stripKey = null; }
  renderAll();
}
function renderAll() { renderResults(); drawStrip(); renderFrame(); renderLiveButtons();
  if (S.autoView && S.stripImg) scrollToFinishes(); }   // default view follows the data until the user takes over

function activeFrame() {
  const r = S.race; if (!r || r.n_flash === null) return null;
  if (S.cursor !== null) return S.cursor;
  const e = r.entries.find((x) => x.entry_id === S.selEntry);
  if (e && e.final_frame !== null) return e.final_frame;
  return r.n_flash;
}
function frameTimeMs(f) { const r = S.race; return (f - r.n_flash) / r.fps * 1000 + r.delta_ms; }

/* ---------- results ---------- */
function confClass(c) { return c < 0.5 ? "conf-low" : c < 0.8 ? "conf-mid" : ""; }
function laneOptions(cur) {
  const n = Math.max(8, cur, ...(S.settings.lanes || []).map((l) => l.lane));
  return Array.from({ length: n }, (_, i) => i + 1).map((l) => `<option ${l === cur ? "selected" : ""}>${l}</option>`).join("");
}
function renderResults() {
  const r = S.race, box = $("results-body");
  if (!r) { box.innerHTML = `<div class="muted">No race selected</div>`; $("warnings").innerHTML = ""; return; }
  const entries = [...r.entries].sort((a, b) => (a.place ?? 99) - (b.place ?? 99) || a.lane - b.lane);
  const locked = r.status === "locked";
  box.innerHTML = `<div class="muted" style="margin-bottom:4px">${esc(r.event)} · heat ${esc(r.heat)} · ${esc(r.status)}</div>
  <table class="res"><thead><tr><th>Pl</th><th>Ln</th><th>Bib</th><th>Name</th><th style="text-align:right">Time</th><th>Cf</th><th>St</th></tr></thead><tbody>` +
    entries.map((e) => `<tr data-eid="${e.entry_id}" class="${e.entry_id === S.selEntry ? "sel" : ""}">
      <td>${e.place ?? ""}</td><td><select data-f="lane" class="lane-sel" style="color:${LANE_COLORS[(e.lane - 1) % 8]}" title="Move this athlete to another lane" ${locked ? "disabled" : ""}>${laneOptions(e.lane)}</select></td>
      <td><input data-f="bib" value="${esc(e.bib)}" size="3" ${locked ? "disabled" : ""}></td>
      <td><input data-f="name" value="${esc(e.name)}" ${locked ? "disabled" : ""}></td>
      <td class="t" title="${e.time_ms !== null ? e.time_ms.toFixed(1) + " ms" : ""}">${esc(e.time) || "–"}${e.edited ? '<span class="edited" title="edited by hand">✎</span>' : ""}</td>
      <td class="${confClass(e.confidence)}" title="${esc(e.notes)}">${r.analysis ? Math.round(e.confidence * 100) : ""}</td>
      <td><select data-f="status" ${locked ? "disabled" : ""}>${["OK", "DNS", "DNF", "DQ"].map((s) => `<option ${s === e.status ? "selected" : ""}>${s}</option>`).join("")}</select></td>
    </tr>`).join("") + `</tbody></table>`;
  box.querySelectorAll("tr[data-eid]").forEach((tr) => {
    const eid = Number(tr.dataset.eid);
    tr.onclick = (ev) => { if (ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT") return; S.autoView = false; S.selEntry = eid; S.cursor = null; renderResults(); drawStrip(); renderFrame(); };
    tr.querySelectorAll("input").forEach((inp) => inp.onchange = () => api(`/api/entries/${eid}/athlete`, { method: "PUT", body: { [inp.dataset.f]: inp.value } }).then(refreshRaces));
    tr.querySelector('select[data-f="lane"]').onchange = (ev) => api(`/api/races/${r.id}/entries/${eid}/lane`, { method: "PUT", body: { lane: Number(ev.target.value) } })
      .then((d) => { S.race = d; refreshRaces(); renderAll(); }, () => renderResults());   // refused (lane taken): put the old value back
    tr.querySelector('select[data-f="status"]').onchange = (ev) => api(`/api/races/${r.id}/entries/${eid}/status`, { method: "PUT", body: { status: ev.target.value } }).then((d) => { S.race = d; refreshRaces(); renderAll(); });
  });
  $("btn-lock").textContent = locked ? "Unlock race" : "Lock race";
  $("btn-undo").disabled = $("btn-redo").disabled = !r.analysis || locked;
  $("btn-lock").disabled = !r.analysis; $("btn-reanalyze").disabled = !r.analysis || locked;
  const w = [...(r.warnings || [])];
  if (r.analysis) {
    w.unshift(`t0 frame ${r.n_flash} · ${r.fps.toFixed(2)} fps measured · ${r.link} link · δ ${r.delta_ms.toFixed(2)} ms`);
  }
  $("warnings").innerHTML = w.map((x, i) => `<div style="color:${i === 0 && r.analysis ? "var(--muted)" : "var(--warn)"}">${i === 0 && r.analysis ? "" : "⚠ "}${esc(x)}</div>`).join("");
}

/* ---------- marker editing ---------- */
function setMarkerLocal(entryId, frame) {
  const e = S.race.entries.find((x) => x.entry_id === entryId); if (!e) return;
  e.final_frame = frame; e.time_ms = frame === null ? null : frameTimeMs(frame); e.time = frame === null ? "" : fmtTime(e.time_ms); e.edited = 1;
  drawStrip(); renderFrame();
}
function sendMarker(entryId, frame) {
  clearTimeout(S.pending[entryId]);
  S.pending[entryId] = setTimeout(async () => {
    try {
      const d = await api(`/api/races/${S.race.id}/entries/${entryId}/marker`, { method: "PUT", body: { frame } });
      S.race = d; renderAll(); refreshRaces();
    } catch (e) { await loadRace(); }
  }, 160);
}
function moveMarker(entryId, frame) { setMarkerLocal(entryId, frame); sendMarker(entryId, frame); }

async function stepHistory(kind) {
  const r = S.race; if (!r || !r.analysis || r.status === "locked") return;
  try { S.race = await api(`/api/races/${r.id}/${kind}`, { method: "POST" }); renderAll(); refreshRaces(); }
  catch (e) { toast(String(e.message || e)); }
}
document.addEventListener("keydown", (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "z" && !document.querySelector("dialog[open]")
      && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
    ev.preventDefault(); stepHistory(ev.shiftKey ? "redo" : "undo"); return;
  }
  if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName) || document.querySelector("dialog[open]")) return;
  const r = S.race; if (!r || r.n_flash === null) return;
  if (/^[1-9]$/.test(ev.key) && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
    const e = r.entries.find((x) => x.lane === Number(ev.key));
    if (e && r.status !== "locked") { ev.preventDefault(); selectEntry(e.entry_id); }
    return;
  }
  const step = ev.shiftKey ? 10 : 1;
  if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
    ev.preventDefault();
    const d = (ev.key === "ArrowLeft" ? -step : step) * (S.rtl ? -1 : 1);   // arrows follow the screen direction
    const e = r.entries.find((x) => x.entry_id === S.selEntry);
    if (e && e.final_frame !== null && S.cursor === null) moveMarker(e.entry_id, e.final_frame + d);
    else { S.cursor = (activeFrame() ?? r.n_flash) + d; drawStrip(); renderFrame(); }
  } else if ((ev.key === "Delete" || ev.key === "Backspace") && S.selEntry) {
    ev.preventDefault(); moveMarker(S.selEntry, null);
  } else if (ev.key === "Escape") { S.cursor = null; drawStrip(); renderFrame(); }
});

/* ---------- strip ---------- */
const RULER_H = 22;
// Time direction of the strip: right to left (t0 on the right) by default; the button flips it.
S.rtl = (() => { try { return localStorage.getItem("timing-rtl") !== "0"; } catch (e) { return true; } })();
const posOf = (r, f) => ((S.rtl ? r.analysis.strip.w - (f - r.first_idx) - 0.5 : f - r.first_idx + 0.5)) * S.zx;
const frameAt = (r, x) => { const i = Math.floor(x / S.zx); return r.first_idx + (S.rtl ? r.analysis.strip.w - 1 - i : i); };
function centerOn(f) { const sc = $("strip-scroll"), r = S.race; if (r && r.analysis) sc.scrollLeft = posOf(r, f) - sc.clientWidth / 2; }
const stripH = () => Math.max(80, $("strip-scroll").clientHeight - RULER_H);
function selectEntry(eid) {
  S.autoView = false;
  S.selEntry = eid; S.cursor = null; renderResults(); drawStrip(); renderFrame();
  const e = S.race && S.race.entries.find((x) => x.entry_id === eid);
  if (e && e.final_frame !== null) { const sc = $("strip-scroll"), x = posOf(S.race, e.final_frame);
    if (x < sc.scrollLeft + 40 || x > sc.scrollLeft + sc.clientWidth - 40) centerOn(e.final_frame); }
}
function renderLaneChips() {
  const r = S.race, box = $("lane-chips");
  if (!r || !r.analysis) { box.innerHTML = ""; return; }
  const locked = r.status === "locked";
  box.innerHTML = [...r.entries].sort((a, b) => a.lane - b.lane).map((e, i) => {
    const col = LANE_COLORS[(e.lane - 1) % 8];
    return `<button class="chip ${e.entry_id === S.selEntry ? "on" : ""} ${e.final_frame === null ? "nomark" : ""}" style="--lc:${col}"
      data-eid="${e.entry_id}" ${locked ? "disabled" : ""} title="Key ${e.lane}">L${e.lane} <small>${e.final_frame === null ? "no marker – click strip" : fmtTime(e.time_ms)}</small></button>`;
  }).join("");
  box.querySelectorAll(".chip").forEach((b) => b.onclick = () => selectEntry(Number(b.dataset.eid)));
  $("strip").classList.toggle("picking", !!S.selEntry);
}
function drawStrip() {
  const cv = $("strip"), ctx = cv.getContext("2d"), r = S.race;
  const ok = r && r.analysis && S.stripImg;
  $("strip-empty").style.display = ok ? "none" : "flex";
  $("review-title").textContent = r ? `${r.event} · heat ${r.heat}` : "";
  renderLaneChips();
  if (!ok) { cv.width = 1; cv.height = 1; return; }
  const a = r.analysis, N = a.strip.w, zx = S.zx;
  const SH = stripH(); cv.width = Math.ceil(N * zx); cv.height = SH + RULER_H;
  ctx.fillStyle = "#222"; ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.imageSmoothingEnabled = zx < 1;
  ctx.save(); if (S.rtl) { ctx.translate(N * zx, 0); ctx.scale(-1, 1); }
  ctx.drawImage(S.stripImg, 0, RULER_H, N * zx, SH); ctx.restore();
  const xOf = (f) => posOf(r, f), dir = S.rtl ? -1 : 1;
  // ruler: time since t0
  const steps = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10];
  const stepS = steps.find((s) => s * r.fps * zx >= 70) || 10;
  ctx.fillStyle = "#333"; ctx.fillRect(0, 0, cv.width, RULER_H);
  ctx.font = "11px system-ui"; ctx.textBaseline = "middle"; ctx.strokeStyle = "#888"; ctx.fillStyle = "#ddd";
  const tMin = (r.first_idx - r.n_flash) / r.fps, tMax = (r.first_idx + N - r.n_flash) / r.fps;
  for (let t = Math.ceil(tMin / stepS) * stepS; t <= tMax; t += stepS) {
    const x = xOf(r.n_flash + t * r.fps);
    ctx.beginPath(); ctx.moveTo(x, RULER_H - 7); ctx.lineTo(x, RULER_H); ctx.stroke();
    ctx.textAlign = S.rtl ? "right" : "left";
    ctx.fillText(t.toFixed(stepS < 0.1 ? 2 : stepS < 1 ? 1 : 0) + " s", x + 3 * dir, 9);
  }
  // t0 marker
  ctx.strokeStyle = "#ffd400"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(xOf(r.n_flash), 0); ctx.lineTo(xOf(r.n_flash), cv.height); ctx.stroke();
  ctx.fillStyle = "#ffd400"; ctx.textAlign = S.rtl ? "right" : "left"; ctx.fillText("t0", xOf(r.n_flash) + 3 * dir, RULER_H + 10);
  // finish markers
  for (const e of r.entries) {
    if (e.final_frame === null) continue;
    const x = xOf(e.final_frame), col = LANE_COLORS[(e.lane - 1) % 8], sel = e.entry_id === S.selEntry;
    ctx.strokeStyle = col; ctx.lineWidth = sel ? 2.5 : 1.3; ctx.beginPath(); ctx.moveTo(x, RULER_H); ctx.lineTo(x, cv.height); ctx.stroke();
    const fx = S.rtl ? x - 25 : x - 1;
    ctx.fillStyle = col; ctx.fillRect(fx, RULER_H, 26, 14); ctx.fillStyle = "#fff"; ctx.font = "bold 11px system-ui"; ctx.textAlign = "left";
    ctx.fillText("L" + e.lane + (e.edited ? "✎" : ""), fx + 3, RULER_H + 8);
  }
  if (S.cursor !== null) {
    ctx.setLineDash([4, 3]); ctx.strokeStyle = "#fff"; ctx.beginPath(); ctx.moveTo(xOf(S.cursor), RULER_H); ctx.lineTo(xOf(S.cursor), cv.height); ctx.stroke(); ctx.setLineDash([]);
  }
}
["wheel", "pointerdown"].forEach((t) => $("strip-scroll").addEventListener(t, () => { S.autoView = false; }, { passive: true }));
$("strip").addEventListener("click", (ev) => {
  const r = S.race; if (!r || !r.analysis) return;
  const rect = $("strip").getBoundingClientRect();
  const frame = frameAt(r, ev.clientX - rect.left);
  if (ev.clientY - rect.top < RULER_H + 16) {          // clicking a lane flag selects that lane
    const hit = r.entries.find((e) => e.final_frame !== null && Math.abs(posOf(r, e.final_frame) + (S.rtl ? -12 : 12) - (ev.clientX - rect.left)) <= 14);
    if (hit) { selectEntry(hit.entry_id); return; }
  }
  if (S.selEntry && !ev.altKey) { S.cursor = null; moveMarker(S.selEntry, frame); }
  else { S.cursor = frame; drawStrip(); renderFrame(); }
});
$("zoom-in").onclick = () => { S.autoView = false; S.zx = Math.min(8, S.zx * 2); keepScroll(); };
$("zoom-fit").onclick = () => { S.autoView = false; const r = S.race; if (!r || !r.analysis) return;
  S.zx = Math.max(0.05, $("strip-scroll").clientWidth / r.analysis.strip.w); drawStrip(); $("strip-scroll").scrollLeft = S.rtl ? $("strip-scroll").scrollWidth : 0; };
$("btn-dir").onclick = () => { S.autoView = false; S.rtl = !S.rtl; try { localStorage.setItem("timing-rtl", S.rtl ? "1" : "0"); } catch (e) { /* ignore */ }
  const f = activeFrame(); drawStrip(); if (f !== null) centerOn(f); updateDirButton(); };
function updateDirButton() { $("btn-dir").textContent = S.rtl ? "Time ←" : "Time →"; }
updateDirButton();
$("zoom-out").onclick = () => { S.autoView = false; S.zx = Math.max(0.125, S.zx / 2); keepScroll(); };
function scrollToFinishes() {      // default view: t0 at the left edge, running left to right past the last finish
  const r = S.race; if (!r) return;
  const fs = r.entries.map((e) => e.final_frame).filter((f) => f !== null), sc = $("strip-scroll");
  if (fs.length) {
    const span = Math.max(...fs) - (r.n_flash - 0.25 * r.fps) + 0.6 * r.fps;   // from 0.25 s before t0 to 0.6 s after the last finish
    S.zx = Math.min(4, Math.max(0.1, sc.clientWidth / span)); drawStrip();
    // t0 (minus a lead-in) at the starting edge: left edge, or right edge when time runs right to left
    const edge = posOf(r, r.n_flash - 0.25 * r.fps) + (S.rtl ? S.zx / 2 : -S.zx / 2);
    sc.scrollLeft = S.rtl ? edge - sc.clientWidth : edge;
  } else { sc.scrollLeft = 0; }
}
function keepScroll() {
  const sc = $("strip-scroll"), r = S.race;
  const f = activeFrame(); drawStrip();
  if (r && f !== null) centerOn(f);
}

/* ---------- frame viewer ---------- */
function renderFrame() {
  const r = S.race, img = $("frame-img"), info = $("frame-info"), ov = $("frame-overlay");
  const f = activeFrame();
  if (f === null || !r.analysis) { img.removeAttribute("src"); info.textContent = ""; ov.width = ov.height = 0; return; }
  const src = `/api/races/${r.id}/frame/${f}.jpg`;
  if (img.dataset.src !== src) { img.dataset.src = src; img.src = src; }
  const rel = f - r.first_idx;
  info.innerHTML = `frame ${f} (${rel >= 0 ? "+" : ""}${f - r.n_flash} from t0)<br>race time ${(frameTimeMs(f) / 1000).toFixed(3)} s`;
  img.onload = drawFrameOverlay;
}
function drawFrameOverlay() {
  const r = S.race, img = $("frame-img"), ov = $("frame-overlay");
  if (!r || !r.analysis || !img.naturalWidth) return;
  ov.width = img.clientWidth; ov.height = img.clientHeight;
  ov.style.left = img.offsetLeft + "px";
  const c = ov.getContext("2d"), sx = img.clientWidth / img.naturalWidth;
  c.strokeStyle = "#ff3b30"; c.lineWidth = 1; c.beginPath();
  c.moveTo(r.analysis.finish_x * sx, 0); c.lineTo(r.analysis.finish_x * sx, ov.height); c.stroke();
}

/* ---------- live capture ---------- */
function renderLiveButtons() {
  const st = S.state, busy = st.state === "recording" || st.state === "analysing";
  const target = armTarget();
  $("btn-arm").disabled = busy || !target; $("btn-disarm").disabled = st.state !== "armed";
  $("btn-arm").textContent = st.state === "armed" ? "Re-arm" : "Arm" + (target ? ` ${target.event} · ${target.heat}` : "");
}
function armTarget() {
  const r = S.races.find((x) => x.id === S.raceId);
  if (r && r.status !== "locked") return r;
  return S.races.find((x) => x.status === "scheduled");
}
function drawLiveOverlay() {
  const img = $("live-img"), cv = $("live-overlay"), st = S.state, s = S.settings;
  const show = st.frame_w > 0 && img.clientWidth > 0;
  $("live-empty").style.display = show ? "none" : "flex"; img.style.visibility = show ? "visible" : "hidden";
  if (!show) { cv.width = cv.height = 0; return; }
  cv.width = img.clientWidth; cv.height = img.clientHeight;
  const k = img.clientWidth / st.frame_w, c = cv.getContext("2d");
  c.lineWidth = 1.5;
  c.strokeStyle = "#ff3b30"; c.beginPath(); c.moveTo(s.finish_x * k, 0); c.lineTo(s.finish_x * k, cv.height); c.stroke();
  const roi = (a, label, col) => { if (!a) return; c.strokeStyle = col; c.strokeRect(a[0] * k, a[1] * k, a[2] * k, a[3] * k); c.fillStyle = col; c.font = "10px system-ui"; c.fillText(label, a[0] * k, a[1] * k - 2); };
  roi(s.start_led_roi, "start", "#ffd400"); roi(s.ref_led_roi, "ref", "#35d07f"); roi(s.link_led_roi, "link", "#4cc9f0");
  (s.lanes || []).forEach((ln, i) => {
    c.fillStyle = LANE_COLORS[(ln.lane - 1) % 8] + "55"; c.fillRect(0, ln.y0 * k, cv.width, Math.max(2, (ln.y1 - ln.y0) * k));
    c.fillStyle = LANE_COLORS[(ln.lane - 1) % 8]; c.font = "bold 10px system-ui"; c.fillText("L" + ln.lane, cv.width - 18, ln.y0 * k - 1);
  });
}
$("live-overlay").addEventListener("click", async (ev) => {
  const sel = $("place").value; if (!sel) return;
  const k = $("live-img").clientWidth / S.state.frame_w, rect = $("live-overlay").getBoundingClientRect();
  const x = Math.round((ev.clientX - rect.left) / k), y = Math.round((ev.clientY - rect.top) / k), s = S.settings;
  const patch = {};
  if (sel === "finish") patch.finish_x = x;
  else if (sel === "start" || sel === "ref" || sel === "link") {
    const key = { start: "start_led_roi", ref: "ref_led_roi", link: "link_led_roi" }[sel], old = s[key] || [0, 0, 30, 30];
    patch[key] = [x - Math.round(old[2] / 2), y - Math.round(old[3] / 2), old[2], old[3]];
  } else if (sel.startsWith("lane")) {
    const n = Number(sel.slice(4)); patch.lanes = s.lanes.map((ln) => ln.lane === n ? { ...ln, y0: y - 12, y1: y + 3 } : ln);
  }
  S.settings = await api("/api/settings", { method: "PUT", body: patch });
  $("place").value = ""; drawLiveOverlay(); toast("Position saved");
});
function fillPlaceMenu() {
  const lanes = (S.settings.lanes || []).map((l) => `<option value="lane${l.lane}">Lane ${l.lane} feet</option>`).join("");
  $("place").innerHTML = `<option value="">choose, then click the image</option><option value="finish">Finish line</option>
    <option value="start">Start LED</option><option value="ref">Reference LED</option><option value="link">Link LED</option>${lanes}`;
}

async function pollState() {
  try {
    const st = await api("/api/state");
    const was = S.state.state;
    S.state = st;
    const b = $("badge"); b.textContent = st.state.toUpperCase(); b.className = "badge " + st.state;
    $("live-msg").textContent = st.message;
    $("live-kv").innerHTML = [
      ["source", st.source === "video" ? `video · ${st.video.name}` : st.source === "browser" ? "browser camera"
        : st.source + (st.source === "agent" ? (st.agent_connected ? " (connected)" : " (not connected)") : "")],
      ["input", `${st.fps_in} fps · queue ${st.queue}`],
      ["frames", `${st.frames_in}` + (st.frames_dropped_queue ? ` · dropped ${st.frames_dropped_queue}` : "")],
      ["baseline", st.state === "armed" ? (st.baseline_ready ? "ready" : "learning…") : "–"],
      ["recording", st.state === "recording" ? `${st.recording_frames} frames` : "–"],
    ].map(([k, v]) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join("");
    $("btn-sim").hidden = st.configured_source !== "sim" || st.source !== "sim";
    renderVideoBar(st.video);
    if (was !== st.state) await refreshRaces();
    drawLiveOverlay(); renderLiveButtons();
  } catch (e) { /* server not reachable: keep polling */ }
}

/* ---------- buttons and dialogs ---------- */
$("btn-arm").onclick = async () => { const t = armTarget(); if (!t) return;
  if (t.status === "review" && !confirm("This race already has a recording. Record it again and replace it?")) return;
  await api(`/api/races/${t.id}/arm`, { method: "POST" }); S.raceId = t.id; await pollState(); await refreshRaces(); };
$("btn-disarm").onclick = async () => { await api("/api/disarm", { method: "POST" }); await pollState(); await refreshRaces(); };
$("btn-sim").onclick = async () => { const r = await api("/api/sim/start", { method: "POST", body: { delay_s: 1.5 } });
  toast("Simulated start. True times: " + Object.entries(r.times).map(([l, t]) => `L${l} ${t.toFixed(2)}`).join("  ")); };
$("btn-undo").onclick = () => stepHistory("undo");
$("btn-redo").onclick = () => stepHistory("redo");
$("btn-lock").onclick = async () => { const r = S.race; if (!r) return;
  S.race = await api(`/api/races/${r.id}/lock`, { method: "POST", body: { locked: r.status !== "locked" } }); renderAll(); refreshRaces(); };
$("btn-reanalyze").onclick = async () => { if (!confirm("Run the detection again? Manual corrections of this race are replaced (Undo brings them back).")) return;
  S.race = await api(`/api/races/${S.race.id}/reanalyze`, { method: "POST" }); S.stripKey = null; await loadRace(); refreshRaces(); };
$("session").onchange = (ev) => { S.raceId = null; loadSessions(Number(ev.target.value)); };
$("btn-new-session").onclick = async () => { const name = prompt("Session name", `Training ${new Date().toISOString().slice(0, 10)}`); if (!name) return;
  const r = await api("/api/sessions", { method: "POST", body: { name } }); S.raceId = null; loadSessions(r.id); };
$("btn-new-race").onclick = async () => { const event = prompt("Event", "100 m"); if (!event) return;
  const lanes = Number(prompt("Number of lanes", "4")) || 4;
  const heat = String(S.races.filter((r) => r.event === event).length + 1);
  const r = await api(`/api/sessions/${S.sessionId}/races`, { method: "POST", body: { event, heat, lanes } });
  S.raceId = r.id; await refreshRaces(); };
$("file-startlist").onchange = async (ev) => { const f = ev.target.files[0]; if (!f) return;
  const res = await api(`/api/sessions/${S.sessionId}/startlist`, { method: "POST", body: await f.text(), type: "text/csv" });
  toast(`Imported ${res.athletes} athletes in ${res.races} races` + (res.warnings && res.warnings.length ? ` – ${res.warnings.join("; ")}` : "")); ev.target.value = ""; refreshRaces(); };

$("btn-calib").onclick = async () => {
  const c = await api("/api/calibration"), f = document.querySelector("#dlg-calib form");
  ["audio_ms", "led_ms", "cable_ms", "radio_ms", "note"].forEach((k) => f.elements[k].value = c[k] ?? "");
  $("dlg-calib").showModal();
};
$("calib-save").onclick = async () => {
  const f = document.querySelector("#dlg-calib form"), v = {};
  ["audio_ms", "led_ms", "cable_ms", "radio_ms"].forEach((k) => v[k] = Number(f.elements[k].value) || 0);
  v.note = f.elements.note.value; v.date = new Date().toISOString().slice(0, 10);
  await api("/api/calibration", { method: "PUT", body: v }); toast("Calibration saved");
};
$("btn-settings").onclick = async () => { S.settings = await api("/api/settings"); $("settings-text").value = JSON.stringify(S.settings, null, 2); $("dlg-settings").showModal(); };
$("settings-save").onclick = async (ev) => {
  try { const v = JSON.parse($("settings-text").value); S.settings = await api("/api/settings", { method: "PUT", body: v }); fillPlaceMenu(); toast("Settings saved"); }
  catch (e) { ev.preventDefault(); toast("Invalid JSON: " + e.message); }
};
window.addEventListener("resize", () => { drawLiveOverlay(); });
new ResizeObserver(() => drawLiveOverlay()).observe($("live-img"));
new ResizeObserver(() => drawFrameOverlay()).observe($("frame-img"));
let stripRO; new ResizeObserver(() => { cancelAnimationFrame(stripRO); stripRO = requestAnimationFrame(() => { if (S.autoView && S.stripImg) scrollToFinishes(); else drawStrip(); }); }).observe($("strip-scroll"));

/* ---------- resizable panes (sizes are remembered in this browser) ---------- */
(function splitters() {
  const main = $("main"), KEY = "timing-layout";
  const DEF = { "--c-list": "230px", "--c-res": "480px", "--r-top": "46%" };
  let saved = {}; try { saved = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { /* ignore */ }
  const apply = (k, v) => main.style.setProperty(k, v);
  for (const k in DEF) apply(k, saved[k] || DEF[k]);
  const save = (k, v) => { saved[k] = v; try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) { /* ignore */ } };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  function wire(id, varName, calc) {
    const el = $(id);
    el.addEventListener("pointerdown", (ev) => {
      ev.preventDefault(); el.setPointerCapture(ev.pointerId); el.classList.add("drag"); document.body.classList.add("dragging");
      const move = (e) => { const rect = main.getBoundingClientRect(); const v = calc(e, rect); apply(varName, v); };
      const up = () => { el.classList.remove("drag"); document.body.classList.remove("dragging");
        el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up);
        save(varName, main.style.getPropertyValue(varName)); };
      el.addEventListener("pointermove", move); el.addEventListener("pointerup", up);
    });
    el.addEventListener("dblclick", () => { apply(varName, DEF[varName]); save(varName, DEF[varName]); });
  }
  wire("sp-list", "--c-list", (e, r) => clamp(e.clientX - r.left - 8, 120, r.width * 0.4) + "px");
  wire("sp-res", "--c-res", (e, r) => clamp(r.right - 8 - e.clientX - 4, 260, r.width * 0.7) + "px");
  wire("sp-row", "--r-top", (e, r) => clamp(e.clientY - r.top - 8, 140, r.height - 180) + "px");
})();
$("live-img").addEventListener("load", drawLiveOverlay);

/* ---------- start ---------- */
(async function init() {
  $("live-img").src = "/api/live.mjpg";
  S.settings = await api("/api/settings"); fillPlaceMenu();
  await pollState(); await loadSessions();
  setInterval(pollState, 500);
  setInterval(refreshRaces, 2000);
})();


/* ---------- image source: browser camera and recorded video ---------- */
function renderVideoBar(v) {
  const on = v && (v.state === "playing" || v.state === "paused");
  $("video-bar").hidden = !on && !(v && v.state === "ended");
  if (!v) return;
  $("vb-name").textContent = `${v.name} · ${v.state}${v.state === "playing" ? ` ${v.speed}×` : ""}`;
  $("vb-prog").max = Math.max(1, v.total); $("vb-prog").value = v.pos;
  $("vb-pause").textContent = v.state === "paused" ? "Resume" : "Pause";
  $("vb-pause").disabled = !on;
}
$("vb-pause").onclick = () => api("/api/video/pause", { method: "POST", body: { paused: S.state.video.state !== "paused" } }).then(pollState);
$("vb-stop").onclick = () => api("/api/video/stop", { method: "POST" }).then(pollState);

const Cam = { stream: null, ws: null, video: null, canvas: null, n: 0, inflight: 0, on: false, sent: 0 };
async function camDevices() {
  try {
    const devs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
    const cur = $("cam-dev").value;
    $("cam-dev").innerHTML = `<option value="">default</option>` + devs.map((d, i) =>
      `<option value="${esc(d.deviceId)}">${esc(d.label || "Camera " + (i + 1))}</option>`).join("");
    $("cam-dev").value = cur;
  } catch (e) { /* no camera API */ }
}
async function camStart() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { toast("This browser cannot open a camera here (needs localhost or https)"); return; }
  if (S.state.agent_connected && S.state.source === "agent") { toast("The host capture agent is connected: stop it first"); return; }
  const [w, h] = $("cam-res").value.split("x").map(Number), dev = $("cam-dev").value;
  try {
    Cam.stream = await navigator.mediaDevices.getUserMedia({ audio: false,
      video: { deviceId: dev ? { exact: dev } : undefined, width: { ideal: w }, height: { ideal: h }, frameRate: { ideal: 120 } } });
  } catch (e) { toast("Camera: " + (e.message || e.name)); return; }
  await camDevices();
  const fps = Cam.stream.getVideoTracks()[0].getSettings().frameRate;
  if (fps > 1) await api("/api/settings", { method: "PUT", body: { nominal_fps: Math.round(fps * 10) / 10 } });
  Cam.video = document.createElement("video"); Cam.video.muted = true; Cam.video.playsInline = true;
  Cam.video.srcObject = Cam.stream; await Cam.video.play();
  Cam.canvas = document.createElement("canvas");
  Cam.ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/ingest?src=browser`);
  Cam.ws.binaryType = "arraybuffer";
  Cam.ws.onclose = () => { if (Cam.on) camStop(true); };
  await new Promise((res, rej) => { Cam.ws.onopen = res; Cam.ws.onerror = () => rej(new Error("server connection failed")); }).catch((e) => { toast(e.message); camStop(); });
  if (!Cam.ws) return;
  Cam.on = true; Cam.n = 0; Cam.inflight = 0; renderCamButton();
  const grab = () => {
    if (!Cam.on) return;
    const v = Cam.video;
    if (v.videoWidth && Cam.inflight < 3 && Cam.ws.bufferedAmount < 4e6) {
      if (Cam.canvas.width !== v.videoWidth) { Cam.canvas.width = v.videoWidth; Cam.canvas.height = v.videoHeight; }
      Cam.canvas.getContext("2d").drawImage(v, 0, 0);
      const idx = Cam.n++, t = Date.now() / 1000; Cam.inflight++;
      Cam.canvas.toBlob(async (blob) => {
        try {
          if (blob && Cam.on) {
            const head = new ArrayBuffer(16), dv = new DataView(head);
            dv.setBigUint64(0, BigInt(idx), true); dv.setFloat64(8, t, true);
            Cam.ws.send(new Blob([head, blob]));
          }
        } finally { Cam.inflight--; }
      }, "image/jpeg", 0.85);
    }
    if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(grab); else requestAnimationFrame(grab);
  };
  grab();
  toast(`Browser camera on (${Math.round(fps || 0)} fps requested)`);
}
function camStop(lost) {
  Cam.on = false;
  if (Cam.stream) Cam.stream.getTracks().forEach((t) => t.stop());
  if (Cam.ws) { Cam.ws.onclose = null; try { Cam.ws.close(); } catch (e) { /* closed */ } }
  Cam.stream = Cam.ws = Cam.video = null; renderCamButton();
  if (lost) toast("Browser camera disconnected");
}
function renderCamButton() { $("cam-toggle").textContent = Cam.on ? "Stop browser camera" : "Start browser camera"; }
$("cam-toggle").onclick = () => (Cam.on ? camStop() : camStart());
window.addEventListener("beforeunload", () => camStop());

async function renderVideoList() {
  const r = await api("/api/videos");
  $("video-list").innerHTML = r.files.length ? r.files.map((f) => `<div class="vrow" data-n="${esc(f.name)}">
      <span class="nm" title="${esc(f.name)}">${esc(f.name)}</span>
      <span class="meta">${f.error ? "unreadable" : `${f.w}×${f.h} · ${f.fps} fps · ${f.frames} fr`}</span>
      <button data-a="play" ${f.error ? "disabled" : ""}>Play</button><button data-a="del" title="Delete the file">✕</button></div>`).join("")
    : `<div class="muted">No video imported yet</div>`;
  const st = S.state.agent_connected ? "Host capture agent connected." : "Host capture agent not connected (see README).";
  $("src-agent").textContent = S.state.configured_source === "sim" ? "Source: built-in simulator." : st;
}
$("video-list").onclick = async (ev) => {
  const b = ev.target.closest("button"); if (!b) return;
  const name = b.closest(".vrow").dataset.n;
  if (b.dataset.a === "del") { if (!confirm(`Delete ${name}?`)) return; await api(`/api/videos/${encodeURIComponent(name)}`, { method: "DELETE" }); renderVideoList(); return; }
  if (Cam.on) camStop();
  await api("/api/video/play", { method: "POST", body: { name, speed: Number($("vid-speed").value), loop: $("vid-loop").checked } });
  $("dlg-source").close(); await pollState();
  toast(S.state.state === "armed" ? "Playing: the flash will start the recording" : "Playing. Arm a race to record the start flash");
};
$("file-video").onchange = (ev) => {
  const f = ev.target.files[0]; ev.target.value = ""; if (!f) return;
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/videos?name=" + encodeURIComponent(f.name));
  xhr.upload.onprogress = (e) => { $("up-prog").textContent = e.lengthComputable ? `Uploading ${Math.round(100 * e.loaded / e.total)} %` : "Uploading…"; };
  xhr.onload = () => { $("up-prog").textContent = "";
    if (xhr.status === 200) { toast("Video imported"); renderVideoList(); }
    else { let m = xhr.statusText; try { m = JSON.parse(xhr.responseText).detail || m; } catch (e) { /* plain text */ } toast(m); } };
  xhr.onerror = () => { $("up-prog").textContent = ""; toast("Upload failed"); };
  xhr.send(f);
};
$("btn-source").onclick = async () => { await pollState(); await camDevices(); await renderVideoList(); $("dlg-source").showModal(); };
