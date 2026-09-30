"""
Live viewer for a VGGT reconstruction - the batch path (vggt_reconstruct.py,
--video or --from-recording) and the network-live path (vggt_live.py) both
push into this the same way, so one page works for either.

    python -m rtvio.vggt_reconstruct --video clip.mp4 --out <dir> --live-viz --open
    python -m rtvio.vggt_live --live-viz --open
    # then open http://localhost:8766 in a browser (or let --open do it)

Shows three things together, updating from the same per-window loop rather
than in separate phases:
  - the frame currently being consumed (the batch path's FrameLoader decode,
    or vggt_live's actual incoming network JPEG)
  - the point cloud growing window by window
  - a live stats panel: frames/windows done, fps, VRAM, the confidence
    gate's kept-pixel fraction, each window's seam scale/residual, and each
    window's motion-blur score (ingest.sharpness_score)

When the run finishes, push_report() sends the same numbers
CHECKPOINT_REPORT.md is built from; the page then fetches the finished
mesh/cloud and switches from "growing preview" to an explorable view with
Wireframe / Auto-rotate / Reset-view controls, matching the summary a
finished run's CHECKPOINT_REPORT.md already contains.

THIS IS A PREVIEW CHANNEL, NOT A DATA PATH, same contract as viz_server.py:
pushes are best-effort and drop rather than block (see _Client), and a
slow/absent browser changes nothing about the reconstruction itself - every
output file is still written at full resolution regardless of whether a
viewer was ever attached.
"""
import base64
import http.server
import json
import os
import queue
import socketserver
import threading

import numpy as np

MAX_POINTS_PER_PUSH = 4000

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>RTVIO reconstruction</title>
<style>
  html,body{margin:0;height:100%;background:#0b0d10;overflow:hidden;font:12px/1.4 -apple-system,Segoe UI,sans-serif;color:#cfd8e3}
  canvas{display:block}
  #top{position:fixed;top:0;left:0;right:0;padding:10px 14px;display:flex;align-items:center;gap:14px;
       background:linear-gradient(rgba(10,12,16,.85),rgba(10,12,16,0));z-index:3;pointer-events:none}
  #top>*{pointer-events:auto}
  #kicker{color:#7fd3ff;letter-spacing:.08em;font-size:10px;text-transform:uppercase}
  #title{font-size:15px;font-weight:600}
  #sub{color:#7d8794;font-size:11px;margin-left:auto}
  .btn{background:#171b21;border:1px solid #2a3038;color:#cfd8e3;border-radius:6px;padding:6px 10px;
       font-size:10px;letter-spacing:.05em;text-transform:uppercase;cursor:pointer}
  .btn:hover{border-color:#3d4553}
  .rot{font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:#7d8794;display:flex;align-items:center;gap:4px}
  .rot input{width:90px}
  .btn.on{background:#7fa84a;border-color:#7fa84a;color:#0b0d10}
  #cam{position:fixed;top:52px;right:12px;width:260px;border:1px solid #2a3038;border-radius:8px;
       background:#000;z-index:2;box-shadow:0 4px 18px rgba(0,0,0,.5)}
  #banner{position:fixed;left:12px;right:12px;bottom:132px;padding:8px 12px;border-radius:8px;
          background:rgba(224,90,78,.12);border:1px solid rgba(224,90,78,.4);color:#ff8a65;
          font-size:11px;z-index:2;display:none}
  #banner.show{display:block}
  #capture{position:fixed;left:12px;right:12px;bottom:0;padding:10px 14px 14px;
           background:rgba(10,12,16,.82);border-top:1px solid #2a3038;z-index:2;
           font-variant-numeric:tabular-nums}
  #capture h3{margin:0 0 6px;font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#7d8794}
  #grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:6px 18px}
  .stat b{display:block;font-size:15px;color:#e7ecf2}
  .stat span{color:#7d8794;font-size:10px}
  #status{position:fixed;bottom:150px;left:12px;color:#7d8794;font-size:10px;z-index:2}
  #status .live{color:#4ed07a}
  #hint{position:fixed;bottom:150px;right:12px;color:#4a5361;font-size:10px;z-index:2}
  html[data-theme=light],html[data-theme=light] body{background:#ffffff;color:#1b2430}
  html[data-theme=light] #top{background:linear-gradient(rgba(255,255,255,.95),rgba(255,255,255,0))}
  html[data-theme=light] #kicker{color:#3a6fc4}
  html[data-theme=light] #sub,html[data-theme=light] #status,html[data-theme=light] .rot,html[data-theme=light] .stat span,html[data-theme=light] #capture h3{color:#647184}
  html[data-theme=light] #hint{color:#8a96a6}
  html[data-theme=light] .btn{background:#f4f6f9;border-color:#dbe1e9;color:#1b2430}
  html[data-theme=light] .btn.on{background:#3a6fc4;border-color:#3a6fc4;color:#fff}
  html[data-theme=light] #capture{background:rgba(255,255,255,.9);border-top-color:#dbe1e9}
  html[data-theme=light] .stat b{color:#1b2430}
  html[data-theme=light] #cam{border-color:#dbe1e9;box-shadow:0 4px 18px rgba(20,30,50,.15)}
</style>
<script>document.documentElement.dataset.theme="dark";</script>
</head>
<body>
<div id="top">
  <div><div id="kicker">RTVIO &middot; VGGT RECONSTRUCTION</div><div id="title">-</div></div>
  <button class="btn" id="btnWire">Wireframe</button>
  <button class="btn" id="btnRotate">Auto-rotate</button>
  <button class="btn" id="btnReset">Reset view</button>
  <a class="btn" id="btnBack" href="/#/studio" style="display:none;text-decoration:none">&larr; Studio</a>
  <button class="btn" id="btnFlip">Flip upside-down</button>
  <label class="rot">tilt <input type="range" id="rotX" min="0" max="360" step="1" value="180"></label>
  <label class="rot">turn <input type="range" id="rotY" min="0" max="360" step="1" value="0"></label>
  <label class="rot">roll <input type="range" id="rotZ" min="0" max="360" step="1" value="0"></label>
  <button class="btn" id="btnRotReset">Default rotation</button>
  <div id="sub"></div>
</div>
<img id="cam" alt="">
<div id="banner"></div>
<div id="status">connecting...</div>
<div id="hint">drag rotate any direction &middot; Alt-drag or Q/E roll &middot; right-drag pan &middot; scroll zoom</div>
<div id="capture">
  <h3>Capture</h3>
  <div id="grid"></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/PLYLoader.js"></script>
<script>
document.getElementById('title').textContent = __TITLE__;

const scene = new THREE.Scene();
const isLight = () => false;   // the live view is always dark
scene.background = new THREE.Color(isLight() ? 0xffffff : 0x0b0d10);
window.addEventListener('storage', (e) => {
  return;   // theme is fixed (dark); nothing to follow
  document.documentElement.dataset.theme = e.newValue || '';
  scene.background = new THREE.Color(isLight() ? 0xffffff : 0x0b0d10);
});
const camera = new THREE.PerspectiveCamera(60, innerWidth/innerHeight, 0.01, 1e6);
camera.position.set(6, 6, 6);
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(devicePixelRatio);
document.body.appendChild(renderer.domElement);
const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableRotate = false;   // left-drag rotates the model freely instead (attachFreeRotate below)
scene.add(new THREE.AmbientLight(0xffffff, 0.7));
const sun = new THREE.DirectionalLight(0xffffff, 0.7);
sun.position.set(1, 2, 1);
scene.add(sun);
const grid = new THREE.GridHelper(400, 40, 0x2a3038, 0x1a1e24);
scene.add(grid);

// ---- growable point cloud (the live preview, while windows are still coming in)
let capacity = 20000, count = 0;
let posArr = new Float32Array(capacity*3), colArr = new Float32Array(capacity*3);
let geom = new THREE.BufferGeometry();
function rebuildGeom(){
  geom.setAttribute('position', new THREE.BufferAttribute(posArr, 3));
  geom.setAttribute('color', new THREE.BufferAttribute(colArr, 3));
  geom.setDrawRange(0, count);
}
rebuildGeom();
const cloudMat = new THREE.PointsMaterial({size:0.035, vertexColors:true});
let current = new THREE.Points(geom, cloudMat);
// Everything that is the model lives in `pivot`, so the rotation controls
// turn the whole reconstruction about its own centre.
const pivot = new THREE.Group();
scene.add(pivot);
pivot.add(current);
let haveFinal = false;   // true once the finished mesh/cloud has replaced the live preview

function ensureCapacity(extra){
  if (count + extra <= capacity) return;
  while (count + extra > capacity) capacity *= 2;
  const p2 = new Float32Array(capacity*3), c2 = new Float32Array(capacity*3);
  p2.set(posArr); c2.set(colArr);
  posArr = p2; colArr = c2;
  rebuildGeom();
  current.geometry = geom;
}
function addPoints(rows){
  if (haveFinal) return;
  ensureCapacity(rows.length);
  for (const [x,y,z,r,g,b] of rows){
    const i = count*3;
    posArr[i]=x; posArr[i+1]=y; posArr[i+2]=z;
    colArr[i]=r/255; colArr[i+1]=g/255; colArr[i+2]=b/255;
    count++;
  }
  geom.attributes.position.needsUpdate = true;
  geom.attributes.color.needsUpdate = true;
  geom.setDrawRange(0, count);
}

function frameCamera(obj){
  const box = new THREE.Box3().setFromObject(obj);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.x, size.y, size.z, 1e-6);
  camera.position.set(center.x + radius, center.y + radius * 0.8, center.z + radius);
  camera.near = radius / 1000; camera.far = radius * 100;
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
  grid.scale.setScalar(radius / 200 || 1);
}

// ---- toolbar
let wireOn = false, rotateOn = false;
const btnWire = document.getElementById('btnWire'), btnRotate = document.getElementById('btnRotate'),
      btnReset = document.getElementById('btnReset');
btnWire.onclick = () => {
  wireOn = !wireOn;
  btnWire.classList.toggle('on', wireOn);
  if (current.material) current.material.wireframe = wireOn;
};
btnRotate.onclick = () => {
  rotateOn = !rotateOn;
  btnRotate.classList.toggle('on', rotateOn);
  controls.autoRotate = rotateOn;
  controls.autoRotateSpeed = 1.2;
};
btnReset.onclick = () => frameCamera(current);

// ---- video preview
const camImg = document.getElementById('cam');
function showFrame(b64){ camImg.src = 'data:image/jpeg;base64,' + b64; }

// ---- capture stats card
const gridEl = document.getElementById('grid');
function statTile(label, value){
  return '<div class="stat"><b>' + value + '</b><span>' + label + '</span></div>';
}
function renderWindow(d){
  const tiles = [];
  tiles.push(statTile('frames sampled', (d.frames_done||0) + ' / ' + (d.frames_total||'?')));
  tiles.push(statTile('vggt windows', (d.window||0) + ' &middot; ~' + (d.windows_est||'?') + ' est'));
  if (d.gpu) tiles.push(statTile(d.gpu, (d.fps||0).toFixed(1) + ' frames/s'));
  if (d.kept_frac != null) tiles.push(statTile('pixels kept past confidence gate', (100*d.kept_frac).toFixed(1) + '%'));
  if (d.seam) tiles.push(statTile('seam ' + d.seam.method, 's=' + d.seam.scale.toFixed(4) +
    (d.seam.median_rel_residual != null ? (' resid=' + d.seam.median_rel_residual.toFixed(3)) : '')));
  if (d.blur) tiles.push(statTile('motion blur (Laplacian var, this window)',
    'median ' + d.blur.median.toFixed(0) + ' &middot; worst ' + d.blur.worst.toFixed(0) + ' @' + d.blur.worst_frame));
  gridEl.innerHTML = tiles.join('');
}
function renderReport(r){
  const tiles = [];
  tiles.push(statTile('frames', r.n + ' (stride ' + r.frame_stride + ')'));
  tiles.push(statTile('windows', r.windows + ' &times; up to ' + r.window + ' frames'));
  if (r.gpu) tiles.push(statTile(r.gpu, r.vggt_fps.toFixed(1) + ' frames/s &middot; peak ' + r.peak_mb.toFixed(0) + ' MB'));
  tiles.push(statTile('wall time', r.wall_s.toFixed(1) + ' s (' + (r.span_s / Math.max(r.wall_s, 1e-6)).toFixed(2) + 'x real time)'));
  tiles.push(statTile('cloud points', r.n_pts.toLocaleString()));
  if (r.mesh_faces != null) tiles.push(statTile('mesh faces', r.mesh_faces.toLocaleString()));
  if (r.seam_scale_median != null) tiles.push(statTile('seam scale correction',
    'min ' + r.seam_scale_min.toFixed(4) + ' &middot; median ' + r.seam_scale_median.toFixed(4) + ' &middot; max ' + r.seam_scale_max.toFixed(4)));
  if (r.seam_fallbacks) tiles.push(statTile('seam fallbacks (low confident overlap)', r.seam_fallbacks));
  if (r.blur_median != null) tiles.push(statTile('motion blur (Laplacian var, whole run)',
    'median ' + r.blur_median.toFixed(0) + ' &middot; worst ' + r.blur_worst.toFixed(0) + ' @frame ' + r.blur_worst_frame));
  gridEl.innerHTML = tiles.join('');

  const banner = document.getElementById('banner');
  if (r.georef) {
    banner.className = ''; banner.style.display = 'none';
  } else {
    banner.textContent = 'Relative mode — not georeferenced. No GPS anchor for this run: scale/position/orientation are VGGT\\'s own estimate only, good for judging reconstruction quality, not absolute accuracy.';
    banner.className = 'show';
  }
  loadFinal();
}

function loadFinal(){
  const loader = new THREE.PLYLoader();
  loader.load('mesh.ply' + location.search, (g) => {
    g.computeVertexNormals();
    pivot.remove(current);
    const hasColor = !!g.getAttribute('color');
    const mat = g.index ?
      new THREE.MeshStandardMaterial({vertexColors: hasColor, color: hasColor ? 0xffffff : 0x8fa0b3, side: THREE.DoubleSide}) :
      new THREE.PointsMaterial({size:0.035, vertexColors: hasColor});
    current = g.index ? new THREE.Mesh(g, mat) : new THREE.Points(g, mat);
    current.material.wireframe = wireOn;
    centrePivot(current);
    haveFinal = true;
    frameCamera(current);
  }, undefined, () => {
    // no mesh (meshing skipped/failed) - fall back to the finished cloud
    loader.load('cloud.ply' + location.search, (g) => {
      pivot.remove(current);
      current = new THREE.Points(g, new THREE.PointsMaterial({size:0.035, vertexColors: !!g.getAttribute('color')}));
      centrePivot(current);
      haveFinal = true;
      frameCamera(current);
    }, undefined, () => {
      // The server may still be writing the files, or the connection blipped: try again.
      if (++finalTries < 6) setTimeout(loadFinal, 1500);
    });
  });
}
let finalTries = 0;

// Puts `obj`'s bounding-box centre at the pivot's origin so rotating the
// pivot spins the model about its middle rather than about the world origin.
function centrePivot(obj){
  obj.geometry.computeBoundingBox();
  const c = obj.geometry.boundingBox.getCenter(new THREE.Vector3());
  obj.position.copy(c).negate();
  pivot.position.copy(c);
  pivot.add(obj);
}


// Free rotation: dragging with the left button turns the MODEL about the screen's own axes, so it
// can go over the top and all the way round in any direction (OrbitControls stops at the poles).
// Right-drag / Shift-drag still pans and the wheel still zooms - those stay with OrbitControls.
function attachFreeRotate(el, camera, getObj, onChange, onEnd, isActive) {
  const pts = new Map();                       // active pointers: id -> {x, y}
  let mode = "tumble";                         // "tumble" (left-drag) or "roll" (Alt-drag)
  let lastTwist = null;
  const axis = (x, y, z) => new THREE.Vector3(x, y, z).applyQuaternion(camera.quaternion);
  const turn = (a, angle) => getObj().quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(a, angle));
  const k = () => (Math.PI * 2) / Math.max(el.clientHeight, 320);       // about one full turn per view height
  el.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;                          // right / middle: OrbitControls pans, zooms
    if (e.pointerType === "mouse" && (e.shiftKey || e.ctrlKey || e.metaKey)) return;  // Shift / Ctrl + drag: OrbitControls pans
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    try { el.setPointerCapture(e.pointerId); } catch (err) { /* not capturable */ }
    mode = e.altKey ? "roll" : "tumble";
    lastTwist = null;
  });
  el.addEventListener("pointermove", (e) => {
    const p = pts.get(e.pointerId);
    if (!p) return;
    if (e.pointerType === "mouse" && e.buttons === 0) { pts.clear(); onEnd(); return; }   // the release was missed: do not stay stuck
    const dx = e.clientX - p.x, dy = e.clientY - p.y;
    p.x = e.clientX; p.y = e.clientY;
    if (pts.size >= 2) {                                        // two fingers: a twist rolls the model
      const [a, b] = [...pts.values()];
      const ang = Math.atan2(b.y - a.y, b.x - a.x);
      if (lastTwist !== null) {
        let d = ang - lastTwist;
        if (d > Math.PI) d -= 2 * Math.PI; else if (d < -Math.PI) d += 2 * Math.PI;
        turn(axis(0, 0, -1), d);
        onChange();
      }
      lastTwist = ang;
      return;
    }
    if (mode === "roll") turn(axis(0, 0, -1), dx * k());        // Alt-drag: turn about the line of sight
    else { turn(axis(0, 1, 0), dx * k()); turn(axis(1, 0, 0), dy * k()); }   // drag: tumble about the screen's own axes
    onChange();
  });
  const end = (e) => { if (pts.delete(e.pointerId)) { lastTwist = null; if (!pts.size) onEnd(); } };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
  el.addEventListener("lostpointercapture", end);
  // Q / E roll from the keyboard (not while typing in a field)
  window.addEventListener("keydown", (e) => {
    if ((isActive && !isActive()) || /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || "")) return;
    if (e.key === "q" || e.key === "Q") { turn(axis(0, 0, -1), 0.08); onChange(); onEnd(); }
    else if (e.key === "e" || e.key === "E") { turn(axis(0, 0, -1), -0.08); onChange(); onEnd(); }
  });
}
// Opened as its own page under the Studio's /viz/ (not embedded in it): offer a way back.
if (window.top === window && location.pathname.indexOf('/viz') === 0) document.getElementById('btnBack').style.display = '';
attachFreeRotate(renderer.domElement, camera, () => pivot,
  () => {
    const e = new THREE.Euler().setFromQuaternion(pivot.quaternion, 'YXZ');
    const deg = (r) => Math.round(((THREE.MathUtils.radToDeg(r) % 360) + 360) % 360);
    rotEls.x.value = deg(e.x); rotEls.y.value = deg(e.y); rotEls.z.value = deg(e.z);
  },
  () => { try { localStorage.setItem('rtvioRot2', JSON.stringify([rotEls.x.value, rotEls.y.value, rotEls.z.value])); } catch (e) {} });

// ---- rotation: 0-360 deg about each axis, remembered across reconstructions
// (the whole point is fixing a model that comes out upside down, and that
// applies to every run, so the choice is kept in this browser).
const rotEls = {x: document.getElementById('rotX'), y: document.getElementById('rotY'), z: document.getElementById('rotZ')};
function applyRot(){
  pivot.rotation.set(THREE.MathUtils.degToRad(+rotEls.x.value), THREE.MathUtils.degToRad(+rotEls.y.value),
                     THREE.MathUtils.degToRad(+rotEls.z.value), 'YXZ');
  try { localStorage.setItem('rtvioRot2', JSON.stringify([rotEls.x.value, rotEls.y.value, rotEls.z.value])); } catch (e) {}
}
// Default is flipped 180 deg about X: reconstructions come out upside down.
const DEFAULT_ROT = [180, 0, 0];
[rotEls.x.value, rotEls.y.value, rotEls.z.value] = DEFAULT_ROT;
try {
  const saved = JSON.parse(localStorage.getItem('rtvioRot2') || 'null');
  if (saved) { rotEls.x.value = saved[0]; rotEls.y.value = saved[1]; rotEls.z.value = saved[2]; }
} catch (e) {}
Object.values(rotEls).forEach((el) => el.addEventListener('input', applyRot));
document.getElementById('btnFlip').onclick = () => { rotEls.x.value = (+rotEls.x.value + 180) % 360; applyRot(); };
document.getElementById('btnRotReset').onclick = () => { [rotEls.x.value, rotEls.y.value, rotEls.z.value] = DEFAULT_ROT; applyRot(); };
applyRot();

// ---- SSE
const statusEl = document.getElementById('status');
const es = new EventSource('events' + location.search);
es.onopen = () => { statusEl.textContent = 'live'; statusEl.className = 'live'; };
es.onerror = () => { statusEl.textContent = 'reconnecting...'; statusEl.className = ''; };
es.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.t === 'pts') addPoints(msg.d);
  else if (msg.t === 'frame') showFrame(msg.d);
  else if (msg.t === 'window') renderWindow(msg.d);
  else if (msg.t === 'report') { statusEl.textContent = 'done'; renderReport(msg.d); }
};

window.addEventListener('resize', () => {
  camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
(function animate(){
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
})();
</script>
</body></html>
"""


class _Client:
    """One connected browser tab's outbound queue - see viz_server.py's twin
    for the non-blocking/drop-oldest rationale, identical here."""

    def __init__(self):
        self.q = queue.Queue(maxsize=200)

    def send(self, msg):
        try:
            self.q.put_nowait(msg)
        except queue.Full:
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(msg)
            except queue.Full:
                pass


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class ReconViz:
    """Owns the HTTP/SSE server for one reconstruction run. `out_dir` is
    where cloud_raw.ply / mesh_poisson.ply will land once the run finishes -
    a late-joining or reconnecting tab fetches them directly over HTTP
    rather than needing SSE replay."""

    def __init__(self, out_dir, port=8766, title=None):
        self.out_dir = out_dir
        self.port = port
        self.title = title or os.path.basename(os.path.normpath(out_dir)) or "reconstruction"
        self._clients = []
        self._lock = threading.Lock()
        self._origin = None
        self._final_served = threading.Event()   # a browser has fetched the finished mesh/cloud
        self._httpd = None
        self._thread = None

    def start(self):
        viz = self
        page = PAGE.replace("__TITLE__", json.dumps(viz.title)).encode("utf-8")

        class Handler(http.server.BaseHTTPRequestHandler):
            # HTTP/1.0 for the same reason as viz_server.py: /events has no
            # Content-Length and is never chunked.
            protocol_version = "HTTP/1.0"

            def log_message(self, fmt, *args):
                pass

            def _file(self, name, ctype):
                path = os.path.join(viz.out_dir, name)
                if not os.path.exists(path):
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(os.path.getsize(path)))
                self.end_headers()
                with open(path, "rb") as f:
                    self.wfile.write(f.read())
                if name in ("mesh_poisson.ply", "mesh_preview.ply", "cloud_raw.ply"):
                    viz._final_served.set()

            def do_GET(self):
                self.path = self.path.split("?", 1)[0]    # the page forwards its query string
                if self.path in ("/", "/index.html"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
                    return
                if self.path == "/mesh.ply":
                    # the small preview when the run wrote one (see vggt_reconstruct._write_preview_mesh)
                    name = "mesh_preview.ply" if os.path.exists(os.path.join(viz.out_dir, "mesh_preview.ply")) \
                        else "mesh_poisson.ply"
                    return self._file(name, "application/octet-stream")
                if self.path == "/cloud.ply":
                    return self._file("cloud_raw.ply", "application/octet-stream")
                if self.path == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    client = _Client()
                    with viz._lock:
                        viz._clients.append(client)
                    try:
                        while True:
                            msg = client.q.get()
                            if msg is None:                 # viewer is shutting down
                                break
                            self.wfile.write(("data: %s\n\n" % msg).encode("utf-8"))
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                    finally:
                        with viz._lock:
                            if client in viz._clients:
                                viz._clients.remove(client)
                    return
                self.send_response(404)
                self.end_headers()

        self._httpd = _Server(("0.0.0.0", self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def _broadcast(self, msg):
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            c.send(msg)

    def push_points(self, pts, cols):
        """pts (N,3), cols (N,3) 0-255 - numpy arrays or anything array-like.
        Decimates to MAX_POINTS_PER_PUSH and re-centres on the first point
        ever pushed (browsers lose float precision on raw VGGT/UTM-scale
        coordinates)."""
        pts = np.asarray(pts)
        n = len(pts)
        if n == 0:
            return
        cols = np.asarray(cols)
        if self._origin is None:
            self._origin = pts[0].astype(np.float64)
        if n > MAX_POINTS_PER_PUSH:
            idx = np.linspace(0, n - 1, MAX_POINTS_PER_PUSH).astype(np.int64)
            pts, cols = pts[idx], cols[idx]
        rel = pts.astype(np.float64) - self._origin
        rows = np.concatenate([rel, np.clip(cols, 0, 255)], axis=1)
        self._broadcast(json.dumps({"t": "pts", "d": rows.round(3).tolist()}))

    def push_frame(self, jpeg_bytes):
        """jpeg_bytes: an already-encoded JPEG (no re-encoding here)."""
        self._broadcast(json.dumps({"t": "frame", "d": base64.b64encode(jpeg_bytes).decode("ascii")}))

    def push_window(self, stats):
        """Per-window telemetry dict - see vggt_reconstruct.py's call site
        for the exact fields (frames_done/total, window i/N, fps, kept_frac,
        seam, blur)."""
        self._broadcast(json.dumps({"t": "window", "d": stats}))

    def push_report(self, report):
        """Sent once, when the run is completely finished - the same
        numbers CHECKPOINT_REPORT.md/.json are built from."""
        self._broadcast(json.dumps({"t": "report", "d": report}))

    def wait_final(self, timeout=20.0):
        """For a caller that exits right after push_report() (--no-viz-hold):
        keeps the server up until a connected browser has fetched the
        finished mesh, so "Watch live" ends on the result instead of a dead
        page. Returns at once when nobody is watching."""
        with self._lock:
            watching = bool(self._clients)
        if watching:
            self._final_served.wait(timeout)

    def stop(self):
        # In the persistent worker this process outlives the run, so the
        # port must really be released and the open event streams closed -
        # otherwise the next job cannot bind it.
        self._broadcast(None)
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
