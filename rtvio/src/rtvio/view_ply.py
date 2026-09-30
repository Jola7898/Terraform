"""
Minimal coloured point viewer for any .ply (cloud_raw.ply, mesh_poisson.ply, ...).

    python -m rtvio.view_ply path/to/cloud_raw.ply [--max-points 3000000] [--port 8081] [--open]

The browser never sees the .ply itself: a VGGT cloud is ~20 M points / 600 MB, which three.js's PLYLoader
would parse whole in the tab. Instead the file is memory-mapped here, randomly subsampled to --max-points,
re-centred (float32 would jitter at ENU/UTM magnitudes) and served as packed float32 xyz + uint8 rgb.
Points without red/green/blue are coloured by height. Mesh faces are ignored - vertices are drawn as points.
three.js comes from studio/web/vendor, so it works offline.
"""
import argparse
import http.server
import json
import os
import threading
import webbrowser

import numpy as np

VENDOR = os.path.join(os.path.dirname(__file__), "studio", "web", "vendor")
PLY_TYPES = {"char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1", "short": "i2", "int16": "i2",
             "ushort": "u2", "uint16": "u2", "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
             "float": "f4", "float32": "f4", "double": "f8", "float64": "f8"}


def read_header(path):
    """(format, vertex count, [(name, numpy type)], byte offset of the vertex data, header line count)."""
    with open(path, "rb") as f:
        if f.readline().strip() != b"ply":
            raise ValueError("%s is not a PLY file" % path)
        fmt, elements, lines = None, [], 1
        while True:
            line = f.readline()
            lines += 1
            if not line:
                raise ValueError("PLY header has no end_header")
            tok = line.decode("ascii", "replace").split()
            if not tok or tok[0] in ("comment", "obj_info"):
                continue
            if tok[0] == "format":
                fmt = tok[1]
            elif tok[0] == "element":
                elements.append((tok[1], int(tok[2]), []))
            elif tok[0] == "property":
                elements[-1][2].append((tok[-1], None if tok[1] == "list" else PLY_TYPES[tok[1]]))
            elif tok[0] == "end_header":
                offset = f.tell()
                break
    if not elements or elements[0][0] != "vertex":
        raise ValueError("expected 'vertex' as the first PLY element")
    _, n, props = elements[0]
    if any(t is None for _, t in props):
        raise ValueError("list properties on vertices are not supported")
    return fmt, n, props, offset, lines


def load_points(path, max_points=3_000_000, seed=0):
    """(xyz float32 centred, rgb uint8, centre float64, total vertex count, had colour)."""
    fmt, n, props, offset, header_lines = read_header(path)
    names = [name for name, _ in props]
    if fmt == "ascii":
        data = np.loadtxt(path, skiprows=header_lines, max_rows=n, ndmin=2)
        v = {name: data[:, i] for i, name in enumerate(names)}
    else:
        order = "<" if fmt == "binary_little_endian" else ">"
        v = np.memmap(path, dtype=np.dtype([(name, order + t) for name, t in props]), mode="r",
                      offset=offset, shape=(n,))
    keep = n if max_points <= 0 else min(n, max_points)
    idx = np.sort(np.random.default_rng(seed).choice(n, keep, replace=False)) if keep < n else np.arange(n)
    xyz = np.stack([np.asarray(v[c])[idx] for c in ("x", "y", "z")], 1).astype(np.float64)
    centre = xyz.mean(0) if len(xyz) else np.zeros(3)
    has_rgb = {"red", "green", "blue"} <= set(names)
    if has_rgb:
        rgb = np.stack([np.asarray(v[c])[idx] for c in ("red", "green", "blue")], 1).astype(np.float64)
        if rgb.max(initial=0) <= 1.0:                       # float colours in [0, 1]
            rgb *= 255
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    else:
        rgb = _height_colours(xyz[:, 2])
    return (xyz - centre).astype(np.float32), rgb, centre, n, has_rgb


def _height_colours(z):
    if not len(z):
        return np.zeros((0, 3), np.uint8)
    lo, hi = np.percentile(z, [2, 98])
    u = np.clip((z - lo) / max(hi - lo, 1e-9), 0, 1)[:, None]
    blue, yellow = np.array([40, 90, 200]), np.array([250, 220, 60])
    return (blue * (1 - u) + yellow * u).astype(np.uint8)


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>PLY viewer</title>
<style>
  html,body{margin:0;height:100%;background:#0b0d10;overflow:hidden;font:12px/1.5 system-ui,sans-serif;color:#cfd8e3}
  #hud{position:fixed;top:10px;left:12px;padding:8px 12px;background:rgba(10,12,16,.75);border:1px solid #2a3038;border-radius:8px}
  #hud b{color:#7fd3ff} button{font:inherit}
</style></head><body>
<div id="hud"><b>__NAME__</b><br><span id="info">loading...</span><br>
  point size <input id="size" type="range" min="0.5" max="6" step="0.5" value="1.5">
  <button id="reset">reset view</button></div>
<script src="/vendor/three.min.js"></script>
<script src="/vendor/OrbitControls.js"></script>
<script>
const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.01, 1e7);
camera.up.set(0, 0, 1);                                   // RTVIO clouds are z-up (ENU)
const renderer = new THREE.WebGLRenderer({antialias: false});
renderer.setPixelRatio(devicePixelRatio); renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);
const controls = new THREE.OrbitControls(camera, renderer.domElement);
let radius = 1, pts;
function resetView() {
  camera.position.set(radius, -radius, radius); camera.near = radius / 1e4; camera.far = radius * 100;
  camera.updateProjectionMatrix(); controls.target.set(0, 0, 0); controls.update();
}
Promise.all([fetch('/meta').then(r => r.json()), fetch('/points').then(r => r.arrayBuffer())]).then(([m, buf]) => {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(buf, 0, m.shown * 3), 3));
  g.setAttribute('color', new THREE.BufferAttribute(new Uint8Array(buf, m.shown * 12, m.shown * 3), 3, true));
  g.computeBoundingSphere(); radius = Math.max(g.boundingSphere.radius, 1e-6);
  pts = new THREE.Points(g, new THREE.PointsMaterial({size: 1.5, sizeAttenuation: false, vertexColors: true}));
  scene.add(pts); resetView();
  document.getElementById('info').textContent = m.shown.toLocaleString() + ' of ' + m.total.toLocaleString() +
    ' points' + (m.has_rgb ? '' : ' (no colour - by height)') + ', centre ' + m.centre.map(c => c.toFixed(2)).join(', ');
}).catch(e => { document.getElementById('info').textContent = 'failed: ' + e; });
document.getElementById('size').oninput = e => { if (pts) pts.material.size = +e.target.value; };
document.getElementById('reset').onclick = resetView;
addEventListener('resize', () => { camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
                                   renderer.setSize(innerWidth, innerHeight); });
(function loop() { requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
</script></body></html>
"""


def _make_handler(name, meta, payload):
    page = PAGE.replace("__NAME__", name).encode("utf-8")
    meta_b = json.dumps(meta).encode("utf-8")
    vendor = {"/vendor/three.min.js", "/vendor/OrbitControls.js"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(page, "text/html; charset=utf-8")
            elif self.path == "/meta":
                self._send(meta_b, "application/json")
            elif self.path == "/points":
                self._send(payload, "application/octet-stream")
            elif self.path in vendor:
                with open(os.path.join(VENDOR, os.path.basename(self.path)), "rb") as f:
                    self._send(f.read(), "text/javascript")
            else:
                self.send_error(404)

        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve(path, port=8081, max_points=3_000_000, open_browser=False):
    xyz, rgb, centre, total, has_rgb = load_points(path, max_points)
    meta = {"shown": len(xyz), "total": total, "has_rgb": has_rgb, "centre": centre.tolist()}
    print("%s: showing %d of %d points%s" % (path, len(xyz), total, "" if has_rgb else " (no colour, by height)"))
    handler = _make_handler(os.path.basename(path), meta, xyz.tobytes() + rgb.tobytes())
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = "http://127.0.0.1:%d" % port
    print("serving at %s (Ctrl+C to stop)" % url)
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ply", help="a .ply file (point cloud or mesh)")
    ap.add_argument("--max-points", type=int, default=3_000_000, help="random subsample cap; 0 = all points")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--open", action="store_true", help="open a browser tab automatically")
    args = ap.parse_args()
    if not os.path.isfile(args.ply):
        ap.error("no such file: %s" % args.ply)
    serve(args.ply, port=args.port, max_points=args.max_points, open_browser=args.open)


if __name__ == "__main__":
    main()
