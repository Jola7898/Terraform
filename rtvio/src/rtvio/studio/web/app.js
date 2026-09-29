/* RTVIO Studio front end. Plain JS, no build step: polls /api/state once a
   second and /api/sessions every few seconds, and renders. */
"use strict";

const $ = (id) => document.getElementById(id);
let STATE = null;
let SESSIONS = [];
let settingsDirty = 0;          // ms timestamp of the last local edit
let SOURCE = "drone";           // which live source the left column shows
let droneConnShown = false;

/* ------------------------------------------------------ server address */
// config.js sets RTVIO_API_URL when this page is hosted apart from the
// processing PC (Vercel). Then every request goes to that URL with a bearer
// token from sign-in, and the gate below covers the page while the PC is
// offline. Empty: the page came from the Studio itself (cookie sign-in).
const API = String(window.RTVIO_API_URL || "").replace(/\/+$/, "");
const REMOTE = API !== "";
const TOKEN_KEY = "rtvio_token";
function getToken() { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; } }
function setToken(t) { try { if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY); } catch (e) { /* private mode */ } }
function authHeaders() { return REMOTE && getToken() ? { Authorization: "Bearer " + getToken() } : {}; }
// For URLs the browser loads by itself (<img>, <iframe>, links, PLYLoader):
// those cannot carry a header, so the token rides in the query string.
function u(path) {
  if (!REMOTE) return path;
  const t = getToken();
  return API + path + (t ? (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t) : "");
}
function signInRequired() {
  if (!REMOTE) { location.href = "/login"; return; }
  setToken("");
  gate("login");
}

async function api(method, path, body) {
  const res = await fetch(API + path, {
    method, headers: { ...(body ? { "Content-Type": "application/json" } : {}), ...authHeaders() },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { signInRequired(); throw new Error("sign in required"); }
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.error) || res.statusText);
  return data;
}

const fmtBytes = (b) => b > 1e9 ? (b / 1e9).toFixed(2) + " GB" : b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : (b / 1e3).toFixed(0) + " KB";
const fmtDur = (s) => {
  if (s == null || isNaN(s)) return "–";
  s = Math.max(0, Math.round(s));
  const m = Math.floor(s / 60), r = s % 60;
  return m >= 60 ? `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const viewBtn = (base, title, kind, label) =>
  `<button class="primary" data-view="${encodeURIComponent(base)}|${encodeURIComponent(title)}|${kind}">${label}</button>`;

/* ------------------------------------------------------------ settings */

function getPath(obj, path) { return path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj); }
function patchFor(path, value) {
  const keys = path.split("."), out = {};
  let o = out;
  keys.slice(0, -1).forEach((k) => { o = o[k] = {}; });
  o[keys[keys.length - 1]] = value;
  return out;
}

function bindSettings() {
  document.querySelectorAll("[data-setting]").forEach((el) => {
    el.addEventListener("change", async () => {
      settingsDirty = Date.now();
      let v = el.type === "checkbox" ? el.checked : el.value;
      const t = el.dataset.type;
      if (t === "int") v = parseInt(v, 10);
      if (t === "float") v = parseFloat(v);
      if (el.dataset.setting === "recon.window_frames" && v !== "auto") {
        const n = parseInt(v, 10);
        v = isNaN(n) ? "auto" : n;
      }
      try { await api("POST", "/api/settings", patchFor(el.dataset.setting, v)); }
      catch (e) { alert("Could not save setting: " + e.message); }
    });
  });
  const q = document.querySelector('[data-setting="capture.jpeg_quality"]');
  q.addEventListener("input", () => { $("qOut").textContent = q.value; });
  const dq = document.querySelector('[data-setting="drone.jpeg_quality"]');
  dq.addEventListener("input", () => { $("droneQOut").textContent = dq.value; });
}

function renderSettings(settings) {
  if (Date.now() - settingsDirty < 3000) return;   // don't fight the user's edit
  document.querySelectorAll("[data-setting]").forEach((el) => {
    if (document.activeElement === el) return;
    const v = getPath(settings, el.dataset.setting);
    if (v === undefined) return;
    if (el.type === "checkbox") el.checked = !!v; else el.value = v;
  });
  $("qOut").textContent = settings.capture.jpeg_quality;
  $("droneQOut").textContent = settings.drone.jpeg_quality;
  // Nothing can connect until the drone's IP is set, so show where on a first visit.
  if (!droneConnShown) { droneConnShown = true; if (!settings.drone.ip) $("droneConnCard").open = true; }
}

/* --------------------------------------------------------------- phone */

let previewOn = false;
function renderPhone(st) {
  const p = st.phone, s = p.status || {}, rec = p.recording;
  const pill = $("phonePill");
  let pillText = "phone disconnected", pillCls = "pill-off";
  if (p.connected && !p.remote_control) { pillText = "phone connected (old app – no remote control)"; pillCls = "pill-warn"; }
  else if (p.connected) {
    if (s.state === "recording") { pillText = "● recording"; pillCls = "pill-rec"; }
    else if (s.state === "finishing") { pillText = `uploading ${s.queued || 0} spooled frames`; pillCls = "pill-warn"; }
    else { pillText = "phone ready"; pillCls = "pill-ok"; }
  } else if (rec) { pillText = "phone link lost – take kept open"; pillCls = "pill-warn"; }
  pill.textContent = pillText;
  pill.className = "pill " + pillCls;

  $("lanIps").innerHTML = (st.lan.length ? st.lan : ["<this PC's IP>"]).map((ip) => `${esc(ip)} : ${st.phone_port}`).join("<br>");
  if (p.listen_error) $("lanIps").innerHTML = `<span style="color:var(--bad)">${esc(p.listen_error)}</span>`;
  const hasFrame = p.connected, wantStream = hasFrame && SOURCE === "phone";
  if (wantStream && !previewOn) { $("preview").src = u("/api/preview.mjpg"); previewOn = true; }
  if (!wantStream && previewOn) { $("preview").removeAttribute("src"); previewOn = false; }
  $("previewEmpty").classList.toggle("hidden", hasFrame);
  $("phoneModel").textContent = p.connected ? `${s.model || ""}${s.app ? " · app " + s.app : ""}` : "";

  const rows = [];
  if (p.connected) {
    rows.push(["Link", `${p.peer}${p.status_age_s != null && p.status_age_s > 3 ? ` <span style="color:var(--warn)">(status ${p.status_age_s}s old)</span>` : ""}`]);
    if (s.resolution) rows.push(["Camera", `${s.resolution[0]}×${s.resolution[1]} · ${s.fps_target || "?"} fps target · JPEG ${s.jpeg_quality || "?"}`]);
    if (s.fps != null) rows.push(["Capture", `${(+s.fps).toFixed(1)} fps · encode ${s.encode_ms != null ? (+s.encode_ms).toFixed(0) + " ms" : "–"}`]);
    if (s.battery != null) rows.push(["Battery", `${s.battery}%${s.charging ? " ⚡" : ""}${s.temp_c != null ? ` · ${(+s.temp_c).toFixed(0)} °C` : ""}`]);
    if (s.spool_free_mb != null) rows.push(["Phone storage", `${fmtBytes(s.spool_free_mb * 1e6)} free for spooling`]);
    if (s.gps_acc != null && s.gps_acc >= 0) rows.push(["GPS", `±${(+s.gps_acc).toFixed(0)} m`]);
    if (s.error) rows.push(["Last error", `<span style="color:var(--bad)">${esc(s.error)}</span>`]);
  }
  $("phoneKv").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");

  // Record button
  const btn = $("recordBtn");
  btn.classList.remove("stop", "busy");
  let hint = "";
  if (rec && rec.stopping) {
    btn.textContent = s.state === "finishing" ? `Uploading… ${s.queued || 0} frames left` : "Stopping…";
    btn.classList.add("busy"); btn.disabled = true;
    hint = "The phone keeps every frame it captured and uploads the backlog before the take is closed.";
  } else if (rec) {
    btn.textContent = rec.confirmed ? "Stop recording" : "Starting…";
    btn.classList.add("stop"); btn.disabled = rec.origin === "legacy";
  } else {
    btn.textContent = "Start recording";
    btn.disabled = !(p.connected && p.remote_control && s.state === "armed");
    if (!p.connected) hint = "Waiting for the phone to connect.";
    else if (!p.remote_control) hint = "The connected app is an old build without remote control — install the new APK.";
    else if (s.state !== "armed") hint = `Phone state: ${s.state || "unknown"}`;
  }
  $("recHint").textContent = hint;

  $("recBadge").classList.toggle("hidden", !(rec && !rec.stopping));
  if (rec) $("recTime").textContent = fmtDur(rec.elapsed_s);
  renderLiveViz(rec, st.jobs, "liveReconChk", "liveViz", "liveVizFrame");
  const stats = rec ? [
    [rec.frames.toLocaleString(), "frames received"],
    [rec.fps ? rec.fps.toFixed(1) : "–", "fps arriving"],
    [rec.phone_backlog || 0, "queued on phone"],
    [rec.mb.toFixed(0) + " MB", "received"],
  ] : p.last_finalized ? [
    [(p.last_finalized.frames_received ?? 0).toLocaleString(), "frames (last take)"],
    [(p.last_finalized.fps_mean ?? 0).toFixed(1), "fps mean"],
    [fmtDur(p.last_finalized.duration_s ?? 0), "duration"],
    [p.last_finalized.complete ? "yes" : "NO", "complete"],
  ] : [];
  $("recStats").innerHTML = stats.map(([v, l]) => `<div><b>${v}</b><span>${l}</span></div>`).join("");
}

// Shows the embedded recon_viz.py panel for a running "live" job (see
// jobs.py's ReconQueue.submit_live) matching the current recording, on
// either source; hides it and re-enables the checkbox once there is none.
function renderLiveViz(rec, jobs, chkId, boxId, frameId) {
  $(chkId).disabled = !!rec;
  const job = rec && (jobs || []).find((j) => j.kind === "live" && j.session === rec.id && j.state === "running");
  const box = $(boxId), frame = $(frameId);
  if (job && job.viz_url) {
    if (frame.dataset.src !== job.viz_url) { frame.src = u(job.viz_url); frame.dataset.src = job.viz_url; }
    box.classList.remove("hidden");
  } else {
    if (frame.dataset.src) { frame.removeAttribute("src"); frame.dataset.src = ""; }
    box.classList.add("hidden");
  }
}

function renderEvents(st) {
  const ev = st.phone.events.concat(st.drone.events.map(([t, e]) => [t, "[drone] " + e]));
  ev.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  $("events").textContent = ev.map(([t, e]) => `${t}  ${e}`).join("\n");
}

$("recordBtn").addEventListener("click", async () => {
  const btn = $("recordBtn");
  btn.disabled = true;
  try {
    if (STATE && STATE.phone.recording) await api("POST", "/api/record/stop");
    else await api("POST", "/api/record/start", { live: $("liveReconChk").checked });
  } catch (e) { alert(e.message); }
  pollState();
});

/* --------------------------------------------------------------- drone */

let dronePreviewOn = false, calibShown = false, calibSolving = false;
const fmtNum = (v, d = 1) => (v == null || isNaN(v) ? "–" : (+v).toFixed(d));

function renderDrone(st) {
  const d = st.drone, tl = d.telemetry, vid = d.video, v = d.vehicle_state || {}, rec = d.recording;
  const finishing = d.finishing.length > 0;

  let pillText = "drone offline", pillCls = "pill-off";
  if (!d.enabled) pillText = "drone link off";
  else if (!d.video_source && !d.ip) { pillText = "drone: set its IP"; pillCls = "pill-warn"; }
  else if (rec) { pillText = "● drone recording"; pillCls = "pill-rec"; }
  else if (finishing) { pillText = "drone: saving take"; pillCls = "pill-warn"; }
  else if (vid.connected && tl.connected) { pillText = "drone ready"; pillCls = "pill-ok"; }
  else if (vid.connected) { pillText = "drone video · no telemetry"; pillCls = "pill-warn"; }
  else if (tl.connected) { pillText = "drone telemetry · no video"; pillCls = "pill-warn"; }
  $("dronePill").textContent = pillText;
  $("dronePill").className = "pill " + pillCls;

  const wantStream = vid.connected && SOURCE === "drone";
  if (wantStream && !dronePreviewOn) { $("dronePreview").src = u("/api/drone/preview.mjpg"); dronePreviewOn = true; }
  if (!wantStream && dronePreviewOn) { $("dronePreview").removeAttribute("src"); dronePreviewOn = false; }
  $("droneEmpty").classList.toggle("hidden", vid.connected);
  $("droneEmptyTitle").textContent = !d.enabled ? "Drone link is off" : !d.video_source ? "Set the drone's IP" : "No drone video";
  $("droneEmptyText").textContent = !d.enabled ? "Turn it on under Drone connection below."
    : !d.video_source ? "Drone connection → Drone IP (the address iDronam's Add Device uses)."
    : (vid.error || `connecting to ${d.video_source}…`);
  $("droneModel").textContent = tl.connected && v.autopilot ? `${v.autopilot}${tl.vehicle ? " · system " + tl.vehicle.sysid : ""}` : "";

  // Indoor / Outdoor - latched when a take starts, so locked while recording.
  document.querySelectorAll("#droneMode button").forEach((b) => {
    b.classList.toggle("on", b.dataset.mode === d.mode);
    b.disabled = !!rec;
  });
  const noFix = tl.connected && !(v.gps_fix >= 3);
  $("droneModeHint").innerHTML = (d.mode === "outdoor"
    ? `Records the drone's GPS; the model is georeferenced (metres, east-north-up).${noFix ? ' <span style="color:var(--warn)">No 3D GPS fix yet.</span>' : ""}`
    : "Vision-only reconstruction; GPS is not used.") + (rec ? " Locked while recording." : "");

  const rows = [];
  rows.push(["Telemetry", tl.connected
    ? `${esc(tl.target)} · ${fmtNum(tl.msg_rate, 0)} msg/s${tl.age_s != null && tl.age_s > 3 ? ` <span style="color:var(--warn)">(${tl.age_s}s old)</span>` : ""}`
    : `<span class="muted">${esc(tl.error || (d.enabled && d.ip ? "connecting…" : "–"))}</span>`]);
  rows.push(["Video", vid.connected
    ? `${vid.size ? vid.size[0] + "×" + vid.size[1] : "?"} · ${fmtNum(vid.fps)} fps`
    : `<span class="muted">${esc(vid.error || "–")}</span>`]);
  rows.push(["Lens", d.camera
    ? `${esc(d.camera.model)} calibration · ${fmtNum(d.camera.hfov, 0)}° × ${fmtNum(d.camera.vfov, 0)}°`
    : '<span style="color:var(--warn)">not calibrated</span> <span class="muted">– see Camera calibration</span>']);
  $("droneUndistort").disabled = !d.camera;
  if (tl.connected) {
    if (v.flight_mode != null) rows.push(["Flight", `${esc(v.flight_mode)} · ${v.armed ? '<b style="color:var(--rec)">ARMED</b>' : "disarmed"}`]);
    if (v.gps_fix != null) rows.push(["GPS", `${esc(v.gps_fix_name)}${v.satellites != null ? ` · ${v.satellites} sats` : ""}${v.h_acc_m != null ? ` · ±${fmtNum(v.h_acc_m)} m` : v.hdop != null ? ` · HDOP ${fmtNum(v.hdop)}` : ""}`]);
    if (v.lat != null) rows.push(["Position", `${v.lat.toFixed(7)}, ${v.lon.toFixed(7)}`]);
    if (v.rel_alt_m != null) rows.push(["Altitude", `${fmtNum(v.rel_alt_m)} m above home · ${fmtNum(v.alt_msl_m)} m MSL`]);
    if (v.groundspeed != null) rows.push(["Speed", `${fmtNum(v.groundspeed)} m/s · climb ${fmtNum(v.climb)} m/s${v.heading != null ? ` · heading ${fmtNum(v.heading, 0)}°` : ""}`]);
    if (v.roll != null) rows.push(["Attitude", `roll ${fmtNum(v.roll)}° · pitch ${fmtNum(v.pitch)}° · yaw ${fmtNum(v.yaw)}°`]);
    const batt = [v.battery_pct != null ? v.battery_pct + "%" : null, v.voltage != null ? fmtNum(v.voltage, 2) + " V" : null,
                  v.current != null ? fmtNum(v.current) + " A" : null].filter(Boolean);
    if (batt.length) rows.push(["Battery", batt.join(" · ")]);
  }
  $("droneKv").innerHTML = rows.map(([k, x]) => `<dt>${k}</dt><dd>${x}</dd>`).join("");

  const btn = $("droneRecordBtn");
  btn.classList.remove("stop", "busy");
  let hint = "";
  if (rec) {
    btn.textContent = "Stop recording"; btn.classList.add("stop"); btn.disabled = false;
    hint = rec.write_error ? `Disk error: ${rec.write_error}` : "Stopping saves the take and starts its reconstruction straight away.";
  } else if (finishing) {
    btn.textContent = "Saving take…"; btn.classList.add("busy"); btn.disabled = true;
  } else {
    btn.textContent = "Start recording"; btn.disabled = !vid.connected;
    hint = vid.connected ? `Next take: ${d.mode}. Reconstruction starts as soon as you stop.` : "Waiting for the drone's video.";
  }
  $("droneRecHint").textContent = hint;

  $("droneRecBadge").classList.toggle("hidden", !rec);
  if (rec) $("droneRecTime").textContent = fmtDur(rec.elapsed_s);
  renderLiveViz(rec, st.jobs, "droneLiveReconChk", "droneLiveViz", "droneLiveVizFrame");
  const lf = d.last_finalized;
  const stats = rec ? [
    [rec.frames.toLocaleString(), "frames saved"],
    [rec.fps ? rec.fps.toFixed(1) : "–", "fps"],
    [rec.mode === "outdoor" ? rec.gps_fixes : "–", "GPS fixes"],
    [rec.dropped, "dropped"],
  ] : lf ? [
    [(lf.frames_received ?? 0).toLocaleString(), "frames (last take)"],
    [(lf.fps_mean ?? 0).toFixed(1), "fps mean"],
    [fmtDur(lf.duration_s ?? 0), "duration"],
    [esc(lf.drone_mode), lf.drone_mode === "outdoor" ? `${lf.gps_fixes} GPS fixes` : "mode"],
  ] : [];
  $("droneRecStats").innerHTML = stats.map(([x, l]) => `<div><b>${x}</b><span>${l}</span></div>`).join("");
  renderCalib(d);
}

function renderCalib(d) {
  const cam = d.camera, cal = d.calibration, rep = d.camera_reported || {};
  $("droneCalibSummary").textContent = cam
    ? `${cam.model} lens calibrated · ${fmtNum(cam.hfov, 0)}° × ${fmtNum(cam.vfov, 0)}°` : "lens not calibrated";
  if (!calibShown && d.video.connected && !cam) { calibShown = true; $("droneCalibCard").open = true; }

  const capturing = !!(cal && cal.active);
  const start = $("droneCalibStart");
  start.textContent = capturing ? "Stop capturing" : cal && cal.views ? "Resume capturing" : "Start capturing";
  start.disabled = !capturing && !d.video.connected;
  const solve = $("droneCalibSolve");
  solve.disabled = calibSolving || !(cal && cal.views >= cal.min_views);
  solve.textContent = calibSolving ? "Calibrating…" : cal ? `Calibrate (${cal.views} views)` : "Calibrate";
  $("droneCalibDiscard").classList.toggle("hidden", !cal);
  $("droneCalibRemove").classList.toggle("hidden", !cam);

  const cov = cal ? cal.coverage : [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  $("droneCalibGrid").innerHTML = cov.flat().map((n) => `<div class="${n ? "hit" : ""}">${n || ""}</div>`).join("");
  $("droneCalibStatus").innerHTML = cal
    ? `<b>${cal.views}/${cal.target}</b> views · ${esc(cal.status)}${cal.views < cal.min_views ? ` <span class="muted">(${cal.min_views} needed to calibrate)</span>` : ""}`
    : cam ? `Calibrated ${esc(cam.calibrated || "")}${cam.views ? ` from ${cam.views} views` : ""}.`
    : '<span style="color:var(--warn)">Not calibrated: reconstructions from this camera will have bent walls and opened-up corners.</span>';

  const rows = [];
  if (cam) {
    rows.push(["Model", `${esc(cam.model)} · reprojection error ${fmtNum(cam.rms_px, 2)} px`]);
    rows.push(["Field of view", `${fmtNum(cam.hfov, 0)}° × ${fmtNum(cam.vfov, 0)}° (diagonal ${fmtNum(cam.dfov, 0)}°)`]);
    rows.push(["Focal length", `fx ${fmtNum(cam.fx)} · fy ${fmtNum(cam.fy)} px at ${cam.width}×${cam.height}`]);
    if (cam.undistorted) rows.push(["VGGT sees", `a pinhole ${fmtNum(cam.undistorted.hfov, 0)}° × ${fmtNum(cam.undistorted.vfov, 0)}° after undistortion (outer edge cropped)`]);
  }
  const ci = rep.CAMERA_INFORMATION, vs = rep.VIDEO_STREAM_INFORMATION;
  if (ci) rows.push(["Drone reports", `${esc(ci.vendor_name)} ${esc(ci.model_name)} · focal ${fmtNum(ci.focal_length, 2)} mm · sensor ${fmtNum(ci.sensor_size_h, 2)} × ${fmtNum(ci.sensor_size_v, 2)} mm`]);
  if (vs) rows.push(["Stream reports", `${vs.resolution_h}×${vs.resolution_v} · ${vs.hfov}° across`]);
  if (!ci && !vs && d.telemetry.connected) rows.push(["Drone reports", '<span class="muted">no camera information over MAVLink</span>']);
  $("droneCalibKv").innerHTML = rows.map(([k, x]) => `<dt>${k}</dt><dd>${x}</dd>`).join("");
}

$("droneCalibStart").addEventListener("click", async () => {
  const cal = STATE && STATE.drone.calibration;
  try { await api("POST", cal && cal.active ? "/api/drone/calib/stop" : "/api/drone/calib/start", {}); }
  catch (e) { alert(e.message); }
  pollState();
});
$("droneCalibSolve").addEventListener("click", async () => {
  calibSolving = true;
  if (STATE) renderCalib(STATE.drone);
  try { await api("POST", "/api/drone/calib/solve"); }
  catch (e) { alert(e.message); }
  calibSolving = false;
  pollState();
});
$("droneCalibDiscard").addEventListener("click", async () => {
  if (!confirm("Discard the captured checkerboard views?")) return;
  try { await api("POST", "/api/drone/calib/discard"); } catch (e) { alert(e.message); }
  pollState();
});
$("droneCalibRemove").addEventListener("click", async () => {
  if (!confirm("Remove the lens calibration? New takes will not be straightened (a copy is kept as drone_camera.json.bak).")) return;
  try { await api("POST", "/api/drone/camera/remove"); } catch (e) { alert(e.message); }
  pollState();
});

$("droneRecordBtn").addEventListener("click", async () => {
  $("droneRecordBtn").disabled = true;
  try {
    if (STATE && STATE.drone.recording) await api("POST", "/api/drone/record/stop");
    else await api("POST", "/api/drone/record/start", { live: $("droneLiveReconChk").checked });
  } catch (e) { alert(e.message); }
  pollState(); pollSessions();
});

document.querySelectorAll("#droneMode button").forEach((b) => b.addEventListener("click", async () => {
  try { await api("POST", "/api/settings", { drone: { mode: b.dataset.mode } }); }
  catch (e) { alert("Could not change mode: " + e.message); }
  pollState();
}));

function showSource(which) {
  SOURCE = which === "phone" ? "phone" : "drone";
  document.querySelectorAll("#sourceTabs button").forEach((b) => b.classList.toggle("on", b.dataset.source === SOURCE));
  $("droneTab").classList.toggle("hidden", SOURCE !== "drone");
  $("phoneTab").classList.toggle("hidden", SOURCE !== "phone");
  try { localStorage.setItem("rtvio.studio.source", SOURCE); } catch (e) { /* private mode */ }
  if (STATE) { renderDrone(STATE); renderPhone(STATE); }
}
document.querySelectorAll("[data-source]").forEach((el) => el.addEventListener("click", () => showSource(el.dataset.source)));
try { SOURCE = localStorage.getItem("rtvio.studio.source") || "drone"; } catch (e) { /* default */ }
showSource(SOURCE);

/* ----------------------------------------------------------------- gpu */

function renderGpu(g) {
  const s = g.samples, last = s[s.length - 1];
  $("gpuUtil").textContent = last ? `${last.util.toFixed(0)}%` : "–";
  $("gpuMem").textContent = last ? `${(last.mem_mb / 1024).toFixed(1)}/${(last.mem_total_mb / 1024).toFixed(0)} GB · ${last.temp_c.toFixed(0)}°C${last.power_w ? " · " + last.power_w.toFixed(0) + " W" : ""}` : (g.error || "");
  const c = $("gpuSpark"), ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  if (s.length < 2) return;
  ctx.beginPath();
  s.forEach((p, i) => {
    const x = (i / (s.length - 1)) * c.width, y = c.height - (p.util / 100) * (c.height - 2) - 1;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.strokeStyle = "#4cc2ff"; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.lineTo(c.width, c.height); ctx.lineTo(0, c.height); ctx.closePath();
  ctx.fillStyle = "rgba(76,194,255,.15)"; ctx.fill();
}

/* ------------------------------------------------------------ sessions */

function latestJobFor(sid) {
  if (!STATE) return null;
  const js = STATE.jobs.filter((j) => j.session === sid);
  return js.length ? js[js.length - 1] : null;
}

function stageLine(job, recon) {
  const pr = (job && job.progress) || (recon && recon.progress) || null;
  const state = job ? job.state : pr ? (pr.stage === "done" ? "done" : pr.stage === "error" ? "failed" : "stale") : null;
  if (!state) return { html: `<span class="muted">not reconstructed</span>`, frac: null };
  if (state === "queued") return { html: `<span class="stage">queued for GPU</span>`, frac: 0 };
  if (state === "done") {
    const t = pr && pr.elapsed_s ? ` in ${fmtDur(pr.elapsed_s)}` : "";
    const g = job && job.gpu_util_mean != null ? ` · GPU ${job.gpu_util_mean}% avg` : "";
    const n = pr && pr.points ? ` · ${(+pr.points).toLocaleString()} pts` : "";
    return { html: `<span class="stage done">✓ reconstructed${t}${n}${g}</span>`, frac: null };
  }
  if (state === "failed" || state === "cancelled") {
    const err = (job && job.error) || (pr && pr.error) || state;
    return { html: `<span class="stage failed">✗ ${esc(err).slice(0, 180)}</span>`, frac: null };
  }
  if (state === "stale") return { html: `<span class="muted">interrupted (${esc(pr.stage)})</span>`, frac: null };
  const frac = pr && pr.fraction != null ? pr.fraction : 0;
  const eta = pr && pr.eta_s != null ? ` · ETA ${fmtDur(pr.eta_s)}` : "";
  return { html: `<span class="stage">${esc((pr && pr.detail) || "starting…")}${eta}</span>`, frac };
}

function renderSessions() {
  const box = $("sessions");
  if (!SESSIONS.length) { box.innerHTML = `<div class="muted">No recordings yet. Connect the phone and press Start.</div>`; return; }
  box.innerHTML = SESSIONS.map((s) => {
    // One session's unexpected meta shape must never blank the whole list
    // (it used to: a single .toLocaleString() on an undefined field threw
    // out of this whole .map(), and pollSessions()'s catch swallowed it
    // silently every 2.5s with no visible error).
    try { return renderSessionCard(s); }
    catch (e) { return `<div class="session"><div><div class="title">${esc(s.id)}</div>
      <div class="facts warn">could not render this session: ${esc(e.message)}</div></div></div>`; }
  }).join("");
}

function renderSessionCard(s) {
    const m = s.meta, rec = s.recons[s.recons.length - 1];
    const job = latestJobFor(s.id);
    const facts = [];
    if (s.recording) facts.push(`<span style="color:var(--rec)">recording…</span>`);
    if (m && m.origin === "drone") {
      facts.push(`<span class="tag">drone · ${esc(m.drone_mode || "?")}${m.drone_mode === "outdoor" ? ` · ${m.gps_fixes || 0} GPS fixes` : ""}${m.camera ? " · lens calibrated" : ""}</span>`);
    }
    if (m) {
      const framesN = m.frames_received ?? m.frames_written;
      const durS = m.duration_s ?? m.total_time_s;
      const fpsV = m.fps_mean ?? m.fps;
      if (framesN != null) facts.push(`${framesN.toLocaleString()} frames`);
      if (durS != null) facts.push(fmtDur(durS));
      if (fpsV != null) facts.push(`${fpsV} fps`);
      if (m.resolution) facts.push(`${m.resolution[0]}×${m.resolution[1]}`);
      if (!m.complete && m.frames_reported_sent != null) facts.push(`<span class="warn">phone sent ${m.frames_reported_sent}</span>`);
      if (m.frame_gaps) facts.push(`<span class="warn">${m.frame_gaps} gaps</span>`);
      if (m.frames_dropped) facts.push(`<span class="warn">${m.frames_dropped} dropped</span>`);
    }
    const st = stageLine(job, rec);
    const outs = rec ? rec.files : {};
    const base = rec ? `/files/${encodeURIComponent(s.id)}/${rec.name}/` : "";
    const title = `${s.id} / ${rec ? rec.name : ""}`;
    const running = job && ["queued", "running", "cancelling"].includes(job.state);
    const actions = [];
    if (job && job.viz_url) actions.push(`<a class="btn" href="${u(job.viz_url)}" target="_blank">Watch live</a>`);
    if (outs["cloud_raw.ply"]) actions.push(viewBtn(base, title, "cloud", `View cloud · ${fmtBytes(outs["cloud_raw.ply"])}`));
    if (outs["mesh_poisson.ply"]) actions.push(viewBtn(base, title, "mesh", `View mesh · ${fmtBytes(outs["mesh_poisson.ply"])}`));
    if (outs["cloud_raw.ply"]) actions.push(`<a class="btn" href="${u(base + "cloud_raw.ply")}" download="${s.id}_cloud_raw.ply">cloud_raw.ply</a>`);
    if (outs["mesh_poisson.ply"]) actions.push(`<a class="btn" href="${u(base + "mesh_poisson.ply")}" download="${s.id}_mesh_poisson.ply">mesh_poisson.ply</a>`);
    if (outs["CHECKPOINT_REPORT.md"]) actions.push(`<a class="btn" href="${u(base + "CHECKPOINT_REPORT.md")}" target="_blank">report</a>`);
    if (m && !s.recording) actions.push(`<a class="btn" href="${u(`/api/sessions/${encodeURIComponent(s.id)}/export`)}" download="${s.id}.zip">Export .zip</a>`);
    if (running && job.state !== "cancelling") actions.push(`<button data-cancel="${job.id}">Cancel</button>`);
    else if (m && !s.recording) actions.push(`<button data-recon="${s.id}">${rec ? "Reconstruct again" : "Reconstruct"}</button>`);
    if (job && job.state !== "queued") actions.push(`<button data-log="${job.id}">log</button>`);
    if (rec && !running) actions.push(`<button class="danger" data-delete-recon="${s.id}|${rec.name}">Delete</button>`);
    return `<div class="session${s.recording ? " live" : ""}">
      ${s.thumb ? `<img src="${u(`/files/${encodeURIComponent(s.id)}/${s.thumb}`)}" loading="lazy" alt="">` : `<img alt="">`}
      <div>
        <div class="title">${esc(s.id)}</div>
        <div class="facts">${facts.join(" · ")}</div>
        ${st.frac != null ? `<div class="progress"><div style="width:${(st.frac * 100).toFixed(1)}%"></div></div>` : ""}
        <div>${st.html}</div>
      </div>
      <div class="actions">${actions.join("")}</div>
    </div>`;
}

let VIDEO_JOBS = [];

function renderVideoJobs() {
  const box = $("videoJobs");
  if (!VIDEO_JOBS.length) { box.innerHTML = `<div class="muted">No video reconstructions yet.</div>`; return; }
  box.innerHTML = VIDEO_JOBS.map((vj) => {
    // Mirrors renderSessions()'s stageLine(job, rec) call: pass the live
    // in-memory job when there is one (vj.job_id set), else fall back to
    // reading state straight from progress.json like a session's disk-only
    // recon does - queue.jobs is empty again after every Studio restart,
    // the files and this row are not.
    const job = vj.job_id != null ? { state: vj.state, progress: vj.progress, gpu_util_mean: null, error: null } : null;
    const st = stageLine(job, { progress: vj.progress });
    const base = `/video_files/${encodeURIComponent(vj.dir)}/`;
    const outs = vj.files;
    const running = ["queued", "running", "cancelling"].includes(vj.state);
    const actions = [];
    if (vj.viz_url) actions.push(`<a class="btn" href="${u(vj.viz_url)}" target="_blank">Watch live</a>`);
    if (outs["cloud_raw.ply"]) actions.push(viewBtn(base, vj.label, "cloud", `View cloud · ${fmtBytes(outs["cloud_raw.ply"])}`));
    if (outs["mesh_poisson.ply"]) actions.push(viewBtn(base, vj.label, "mesh", `View mesh · ${fmtBytes(outs["mesh_poisson.ply"])}`));
    if (outs["cloud_raw.ply"]) actions.push(`<a class="btn" href="${u(base + "cloud_raw.ply")}" download="${vj.label}_cloud_raw.ply">cloud_raw.ply</a>`);
    if (outs["mesh_poisson.ply"]) actions.push(`<a class="btn" href="${u(base + "mesh_poisson.ply")}" download="${vj.label}_mesh_poisson.ply">mesh_poisson.ply</a>`);
    if (outs["CHECKPOINT_REPORT.md"]) actions.push(`<a class="btn" href="${u(base + "CHECKPOINT_REPORT.md")}" target="_blank">report</a>`);
    if (vj.job_id != null && running && vj.state !== "cancelling") actions.push(`<button data-cancel="${vj.job_id}">Cancel</button>`);
    if (vj.job_id != null && vj.state !== "queued") actions.push(`<button data-log="${vj.job_id}">log</button>`);
    if (!running) actions.push(`<button class="danger" data-delete-video="${vj.dir}">Delete</button>`);
    return `<div class="session">
      <img alt="">
      <div>
        <div class="title">${esc(vj.label)}</div>
        ${st.frac != null ? `<div class="progress"><div style="width:${(st.frac * 100).toFixed(1)}%"></div></div>` : ""}
        <div>${st.html}</div>
      </div>
      <div class="actions">${actions.join("")}</div>
    </div>`;
  }).join("");
}

async function onJobsClick(ev) {
  const t = ev.target.closest("button");
  if (!t) return;
  try {
    if (t.dataset.view) {
      const [base, title, kind] = t.dataset.view.split("|");
      openViewer(decodeURIComponent(base), decodeURIComponent(title), kind);
    }
    if (t.dataset.recon) { await api("POST", `/api/sessions/${t.dataset.recon}/reconstruct`, {}); pollState(); }
    if (t.dataset.cancel) { await api("POST", `/api/jobs/${t.dataset.cancel}/cancel`); pollState(); pollVideoJobs(); }
    if (t.dataset.deleteRecon) {
      if (!confirm("Delete this reconstruction's output? This cannot be undone.")) return;
      const [sid, name] = t.dataset.deleteRecon.split("|");
      await api("POST", `/api/sessions/${sid}/recons/${name}/delete`); pollSessions();
    }
    if (t.dataset.deleteVideo) {
      if (!confirm("Delete this reconstruction's output? This cannot be undone.")) return;
      await api("POST", `/api/video-jobs/${encodeURIComponent(t.dataset.deleteVideo)}/delete`); pollVideoJobs();
    }
    if (t.dataset.log) {
      const r = await api("GET", `/api/jobs/${t.dataset.log}/log`);
      const w = window.open("", "_blank");
      w.document.write(`<pre style="font:12px/1.4 Consolas,monospace;white-space:pre-wrap">${esc(r.lines.join("\n"))}</pre>`);
    }
  } catch (e) { alert(e.message); }
}
$("sessions").addEventListener("click", onJobsClick);
$("videoJobs").addEventListener("click", onJobsClick);

/* ------------------------------------------------------------- uploads */
// Files go up in chunks (server.py "chunked uploads"): each request stays
// under Cloudflare's 100 MB cap, and a dropped connection costs one chunk -
// it is retried from the size the server reports. XHR rather than fetch()
// for per-chunk upload progress.
const CHUNK_BYTES = 32 * 1024 * 1024;

function postChunk(id, offset, blob, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API}/api/uploads/${id}?offset=${offset}`);
    const h = authHeaders();
    Object.keys(h).forEach((k) => xhr.setRequestHeader(k, h[k]));
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* non-JSON */ }
      if (xhr.status === 401) { signInRequired(); reject(new Error("sign in required")); }
      else if (data && typeof data.size === "number" && (xhr.status === 200 || xhr.status === 409)) resolve(data.size);
      else reject(new Error((data && data.error) || `HTTP ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("connection lost"));
    xhr.send(blob);
  });
}

async function uploadChunked(file, statusEl, label) {
  const { id } = await api("POST", "/api/uploads");
  let offset = 0, failures = 0;
  while (offset < file.size) {
    try {
      offset = await postChunk(id, offset, file.slice(offset, offset + CHUNK_BYTES), (sent) => {
        const done = offset + sent;
        statusEl.textContent = `uploading ${label}: ${fmtBytes(done)} / ${fmtBytes(file.size)} (${Math.round(100 * done / file.size)}%)`;
      });
      failures = 0;
    } catch (e) {
      if (e.message === "sign in required" || ++failures > 5) throw e;
      statusEl.textContent = `uploading ${label}: connection dropped, retrying (${failures}/5)…`;
      await new Promise((r) => setTimeout(r, 2000 * failures));
    }
  }
  return id;
}

$("importSessionFile").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  const status = $("importStatus");
  status.textContent = `uploading ${file.name}…`;
  try {
    const id = await uploadChunked(file, status, file.name);
    status.textContent = `importing ${file.name}…`;
    const data = await api("POST", `/api/sessions/import?upload=${id}`);
    status.textContent = `imported as ${data.session}`;
    pollSessions();
  } catch (e) {
    status.textContent = "";
    alert("Import failed: " + e.message);
  }
});

// Uploads one clip to the processing PC and queues it.
async function uploadVideo(ev) {
  const file = ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  const status = $("videoUploadStatus");
  const name = file.name && /\.\w+$/.test(file.name) ? file.name : "capture.mp4";
  status.textContent = `uploading ${name}…`;
  try {
    const id = await uploadChunked(file, status, name);
    await api("POST", `/api/upload-video?upload=${id}&name=${encodeURIComponent(name)}`);
    status.textContent = `${name} uploaded - reconstruction queued`;
    pollVideoJobs();
  } catch (e) {
    status.textContent = "";
    alert("Upload failed: " + e.message);
  }
}
$("videoUploadFile").addEventListener("change", uploadVideo);
$("videoCaptureFile").addEventListener("change", uploadVideo);

$("videoReconBtn").addEventListener("click", async () => {
  // "Copy as path" in Explorer quotes the path; drop the quotes.
  const path = $("videoPath").value.trim().replace(/^["']|["']$/g, "").trim();
  if (!path) return;
  const btn = $("videoReconBtn");
  btn.disabled = true;
  try { await api("POST", "/api/reconstruct-video", { path }); pollVideoJobs(); }
  catch (e) { alert(e.message); }
  finally { btn.disabled = false; }
});

/* ------------------------------------------------------------- polling */

// READY: the page may talk to the server. Always true when the Studio
// served this page; for a remote page, only once the PC answered and we are
// signed in (see checkServer).
let READY = !REMOTE;
let pollFails = 0;

async function pollState() {
  if (!READY) return;
  try {
    STATE = await api("GET", "/api/state");
    pollFails = 0;
    renderPhone(STATE);
    renderDrone(STATE);
    renderEvents(STATE);
    renderGpu(STATE.gpu);
    renderSettings(STATE.settings);
    renderSessions();
  } catch (e) {
    $("phonePill").textContent = "studio server unreachable";
    $("phonePill").className = "pill pill-off";
    // A few misses in a row (not one blip): the PC went away.
    if (REMOTE && READY && ++pollFails >= 3) checkServer();
  }
}
async function pollVideoJobs() {
  if (!READY) return;
  try { VIDEO_JOBS = await api("GET", "/api/video-jobs"); renderVideoJobs(); } catch (e) { /* next tick */ }
}
async function pollSessions() {
  if (!READY) return;
  try { SESSIONS = await api("GET", "/api/sessions"); renderSessions(); } catch (e) { /* next tick */ }
}

/* ---------------------------------------------------------------- gate */
// Remote page only: covers the UI with "offline" while the PC does not
// answer (off, no internet, Studio not running - Cloudflare's error page for
// a down tunnel carries no CORS header, so it lands here as a failed fetch
// too), or with a sign-in form once it does.

const CHECK_EVERY_S = 10;
let checkTimer = null;

function gate(mode) {     // "checking" | "offline" | "login" | null (hidden)
  READY = mode === null;
  $("gate").classList.toggle("hidden", mode === null);
  $("gateChecking").classList.toggle("hidden", mode !== "checking");
  $("gateOffline").classList.toggle("hidden", mode !== "offline");
  $("gateLogin").classList.toggle("hidden", mode !== "login");
  if (mode === "login") setTimeout(() => $("gatePassword").focus(), 0);
}

async function checkServer() {
  clearTimeout(checkTimer);
  let health = null;
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 8000);
    const res = await fetch(API + "/api/health", { headers: authHeaders(), signal: ctl.signal, cache: "no-store" });
    clearTimeout(t);
    if (res.ok) health = await res.json();
  } catch (e) { /* offline */ }
  if (!health || !health.ok) {
    gate("offline");
    $("gateNext").textContent = `Last checked ${new Date().toLocaleTimeString()} - checking every ${CHECK_EVERY_S} s.`;
    checkTimer = setTimeout(checkServer, CHECK_EVERY_S * 1000);
    return;
  }
  if (health.auth_required && !health.authed) { setToken(""); gate("login"); return; }
  gate(null);
  pollFails = 0;
  pollState(); pollSessions(); pollVideoJobs();
}

$("gateRetry").addEventListener("click", () => { gate("checking"); checkServer(); });
$("gateLogin").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const err = $("gateError");
  err.textContent = "";
  try {
    const res = await fetch(API + "/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: $("gatePassword").value }),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok || !data) throw new Error((data && data.error) || `HTTP ${res.status}`);
    setToken(data.token);
    $("gatePassword").value = "";
    checkServer();
  } catch (e) {
    err.textContent = e instanceof TypeError ? "server unreachable" : e.message;
    if (e instanceof TypeError) checkServer();
  }
});

bindSettings();
if (REMOTE) { gate("checking"); checkServer(); }
else { pollState(); pollSessions(); pollVideoJobs(); }
setInterval(pollState, 1000);
setInterval(pollSessions, 2500);
setInterval(pollVideoJobs, 2500);

/* -------------------------------------------------------------- viewer */

const V = { renderer: null, scene: null, camera: null, controls: null, obj: null, cams: null,
  sid: null, rec: null, kind: null, radius: 1, center: new THREE.Vector3() };

function initViewer() {
  if (V.renderer) return;
  const host = $("viewerCanvas");
  V.renderer = new THREE.WebGLRenderer({ antialias: true });
  V.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  host.appendChild(V.renderer.domElement);
  V.scene = new THREE.Scene();
  V.scene.background = new THREE.Color(0x07090c);
  V.camera = new THREE.PerspectiveCamera(55, 1, 0.001, 1e5);
  V.controls = new THREE.OrbitControls(V.camera, V.renderer.domElement);
  V.controls.enableDamping = true;
  V.controls.screenSpacePanning = true;
  V.scene.add(new THREE.HemisphereLight(0xffffff, 0x303040, 0.9));
  const sun = new THREE.DirectionalLight(0xffffff, 0.6);
  sun.position.set(1, 2, 1.5);
  V.scene.add(sun);
  const resize = () => {
    const w = host.clientWidth, h = host.clientHeight;
    V.renderer.setSize(w, h); V.camera.aspect = w / Math.max(h, 1); V.camera.updateProjectionMatrix();
  };
  window.addEventListener("resize", resize);
  V.resize = resize;
  (function loop() { requestAnimationFrame(loop); if (!$("viewer").classList.contains("hidden")) { V.controls.update(); V.renderer.render(V.scene, V.camera); } })();
}

function clearObj() {
  [V.obj, V.cams].forEach((o) => {
    if (!o) return;
    V.scene.remove(o);
    o.traverse((c) => { if (c.geometry) c.geometry.dispose(); if (c.material) c.material.dispose(); });
  });
  V.obj = V.cams = null;
}

function resetView() {
  const r = V.radius, c = V.center;
  V.camera.near = r / 2000; V.camera.far = r * 200; V.camera.updateProjectionMatrix();
  // Output is Y-up with the first camera looking down -Z (see
  // vggt_reconstruct's output frame), so start just behind and above it.
  V.camera.position.set(c.x + r * 0.35, c.y + r * 0.55, c.z + r * 1.4);
  V.controls.target.copy(c); V.controls.update();
}

async function loadCams(base) {
  try {
    const res = await fetch(u(base + "cameras.json"));
    if (!res.ok) return;
    const cams = await res.json();
    const pts = cams.centers.map((p) => new THREE.Vector3(p[0], p[1], p[2]));
    const g = new THREE.BufferGeometry().setFromPoints(pts);
    V.cams = new THREE.Line(g, new THREE.LineBasicMaterial({ color: 0xffb020 }));
    V.cams.visible = $("showCams").checked;
    V.scene.add(V.cams);
  } catch (e) { /* optional */ }
}

function openViewer(base, title, kind) {
  initViewer();
  $("viewer").classList.remove("hidden");
  V.resize();
  V.base = base; V.title = title;
  showKind(kind);
}

function showKind(kind) {
  V.kind = kind;
  document.querySelectorAll("#viewerKind button").forEach((b) => b.classList.toggle("on", b.dataset.kind === kind));
  const file = kind === "mesh" ? "mesh_poisson.ply" : "cloud_raw.ply";
  const base = V.base;
  $("viewerTitle").textContent = `${V.title} / ${file}`;
  $("viewerDownload").href = u(base + file);
  $("viewerDownload").setAttribute("download", file);
  $("viewerLoading").classList.remove("hidden");
  $("viewerLoading").textContent = "loading " + file + "…";
  clearObj();
  new THREE.PLYLoader().load(u(base + file), (geom) => {
    $("viewerLoading").classList.add("hidden");
    geom.computeBoundingSphere();
    V.radius = Math.max(geom.boundingSphere.radius, 1e-6);
    V.center.copy(geom.boundingSphere.center);
    const hasColor = !!geom.getAttribute("color");
    if (kind === "mesh") {
      if (!geom.getAttribute("normal")) geom.computeVertexNormals();
      const lit = $("litMesh").checked;
      const mat = lit ? new THREE.MeshStandardMaterial({ vertexColors: hasColor, roughness: 0.9, metalness: 0, side: THREE.DoubleSide })
                      : new THREE.MeshBasicMaterial({ vertexColors: hasColor, side: THREE.DoubleSide });
      mat.wireframe = $("wireMesh").checked;
      V.obj = new THREE.Mesh(geom, mat);
      const n = geom.index ? geom.index.count / 3 : geom.getAttribute("position").count / 3;
      $("viewerInfo").textContent = `${Math.round(n).toLocaleString()} triangles`;
    } else {
      const mat = new THREE.PointsMaterial({ size: parseFloat($("ptSize").value), sizeAttenuation: false, vertexColors: hasColor });
      V.obj = new THREE.Points(geom, mat);
      $("viewerInfo").textContent = `${geom.getAttribute("position").count.toLocaleString()} points`;
    }
    V.scene.add(V.obj);
    resetView();
    loadCams(base);
  }, (xhr) => {
    if (xhr.total) $("viewerLoading").textContent = `loading ${file}… ${(100 * xhr.loaded / xhr.total).toFixed(0)}%`;
  }, (err) => {
    $("viewerLoading").textContent = "failed to load " + file;
    console.error(err);
  });
}

document.querySelectorAll("#viewerKind button").forEach((b) => b.addEventListener("click", () => showKind(b.dataset.kind)));
$("viewerClose").addEventListener("click", () => { $("viewer").classList.add("hidden"); clearObj(); });
$("viewerReset").addEventListener("click", resetView);
$("ptSize").addEventListener("input", () => { if (V.obj && V.obj.isPoints) V.obj.material.size = parseFloat($("ptSize").value); });
$("litMesh").addEventListener("change", () => { if (V.kind === "mesh") showKind("mesh"); });
$("wireMesh").addEventListener("change", () => { if (V.obj && V.obj.material) V.obj.material.wireframe = $("wireMesh").checked; });
$("showCams").addEventListener("change", () => { if (V.cams) V.cams.visible = $("showCams").checked; });
window.addEventListener("keydown", (e) => { if (e.key === "Escape") $("viewerClose").click(); });
