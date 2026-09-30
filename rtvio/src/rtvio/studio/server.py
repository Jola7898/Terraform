"""
RTVIO Studio - browser control for drone and phone capture + VGGT reconstruction.

    python -m rtvio.studio [--port 8080] [--phone-port 5555] [--open]

Drone: in the page's Drone connection card, enter the drone's IP (the one
iDronam's Add Device uses); the Studio pulls its RTSP video and MAVLink
telemetry itself (drone_link.py). Phone: Settings -> Server IP = this PC's
LAN address, port 5555 -> back -> CONNECT. The page at
http://127.0.0.1:8080 shows either live view, starts/stops recordings,
queues finished takes for reconstruction (a drone take always, the moment
it stops), and opens cloud_raw.ply / mesh_poisson.ply in a 3D viewer.

The web UI binds to 127.0.0.1 unless --web-host says otherwise: it can start
the phone's camera and run GPU jobs, so exposing it is an explicit choice,
and one that needs --password (or RTVIO_STUDIO_PASSWORD) unless --no-auth
says otherwise. The phone port has to be reachable from the phone.

REMOTE USE (docs/REMOTE_ACCESS.md): this page can also be hosted on Vercel
(web/config.js points it here) and reach this server through a Cloudflare
Tunnel; --cors-origin names that site, which signs in via /api/login for a
bearer token and polls the open /api/health to show "offline" when this PC
is not reachable. Video files are uploaded in chunks (/api/uploads), and
each job's live 3D viewer is proxied under /viz/ so it needs no extra open
port. The phone's TCP stream and the drone's RTSP + MAVLink are not HTTP;
from outside the LAN those go over Tailscale.
"""
import argparse
import email.utils
import fnmatch
import gzip
import hashlib
import hmac
import http.client
import http.cookies
import base64
import json
import os
import re
import shutil
import socket
import struct
import tempfile
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .drone_link import DroneLink
from .jobs import GpuMonitor, ReconQueue
from .phone_link import PhoneLink

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(PACKAGE_DIR, "web")
RTVIO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(PACKAGE_DIR)))
DEFAULT_DATA_ROOT = os.path.join(RTVIO_ROOT, "data")

# Capture defaults are chosen for "use every frame": 720p rather than 1080p
# because VGGT resizes every frame to 518 px on its long side anyway, so 1080p
# only costs phone encode time and WiFi bandwidth (the two things that cost
# frames) without adding anything the model can see. JPEG 90 because at 518 px
# the model does see blocking artifacts from low qualities.
DEFAULT_SETTINGS = {
    "capture": {"resolution": "720p", "fps": 30, "jpeg_quality": 90,
                "gps": False, "imu": True},
    # Indoor test defaults: no GPS (unreliable indoors), vision-only
    # alignment, every frame used.
    "recon": {"window_frames": "auto", "overlap": 8, "frame_stride": 1,
              "conf_percentile": 50, "poisson_depth": 10, "voxel_factor": 1.0,
              "min_views": 2, "gps_mode": "off", "masking": False, "extras": False,
              "enhance": False},
    "auto_reconstruct": True,
    # Drone (drone_link.py). ip is the drone / companion computer - the
    # address iDronam's "Add Device" uses - and {ip} in video_url is replaced
    # by it. mode is latched per take: "indoor" = vision-only, "outdoor" =
    # record the drone's GPS and georeference. record_long_side 1280 for the
    # same reason capture.resolution is 720p; video_delay_ms: see
    # drone_link's TIMESTAMPS note.
    "drone": {"enabled": True, "ip": "", "mavlink_port": 14550,
              "video_url": "rtsp://{ip}:10000/drone_cam", "mode": "indoor",
              "record_long_side": 1280, "jpeg_quality": 90, "video_delay_ms": 0,
              # Lens calibration (camera_calib.py): checkerboard inner corners,
              # and whether the live view shows the undistorted frame.
              "calib_cols": 9, "calib_rows": 6, "preview_undistort": False,
              "enhance": False},
}

SESSION_ID_RE = re.compile(r"^[\w.-]+$")
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp")
IMAGE_EXTS = (".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".bmp", ".webp", ".tif", ".tiff", ".jp2", ".ppm", ".pgm")
MAX_ZIP_BYTES = 40 * 1024 ** 3      # uncompressed - a zip bomb guard, not a real limit


def unpack_upload_zip(zip_path, dest_dir, label="frames"):
    """A .zip sent to the video upload -> ("video", path) if it holds a video,
    ("images", folder) if it holds image frames, ("session", None) if it is a
    Studio session export (the caller imports it). Members are written under
    names we choose, so nothing in the archive can pick its own path."""
    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and "__MACOSX" not in i.filename
                 and not os.path.basename(i.filename).startswith("._")]
        if sum(i.file_size for i in infos) > MAX_ZIP_BYTES:
            raise ValueError("zip is too large when unpacked")
        if any(os.path.basename(i.filename) == "session_meta.json" for i in infos):
            return "session", None
        videos = [i for i in infos if i.filename.lower().endswith(VIDEO_EXTS)]
        if videos:
            v = max(videos, key=lambda i: i.file_size)
            out = os.path.join(dest_dir, re.sub(r"[^\w.-]", "_", os.path.basename(v.filename)) or "video.mp4")
            with zf.open(v) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            return "video", out
        key = lambda i: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", i.filename.lower())]
        images = sorted((i for i in infos if i.filename.lower().endswith(IMAGE_EXTS)), key=key)
        if len(images) < 2:
            raise ValueError("the zip needs a video file or at least 2 image frames")
        folder = os.path.join(dest_dir, re.sub(r"[^\w.-]", "_", label) or "frames")
        os.makedirs(folder)
        for n, i in enumerate(images):
            with zf.open(i) as src, open(os.path.join(folder, "%06d%s" % (n, os.path.splitext(i.filename)[1].lower())), "wb") as dst:
                shutil.copyfileobj(src, dst)
        return "images", folder
UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_CHUNK_BYTES = 96 * 1024 * 1024      # under Cloudflare's 100 MB request cap
AUTH_COOKIE = "rtvio_auth"
LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>RTVIO Studio - sign in</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0d10;color:#cfd8e3;
font:15px/1.4 -apple-system,Segoe UI,sans-serif}form{display:flex;flex-direction:column;gap:12px;width:min(320px,90vw)}
input,button{font:inherit;padding:10px 12px;border-radius:8px;border:1px solid #2a3340;background:#141920;color:inherit}
button{background:#2f6fed;border-color:#2f6fed;color:#fff;cursor:pointer}.err{color:#ff7b7b;min-height:1.4em}</style>
</head><body><form method="post" action="/login"><h2 style="margin:0">RTVIO Studio</h2>
<input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password">
<button type="submit">Sign in</button><div class="err">__ERROR__</div></form></body></html>"""
PRIORITY_OUTPUTS = ("cloud_raw.ply", "mesh_poisson.ply")
def _compressible(ctype):
    """Text-like responses worth gzipping (not the binary meshes, which barely shrink)."""
    return ctype.startswith("text/") or "json" in ctype or "javascript" in ctype or ctype.startswith("image/svg")


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json",
    ".jpg": "image/jpeg", ".png": "image/png", ".md": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8", ".log": "text/plain; charset=utf-8",
    ".ply": "application/octet-stream", ".obj": "text/plain; charset=utf-8",
    ".glb": "model/gltf-binary", ".las": "application/octet-stream",
}


def lan_addresses():
    """Best-effort list of this PC's LAN IPv4 addresses, for telling the user
    what to type into the phone. The UDP-connect trick finds the address of
    the default route's interface without sending anything."""
    addrs = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


def _merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Studio:
    def __init__(self, data_root, phone_host, phone_port, viz_port=8767, warm_model=True):
        self.data_root = data_root
        self.sessions_root = os.path.join(data_root, "sessions")
        self.video_root = os.path.join(data_root, "video_jobs")
        self.upload_root = os.path.join(data_root, "uploads")
        os.makedirs(self.sessions_root, exist_ok=True)
        os.makedirs(self.video_root, exist_ok=True)
        self.settings_path = os.path.join(data_root, "studio_settings.json")
        self.settings = _merge(DEFAULT_SETTINGS, self._load_settings())
        self.gpu = GpuMonitor().start()
        self.queue = ReconQueue(self.gpu, cwd=RTVIO_ROOT, video_root=self.video_root, viz_port=viz_port,
                               warm=warm_model).start()
        self.phone = PhoneLink(self.sessions_root, phone_host, phone_port,
                               on_session_finalized=self._on_finalized)
        self.phone.start()
        self.drone = DroneLink(self.sessions_root, self.settings["drone"],
                               on_session_finalized=self._on_drone_finalized,
                               camera_path=os.path.join(data_root, "drone_camera.json"),
                               calib_root=os.path.join(data_root, "drone_calib"))
        self.drone.start()
        self.lan = lan_addresses()
        self.phone_port = phone_port

    # ---------------------------------------------------------- settings --

    def _load_settings(self):
        try:
            with open(self.settings_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def update_settings(self, patch):
        self.settings = _merge(self.settings, patch)
        with open(self.settings_path, "w") as f:
            json.dump(self.settings, f, indent=2)
        if "drone" in (patch or {}):
            self.drone.configure(self.settings["drone"])
        return self.settings

    # ---------------------------------------------------------- sessions --

    def _on_finalized(self, session_dir, meta):
        # If "live reconstruct" was checked when this take started, a "live"
        # job has been running against it since - see submit_live() below -
        # and stays busy_with() this session_id through its own finalize and
        # export, well past this callback. Queuing the normal batch job on
        # top of it would just repeat the same GPU work for no benefit.
        if self.settings.get("auto_reconstruct", True) and not self.queue.busy_with(meta["id"]):
            self.queue.submit(session_dir, self.recon_params(session_dir))

    def _on_drone_finalized(self, session_dir, meta):
        # Unlike a phone take (settings.auto_reconstruct), a drone take goes
        # to the GPU the moment it is saved - unless a live job already
        # covered it (see _on_finalized's comment).
        if not self.queue.busy_with(meta["id"]):
            self.queue.submit(session_dir, self.recon_params(session_dir))

    def recon_params(self, session_dir, overrides=None):
        """settings.recon - except that a drone take is reconstructed in the
        mode it was flown in (the drone's own Indoor/Outdoor switch, latched
        into session_meta.json), not the GPS setting phone takes and video
        files use; and outdoor only if the drone actually recorded GPS."""
        params = dict(self.settings["recon"])
        try:
            with open(os.path.join(session_dir, "session_meta.json")) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            meta = {}
        if meta.get("origin") == "drone":
            outdoor = meta.get("drone_mode") == "outdoor" and (meta.get("gps_fixes") or 0) > 0
            params["gps_mode"] = "global" if outdoor else "off"
            params["enhance"] = bool(self.settings["drone"].get("enhance"))
            # A take recorded before the lens was calibrated carries no
            # camera_intrinsics.json of its own: straighten it with today's
            # calibration of the same camera (vggt_reconstruct checks the
            # aspect ratio still matches).
            if (self.drone.camera is not None
                    and not os.path.exists(os.path.join(session_dir, "camera_intrinsics.json"))):
                params["intrinsics"] = self.drone.camera_path
        return _merge(params, overrides or {})

    def drone_live_recon_params(self, session_dir):
        """recon_params()'s drone overrides, computed from the drone's
        current Indoor/Outdoor setting and calibration instead of from
        session_meta.json - a live job starts at record-start time, before
        the take (and that file) exists. Once the take finalizes, its own
        camera_intrinsics.json/GPS track are still what vggt_live --tail
        actually reconstructs with (see run_tail); this only decides what to
        ask for up front."""
        overrides = {"gps_mode": "global" if self.settings["drone"]["mode"] == "outdoor" else "off",
                     "enhance": bool(self.settings["drone"].get("enhance"))}
        if self.drone.camera is not None:
            overrides["intrinsics"] = self.drone.camera_path
        return self.recon_params(session_dir, overrides)

    def session_dir(self, sid):
        if not SESSION_ID_RE.match(sid or ""):
            return None
        d = os.path.join(self.sessions_root, sid)
        return d if os.path.isdir(d) else None

    def unique_session_id(self, preferred):
        """A free directory name under sessions_root: `preferred` if it is a
        valid id and not already taken (so an imported session can keep the
        id it was exported with), otherwise a timestamp-based one like
        phone_link.new_session_id / drone_link.new_drone_session_id."""
        if preferred and SESSION_ID_RE.match(preferred) and not os.path.exists(
                os.path.join(self.sessions_root, preferred)):
            return preferred
        base = time.strftime("%Y%m%d-%H%M%S") + "-imported"
        sid, n = base, 1
        while os.path.exists(os.path.join(self.sessions_root, sid)):
            n += 1
            sid = "%s-%d" % (base, n)
        return sid

    def import_session_zip(self, zip_path, preferred_id=None):
        """Extracts a .zip built by export_session_zip (files at the zip
        root, not nested in a folder) into a new session directory. Returns
        (True, session_id) or (False, error)."""
        sid = self.unique_session_id(preferred_id)
        dest = os.path.join(self.sessions_root, sid)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                dest_real = os.path.realpath(dest)
                for member in zf.namelist():
                    target = os.path.realpath(os.path.join(dest, member))
                    if target != dest_real and not target.startswith(dest_real + os.sep):
                        return False, "zip entry escapes the session directory: %s" % member
                os.makedirs(dest, exist_ok=True)
                zf.extractall(dest)
        except zipfile.BadZipFile:
            shutil.rmtree(dest, ignore_errors=True)
            return False, "not a valid zip file"
        if not os.path.exists(os.path.join(dest, "session_meta.json")):
            shutil.rmtree(dest, ignore_errors=True)
            return False, "zip has no session_meta.json at its root - not a session export"
        return True, sid

    def video_job_dir(self, dirname):
        if not SESSION_ID_RE.match(dirname or ""):
            return None
        d = os.path.join(self.video_root, dirname)
        return d if os.path.isdir(d) else None

    def delete_recon(self, sid, name):
        """Deletes one recon-N output directory straight off disk - not
        job-id based, since queue.jobs is in-memory and empty again after
        every Studio restart while the files (and this delete button)
        obviously need to keep working."""
        d = self.session_dir(sid)
        if d is None:
            return False, "no such session"
        if not re.match(r"^recon-\d+$", name or ""):
            return False, "bad reconstruction name"
        p = os.path.join(d, name)
        if not os.path.isdir(p):
            return False, "no such reconstruction"
        if self.queue.busy_with(sid):
            return False, "a reconstruction is still queued/running for this session"
        shutil.rmtree(p, ignore_errors=True)
        return True, None

    def list_video_jobs(self, limit=50):
        """Video-file jobs, read straight off video_root - like
        list_sessions(), this survives a Studio restart even though
        queue.jobs (in-memory) does not. A currently-tracked job is matched
        in by its out_dir's basename purely to overlay live state/progress
        on top of what's already on disk."""
        out = []
        try:
            names = sorted(os.listdir(self.video_root), reverse=True)
        except OSError:
            names = []
        live = {os.path.basename(j.out_dir): j for j in self.queue.jobs if j.out_dir}
        for name in names[:limit]:
            d = os.path.join(self.video_root, name)
            if not os.path.isdir(d):
                continue
            job = live.get(name)
            prog = None
            try:
                with open(os.path.join(d, "progress.json")) as f:
                    prog = json.load(f)
            except (OSError, ValueError):
                pass
            files = {}
            for fn in PRIORITY_OUTPUTS + ("CHECKPOINT_REPORT.md",):
                p = os.path.join(d, fn)
                if os.path.exists(p):
                    files[fn] = os.path.getsize(p)
            out.append({
                "dir": name,
                "label": job.label if job else re.sub(r"-\d+$", "", name),
                "job_id": job.id if job else None,
                "state": job.state if job else None,
                "viz_url": "/viz/" if job and job.state == "running" and job.viz_port else None,
                "progress": prog,
                "files": files,
            })
        return out

    def delete_video_job(self, dirname):
        if not SESSION_ID_RE.match(dirname or ""):
            return False, "bad name"
        d = os.path.join(self.video_root, dirname)
        if not os.path.isdir(d):
            return False, "no such job"
        live = next((j for j in self.queue.jobs if j.out_dir and os.path.basename(j.out_dir) == dirname), None)
        if live is not None and live.state in ("queued", "running", "cancelling"):
            return False, "cancel it first"
        shutil.rmtree(d, ignore_errors=True)
        return True, None

    def list_sessions(self, limit=50):
        active = self.drone.busy_ids()
        if self.phone.active is not None:
            active.add(self.phone.active.id)
        out = []
        try:
            names = sorted(os.listdir(self.sessions_root), reverse=True)
        except OSError:
            names = []
        for sid in names[:limit]:
            d = os.path.join(self.sessions_root, sid)
            if not os.path.isdir(d):
                continue
            meta = None
            try:
                with open(os.path.join(d, "session_meta.json")) as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                pass
            recons = []
            for name in sorted((n for n in os.listdir(d) if n.startswith("recon-")),
                               key=lambda n: int(n.split("-")[1]) if n.split("-")[1].isdigit() else 0):
                rd = os.path.join(d, name)
                prog = None
                try:
                    with open(os.path.join(rd, "progress.json")) as f:
                        prog = json.load(f)
                except (OSError, ValueError):
                    pass
                files = {}
                for fn in PRIORITY_OUTPUTS + ("CHECKPOINT_REPORT.md", "trajectory_check.png",
                                              "cameras.json", "job.log"):
                    p = os.path.join(rd, fn)
                    if os.path.exists(p):
                        files[fn] = os.path.getsize(p)
                recons.append({"name": name, "progress": prog, "files": files})
            out.append({
                "id": sid,
                "recording": sid in active,
                "meta": meta,
                "thumb": "frames/000000.jpg" if os.path.exists(os.path.join(d, "frames", "000000.jpg")) else None,
                "recons": recons,
                "busy": self.queue.busy_with(sid),
            })
        return out

    def state(self):
        return {
            "time": time.time(),
            "phone": self.phone.snapshot(),
            "drone": self.drone.snapshot(),
            "jobs": self.queue.snapshot(),
            "gpu": self.gpu.snapshot(n=90),
            "settings": self.settings,
            "lan": self.lan,
            "phone_port": self.phone_port,
        }


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_frame(opcode, payload=b""):
    """One unmasked server->client WebSocket frame (RFC 6455)."""
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([n])
    elif n < 1 << 16:
        head += b"~" + struct.pack(">H", n)
    else:
        head += b"" + struct.pack(">Q", n)
    return head + payload


def _read_exact(rfile, n):
    buf = b""
    while len(buf) < n:
        chunk = rfile.read(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf


def _ws_read_message(rfile, wfile_lock, wfile):
    """Next binary/text message from the client (unmasking, reassembling
    fragments, answering pings). Returns None on close."""
    data, opcode0 = b"", None
    while True:
        b0, b1 = _read_exact(rfile, 2)
        fin, op = b0 & 0x80, b0 & 0x0F
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", _read_exact(rfile, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", _read_exact(rfile, 8))[0]
        mask = _read_exact(rfile, 4) if b1 & 0x80 else None
        payload = _read_exact(rfile, n) if n else b""
        if mask:
            payload = bytes(a ^ mask[i & 3] for i, a in enumerate(payload)) if n < 256 else                 (int.from_bytes(payload, "big") ^ int.from_bytes((mask * (n // 4 + 1))[:n], "big")).to_bytes(n, "big")
        if op == 0x8:
            return None
        if op == 0x9:
            with wfile_lock:
                wfile.write(_ws_frame(0xA, payload)); wfile.flush()
            continue
        if op == 0xA:
            continue
        if op in (0x1, 0x2):
            opcode0 = op
        data += payload
        if fin:
            return data


def make_handler(studio, password=None, cors_origins=()):
    # Stateless session token: an HMAC of the password, so a sign-in survives
    # a Studio restart and changing the password signs every browser out.
    token = (hmac.new(password.encode("utf-8"), b"rtvio-studio-session", hashlib.sha256).hexdigest()
             if password else None)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        # --------------------------------------------------------- CORS --
        # The UI may be served from elsewhere (Vercel - see deploy/vercel/)
        # and call this API cross-origin. Only the origins given by
        # --cors-origin may; every response passes through end_headers, so
        # this one hook covers JSON, files, MJPEG and the /viz/ proxy.

        def _cors_origin(self):
            o = self.headers.get("Origin")
            return o if o and any(fnmatch.fnmatchcase(o, pat) for pat in cors_origins) else None

        def end_headers(self):
            o = self._cors_origin()
            if o:
                self.send_header("Access-Control-Allow-Origin", o)
                self.send_header("Vary", "Origin")
            super().end_headers()

        def do_OPTIONS(self):
            if not self._cors_origin():
                return self._send(403, {"error": "origin not allowed"})
            self.send_response(204)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        # --------------------------------------------------------- auth --
        # Three ways to present the token: the cookie /login sets (UI served
        # by this server), an Authorization: Bearer header (UI served from
        # elsewhere - fetch/XHR), or ?token= (what that remote UI puts on
        # <img>/<iframe>/download URLs, which cannot carry a header).

        def _authorized(self):
            if token is None:
                return True
            given = None
            auth = self.headers.get("Authorization") or ""
            if auth.startswith("Bearer "):
                given = auth[len("Bearer "):].strip()
            if given is None:
                given = (urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                         .get("token", [None])[0])
            if given is None:
                try:
                    m = http.cookies.SimpleCookie(self.headers.get("Cookie") or "").get(AUTH_COOKIE)
                except http.cookies.CookieError:
                    m = None
                given = m.value if m is not None else None
            return given is not None and hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8"))

        def _api_login(self):
            """JSON sign-in for a UI served from another origin: returns the
            token for it to keep and send as Authorization: Bearer."""
            given = str(self._json_body().get("password") or "")
            if token is None:
                return self._send(200, {"ok": True, "token": ""})
            if not hmac.compare_digest(given.encode("utf-8"), password.encode("utf-8")):
                time.sleep(1.0)                      # slows password guessing
                return self._send(401, {"ok": False, "error": "wrong password"})
            return self._send(200, {"ok": True, "token": token})

        def _reject(self, path):
            # Any request body is left unread, so this connection cannot be
            # reused for another request.
            self.close_connection = True
            if path.startswith(("/api/", "/files/", "/video_files/", "/viz/", "/ws/")):
                return self._send(401, {"error": "sign in required"}, extra={"Connection": "close"})
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def _login_page(self, code=200, error=""):
            return self._send(code, LOGIN_PAGE.replace("__ERROR__", error), "text/html; charset=utf-8")

        def _login(self):
            n = int(self.headers.get("Content-Length") or 0)
            form = urllib.parse.parse_qs(self.rfile.read(min(n, 4096)).decode("utf-8", "replace")) if n else {}
            given = (form.get("password") or [""])[0]
            if token is None or not hmac.compare_digest(given.encode("utf-8"), password.encode("utf-8")):
                time.sleep(1.0)                      # slows password guessing
                return self._login_page(401, "Wrong password")
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d"
                             % (AUTH_COOKIE, token, 30 * 86400))
            self.send_header("Content-Length", "0")
            self.end_headers()

        # ------------------------------------------------------ helpers --

        def _send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            # JSON is repetitive: gzip it for the phone / tunnel case (the state poll is ~40 KB a second).
            if len(body) > 1400 and _compressible(ctype) and "gzip" in (self.headers.get("Accept-Encoding") or ""):
                body = gzip.compress(body, 4)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                obj = json.loads(self.rfile.read(n).decode("utf-8"))
                return obj if isinstance(obj, dict) else {}
            except ValueError:
                return {}

        def _file(self, path):
            try:
                st = os.stat(path)
            except OSError:
                return self._send(404, {"error": "not found"})
            size = st.st_size
            ctype = CONTENT_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
            # Revalidate instead of never caching: the page, three.js and a finished mesh are
            # re-downloaded only if they changed (a 304 costs nothing on a phone's data), and
            # the vendored libraries may simply be kept for a day.
            last_mod = email.utils.formatdate(st.st_mtime, usegmt=True)
            try:
                since = email.utils.parsedate_to_datetime(self.headers.get("If-Modified-Since") or "")
                unchanged = since is not None and int(st.st_mtime) <= int(since.timestamp())
            except (TypeError, ValueError):
                unchanged = False
            cache = "public, max-age=86400" if os.sep + "vendor" + os.sep in path else "no-cache"
            if unchanged:
                self.send_response(304)
                self.send_header("Last-Modified", last_mod)
                self.send_header("Cache-Control", cache)
                self.end_headers()
                return
            if _compressible(ctype) and size < (8 << 20) and "gzip" in (self.headers.get("Accept-Encoding") or ""):
                with open(path, "rb") as f:
                    body = gzip.compress(f.read(), 5)
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Last-Modified", last_mod)
                self.send_header("Cache-Control", cache)
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Last-Modified", last_mod)
            self.send_header("Cache-Control", cache)
            self.end_headers()
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def _export_session(self, sid):
            """Zips a session directory (files at the zip root, so
            import_session_zip's extractall lands them straight back into a
            fresh session dir with no extra nesting to strip) and streams it
            down as a download. Built into a temp file first rather than
            written straight to self.wfile - zipfile wants a seekable
            stream to place its central directory, which a socket isn't."""
            d = studio.session_dir(sid)
            if d is None:
                return self._send(404, {"error": "no such session"})
            if not os.path.exists(os.path.join(d, "session_meta.json")):
                return self._send(409, {"error": "session is still recording"})
            fd, tmp_path = tempfile.mkstemp(suffix=".zip", dir=studio.data_root)
            os.close(fd)
            try:
                with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for root, _dirs, files in os.walk(d):
                        for fn in files:
                            fp = os.path.join(root, fn)
                            zf.write(fp, arcname=os.path.relpath(fp, d))
                size = os.path.getsize(tmp_path)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", 'attachment; filename="%s.zip"' % sid)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with open(tmp_path, "rb") as f:
                    while True:
                        chunk = f.read(1 << 20)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        def _import_session(self, preferred_id):
            """Body is the raw bytes of a .zip (no multipart - the browser
            just fetch()es the File object directly as the request body),
            streamed straight to a temp file so a large take never has to
            sit fully in memory."""
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return self._send(400, {"ok": False, "error": "empty upload"})
            fd, tmp_path = tempfile.mkstemp(suffix=".zip", dir=studio.data_root)
            try:
                with os.fdopen(fd, "wb") as f:
                    remaining = n
                    while remaining:
                        chunk = self.rfile.read(min(remaining, 1 << 20))
                        if not chunk:
                            break
                        f.write(chunk)
                        remaining -= len(chunk)
                ok, res = studio.import_session_zip(tmp_path, preferred_id)
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            if not ok:
                return self._send(400, {"ok": False, "error": res})
            return self._send(200, {"ok": True, "session": res})

        # ----------------------------------------------- chunked uploads --
        # A file is sent as a series of POST /api/uploads/<id>?offset=N
        # chunks, then handed to /api/upload-video or /api/sessions/import
        # with ?upload=<id>. Chunked because Cloudflare (the tunnel that puts
        # this server behind the Vercel-hosted UI) rejects any request body
        # over 100 MB, and because a dropped mobile connection then costs
        # one chunk, not the whole take: the browser retries from the size
        # the server reports.

        def _incoming(self, uid):
            if not UPLOAD_ID_RE.match(uid or ""):
                return None
            return os.path.join(studio.upload_root, ".incoming", uid + ".part")

        def _upload_start(self):
            d = os.path.join(studio.upload_root, ".incoming")
            os.makedirs(d, exist_ok=True)
            for fn in os.listdir(d):                  # abandoned uploads
                p = os.path.join(d, fn)
                try:
                    if time.time() - os.path.getmtime(p) > 24 * 3600:
                        os.remove(p)
                except OSError:
                    pass
            uid = os.urandom(16).hex()
            open(os.path.join(d, uid + ".part"), "wb").close()
            return self._send(200, {"ok": True, "id": uid})

        def _upload_chunk(self, uid, offset):
            p = self._incoming(uid)
            n = int(self.headers.get("Content-Length") or 0)
            if p is None or not os.path.exists(p):
                self.close_connection = True
                return self._send(404, {"ok": False, "error": "no such upload"}, extra={"Connection": "close"})
            size = os.path.getsize(p)
            if offset != size or n > MAX_CHUNK_BYTES:
                # Out of step (e.g. a retried chunk that did land): the
                # browser resumes from "size".
                self.close_connection = True
                return self._send(409, {"ok": False, "error": "offset mismatch", "size": size},
                                  extra={"Connection": "close"})
            remaining = n
            with open(p, "ab") as f:
                while remaining:
                    chunk = self.rfile.read(min(remaining, 1 << 20))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            if remaining:
                self.close_connection = True
                with open(p, "r+b") as f:             # drop the partial chunk
                    f.truncate(size)
                return self._send(400, {"ok": False, "error": "chunk cut off", "size": size},
                                  extra={"Connection": "close"})
            return self._send(200, {"ok": True, "size": size + n})

        def _upload_video(self, uid, name, q=None):
            """Moves a finished chunked upload to
            <data-root>/uploads/<timestamp>/<name> and queues it exactly
            like /api/reconstruct-video. This is how a clip reaches the GPU
            PC when the browser is not on it - a laptop, or a phone's
            gallery or camera."""
            p = self._incoming(uid)
            if p is None or not os.path.exists(p):
                return self._send(404, {"ok": False, "error": "no such upload"})
            safe = re.sub(r"[^\w.-]", "_", os.path.basename(name or "").strip()) or "video.mp4"
            ext = os.path.splitext(safe)[1].lower()
            if not os.path.getsize(p) or ext not in VIDEO_EXTS + (".zip",):
                return self._send(400, {"ok": False, "error": "expected a video (%s) or a .zip of image frames"
                                        % "/".join(VIDEO_EXTS)})
            base = os.path.join(studio.upload_root, time.strftime("%Y%m%d-%H%M%S"))
            d, k = base, 2
            while os.path.exists(d):
                d, k = "%s-%d" % (base, k), k + 1
            os.makedirs(d)
            dest = os.path.join(d, safe)
            if ext == ".zip":
                try:
                    kind, res = unpack_upload_zip(p, d, os.path.splitext(safe)[0])
                except (zipfile.BadZipFile, ValueError, OSError) as e:
                    shutil.rmtree(d, ignore_errors=True)
                    return self._send(400, {"ok": False, "error": "could not use the zip: %s" % e})
                finally:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                if kind == "session":
                    shutil.rmtree(d, ignore_errors=True)
                    # a Studio session export is not a fresh clip
                    return self._send(400, {"ok": False, "error": "this zip is a Studio session export - "
                                            "use Sessions -> Import session (.zip) for it"})
                dest = res
            else:
                os.replace(p, dest)
            # A video upload is its own thing: its options come from the upload
            # checkboxes only, never from the phone's / drone's saved settings.
            params = dict(studio.settings["recon"], enhance=False)
            q = q or {}
            if q.get("enhance", ["0"])[0] == "1":
                params["enhance"] = True
            if q.get("fast", ["0"])[0] == "1":
                # Fast mode: only frames where the drone has moved (vggt_reconstruct.
                # sample_video_keyframes). Nothing else is touched - measured on a
                # 1033-frame clip: 231 s -> 79 s, and a cleaner cloud, because far
                # fewer windows are chained together.
                params["adaptive_frames"] = True
            if q.get("fisheye", ["0"])[0] == "1":
                try:
                    hf = float(q.get("hfov", ["124"])[0])
                    vf = float(q.get("vfov", ["60"])[0])
                except ValueError:
                    return self._send(400, {"ok": False, "error": "field of view must be a number"})
                if not (0 < hf < 360 and 0 < vf < 360):
                    return self._send(400, {"ok": False, "error": "field of view must be between 0 and 360 degrees"})
                params.update(drone_fisheye=True, fisheye_hfov=hf, fisheye_vfov=vf)
            job = studio.queue.submit_video(dest, params)
            return self._send(200, {"ok": True, "job": job.id})

        def _import_uploaded_session(self, uid, preferred_id):
            p = self._incoming(uid)
            if p is None or not os.path.exists(p):
                return self._send(404, {"ok": False, "error": "no such upload"})
            try:
                ok, res = studio.import_session_zip(p, preferred_id)
            finally:
                try:
                    os.remove(p)
                except OSError:
                    pass
            if not ok:
                return self._send(400, {"ok": False, "error": res})
            return self._send(200, {"ok": True, "session": res})

        def _ws_phone(self):
            """WebSocket -> the phone TCP port. Carries the phone app's byte
            stream unchanged (each binary message is a slice of it), so
            phone_link.py needs no changes and the app can reach the Studio
            through anything that passes HTTPS (Tailscale Funnel, Cloudflare
            Tunnel), where the raw TCP port cannot go."""
            key = self.headers.get("Sec-WebSocket-Key")
            if (self.headers.get("Upgrade") or "").lower() != "websocket" or not key:
                return self._send(400, {"error": "websocket upgrade required"})
            try:
                tcp = socket.create_connection(("127.0.0.1", studio.phone_port), timeout=5)
            except OSError:
                return self._send(503, {"error": "phone receiver not running"})
            accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
            self.close_connection = True
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.wfile.flush()
            tcp.settimeout(None)
            lock = threading.Lock()

            def tcp_to_ws():
                try:
                    while True:
                        chunk = tcp.recv(1 << 16)
                        if not chunk:
                            break
                        with lock:
                            self.wfile.write(_ws_frame(0x2, chunk)); self.wfile.flush()
                except (OSError, ValueError):
                    pass
                try:
                    with lock:
                        self.wfile.write(_ws_frame(0x8, struct.pack(">H", 1000))); self.wfile.flush()
                except (OSError, ValueError):
                    pass
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            threading.Thread(target=tcp_to_ws, daemon=True, name="ws-phone-down").start()
            self.connection.settimeout(None)
            try:
                while True:
                    msg = _ws_read_message(self.rfile, lock, self.wfile)
                    if msg is None:
                        break
                    tcp.sendall(msg)
            except (EOFError, OSError):
                pass
            finally:
                try:
                    tcp.close()
                except OSError:
                    pass

        def _proxy_viz(self, rest):
            """Relays /viz/<rest> to the running job's recon_viz server on
            127.0.0.1 (jobs.py gives every job the same port), streaming the
            body so its Server-Sent-Events feed passes through live. Keeps
            the viewer behind this server's sign-in and on its one port,
            instead of needing another port opened to remote browsers."""
            port = studio.queue.viz_port
            if not port:
                return self._send(404, {"error": "live viewer disabled"})
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                conn.connect()
                # Kept here: for a response read until close (SSE),
                # http.client hands the socket to the response and sets
                # conn.sock to None.
                sock = conn.sock
                conn.request("GET", "/" + rest)
                resp = conn.getresponse()
            except OSError:
                return self._send(502, {"error": "no live viewer running"})
            try:
                self.send_response(resp.status)
                for k in ("Content-Type", "Content-Length", "Cache-Control"):
                    v = resp.getheader(k)
                    if v:
                        self.send_header(k, v)
                if resp.getheader("Content-Length") is None:
                    # SSE: no length, runs until one side closes.
                    self.send_header("Connection", "close")
                    self.close_connection = True
                    sock.settimeout(None)            # windows can be minutes apart
                self.end_headers()
                while True:
                    chunk = resp.read1(1 << 16)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (ConnectionError, OSError):
                self.close_connection = True
            finally:
                conn.close()

        @staticmethod
        def _inside(root, rel):
            p = os.path.realpath(os.path.join(root, rel))
            r = os.path.realpath(root)
            return p if p == r or p.startswith(r + os.sep) else None

        # --------------------------------------------------------- GET --

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            path = urllib.parse.unquote(url.path)
            if path == "/login":
                return self._login_page()
            if path == "/api/health":
                # Unauthenticated on purpose: the remote UI's "is the
                # processing server on?" check, before anyone has signed in.
                return self._send(200, {"ok": True, "service": "rtvio-studio",
                                        "auth_required": token is not None,
                                        "authed": self._authorized()})
            if not self._authorized():
                return self._reject(path)
            if path == "/ws/phone":
                return self._ws_phone()
            if path == "/viz":
                self.send_response(301)
                self.send_header("Location", "/viz/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path.startswith("/viz/"):
                return self._proxy_viz(url.path[len("/viz/"):])
            if path in ("/", "/index.html"):
                return self._file(os.path.join(WEB_DIR, "index.html"))
            if path.startswith("/static/"):
                p = self._inside(WEB_DIR, path[len("/static/"):])
                return self._file(p) if p else self._send(404, {"error": "not found"})
            if path == "/api/state":
                return self._send(200, studio.state())
            if path == "/api/sessions":
                return self._send(200, studio.list_sessions())
            if path == "/api/video-jobs":
                return self._send(200, studio.list_video_jobs())
            if path == "/api/preview.jpg":
                _seq, jpeg = studio.phone.latest_seq, studio.phone.latest_jpeg
                if jpeg is None:
                    return self._send(404, {"error": "no frame yet"})
                return self._send(200, jpeg, "image/jpeg")
            if path == "/api/preview.mjpg":
                return self._mjpeg(studio.phone)
            if path == "/api/drone/preview.jpg":
                jpeg = studio.drone.latest_jpeg
                if jpeg is None:
                    return self._send(404, {"error": "no frame yet"})
                return self._send(200, jpeg, "image/jpeg")
            if path == "/api/drone/preview.mjpg":
                return self._mjpeg(studio.drone)
            m = re.match(r"^/api/sessions/([\w.-]+)/export$", path)
            if m:
                return self._export_session(m.group(1))
            m = re.match(r"^/api/jobs/(\d+)/log$", path)
            if m:
                job = next((j for j in studio.queue.jobs if j.id == int(m.group(1))), None)
                if job is None:
                    return self._send(404, {"error": "no such job"})
                return self._send(200, {"lines": job.log_tail(200)})
            m = re.match(r"^/files/([\w.-]+)/(.+)$", path)
            if m:
                d = studio.session_dir(m.group(1))
                p = self._inside(d, m.group(2)) if d else None
                return self._file(p) if p else self._send(404, {"error": "not found"})
            m = re.match(r"^/video_files/([\w.-]+)/(.+)$", path)
            if m:
                d = studio.video_job_dir(m.group(1))
                p = self._inside(d, m.group(2)) if d else None
                return self._file(p) if p else self._send(404, {"error": "not found"})
            return self._send(404, {"error": "not found"})

        def _mjpeg(self, source):
            """multipart/x-mixed-replace: an <img> tag renders it as live
            video with no JavaScript. Capped at ~12 fps - it is a viewfinder,
            and localhost bandwidth is not the concern, the browser's JPEG
            decode is. source: the PhoneLink or DroneLink (wait_preview)."""
            boundary = "rtvioframe"
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=%s" % boundary)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            seq = -1
            last = 0.0
            try:
                while True:
                    seq, jpeg = source.wait_preview(seq, timeout=2.0)
                    if jpeg is None:
                        continue
                    wait = 1.0 / 12 - (time.monotonic() - last)
                    if wait > 0:
                        time.sleep(wait)
                    last = time.monotonic()
                    self.wfile.write(("--%s\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n"
                                      % (boundary, len(jpeg))).encode("ascii"))
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
            except (ConnectionError, OSError):
                return

        # -------------------------------------------------------- POST --

        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            path = url.path
            if path == "/login":
                return self._login()
            if path == "/api/login":
                return self._api_login()
            if not self._authorized():
                return self._reject(path)
            q = urllib.parse.parse_qs(url.query)
            if path == "/api/uploads":
                return self._upload_start()
            m = re.match(r"^/api/uploads/([0-9a-f]{32})$", path)
            if m:
                try:
                    offset = int(q.get("offset", ["0"])[0])
                except ValueError:
                    offset = -1
                return self._upload_chunk(m.group(1), offset)
            if path == "/api/upload-video":
                return self._upload_video(q.get("upload", [None])[0], q.get("name", [None])[0], q)
            if path == "/api/sessions/import":
                preferred = q.get("id", [None])[0]
                if q.get("upload"):
                    return self._import_uploaded_session(q["upload"][0], preferred)
                # Body is raw zip bytes, not JSON - must not go through
                # _json_body() below, which would consume the whole
                # request off the socket trying (and failing) to decode it.
                return self._import_session(preferred)
            body = self._json_body()
            if path == "/api/record/start":
                params = _merge(studio.settings["capture"], body.get("capture") or {})
                ok, res = studio.phone.start_recording(params)
                if ok and body.get("live"):
                    d = studio.session_dir(res)
                    studio.queue.submit_live(d, studio.recon_params(d))
                return self._send(200 if ok else 409, {"ok": ok, "session": res} if ok else {"ok": False, "error": res})
            if path == "/api/record/stop":
                ok, res = studio.phone.stop_recording()
                return self._send(200 if ok else 409, {"ok": ok, "session": res} if ok else {"ok": False, "error": res})
            if path == "/api/drone/record/start":
                ok, res = studio.drone.start_recording()
                if ok and body.get("live"):
                    d = studio.session_dir(res)
                    studio.queue.submit_live(d, studio.drone_live_recon_params(d))
                return self._send(200 if ok else 409, {"ok": ok, "session": res} if ok else {"ok": False, "error": res})
            if path == "/api/drone/record/stop":
                ok, res = studio.drone.stop_recording()
                return self._send(200 if ok else 409, {"ok": ok, "session": res} if ok else {"ok": False, "error": res})
            if path == "/api/drone/calib/start":
                d = studio.settings["drone"]
                ok, err = studio.drone.start_calibration(int(body.get("cols") or d.get("calib_cols") or 9),
                                                         int(body.get("rows") or d.get("calib_rows") or 6))
                return self._send(200 if ok else 409, {"ok": ok, "error": err})
            m = re.match(r"^/api/drone/calib/(stop|solve|discard)$", path)
            if m:
                fn = {"stop": studio.drone.stop_calibration, "solve": studio.drone.solve_calibration,
                      "discard": studio.drone.discard_calibration}[m.group(1)]
                ok, res = fn()
                return self._send(200 if ok else 409, {"ok": ok, "result": res} if ok else {"ok": False, "error": res})
            if path == "/api/drone/camera/remove":
                ok, _res = studio.drone.remove_camera_profile()
                return self._send(200, {"ok": ok})
            if path == "/api/phone/ping":
                studio.phone.ping()
                return self._send(200, {"ok": True})
            if path == "/api/settings":
                return self._send(200, studio.update_settings(body))
            m = re.match(r"^/api/sessions/([\w.-]+)/reconstruct$", path)
            if m:
                d = studio.session_dir(m.group(1))
                if d is None:
                    return self._send(404, {"ok": False, "error": "no such session"})
                if not os.path.exists(os.path.join(d, "frame_timestamps.json")):
                    return self._send(409, {"ok": False, "error": "session is still recording"})
                job = studio.queue.submit(d, studio.recon_params(d, body.get("recon")))
                return self._send(200, {"ok": True, "job": job.id})
            if path == "/api/reconstruct-video":
                # A path on the Studio PC (a browser elsewhere uses
                # /api/upload-video instead). Behind the same sign-in as the
                # phone-capture and subprocess control the rest of this API
                # already has, so a path here is no more trusted than those.
                # Windows Explorer's "Copy as path" wraps the path in quotes,
                # so strip them rather than looking for a file whose name
                # starts with a double quote.
                raw = (body.get("path") or "").strip().strip('"').strip("'").strip()
                p = os.path.abspath(raw) if raw else ""
                if not p or not (os.path.isfile(p) or os.path.isdir(p)):
                    return self._send(404, {"ok": False, "error": "no such file or folder: %s" % (p or raw)})
                job = studio.queue.submit_video(p, _merge(dict(studio.settings["recon"], enhance=False),
                                                          body.get("recon") or {}))
                return self._send(200, {"ok": True, "job": job.id})
            m = re.match(r"^/api/jobs/(\d+)/cancel$", path)
            if m:
                return self._send(200, {"ok": studio.queue.cancel(int(m.group(1)))})
            m = re.match(r"^/api/jobs/(\d+)/delete$", path)
            if m:
                ok, err = studio.queue.delete(int(m.group(1)))
                return self._send(200 if ok else 409, {"ok": ok, "error": err})
            m = re.match(r"^/api/sessions/([\w.-]+)/recons/([\w.-]+)/delete$", path)
            if m:
                ok, err = studio.delete_recon(m.group(1), m.group(2))
                return self._send(200 if ok else 409, {"ok": ok, "error": err})
            m = re.match(r"^/api/video-jobs/([\w.-]+)/delete$", path)
            if m:
                ok, err = studio.delete_video_job(m.group(1))
                return self._send(200 if ok else 409, {"ok": ok, "error": err})
            return self._send(404, {"error": "not found"})

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--web-host", default="127.0.0.1",
                    help="interface for the web UI (default: this PC only; 0.0.0.0 serves it "
                         "on every interface - LAN, Tailscale - and then needs a password)")
    ap.add_argument("--password", default=os.environ.get("RTVIO_STUDIO_PASSWORD") or None,
                    help="sign-in password for the web UI (default: $RTVIO_STUDIO_PASSWORD, "
                         "preferred - a command line is visible to other processes)")
    ap.add_argument("--no-auth", action="store_true",
                    help="serve a non-localhost --web-host without a password (trusted network only)")
    ap.add_argument("--port", type=int, default=8080, help="web UI port")
    ap.add_argument("--phone-host", default="0.0.0.0")
    ap.add_argument("--phone-port", type=int, default=5555, help="port the app connects to")
    ap.add_argument("--data-root", default=DEFAULT_DATA_ROOT,
                    help="sessions are written to <data-root>/sessions/<id>/, video-file jobs to "
                         "<data-root>/video_jobs/<name>-<n>/")
    ap.add_argument("--no-warm-model", action="store_true",
                    help="do not keep VGGT loaded between reconstructions (one process per job, model "
                         "reloaded each time, its VRAM freed in between)")
    ap.add_argument("--recon-viz-port", type=int, default=8767,
                    help="port for each reconstruction's live viewer (--live-viz) - always the same "
                         "port since jobs.py runs one reconstruction at a time")
    ap.add_argument("--cors-origin", action="append",
                    default=[o.strip() for o in (os.environ.get("RTVIO_STUDIO_CORS_ORIGINS") or "").split(",")
                             if o.strip()],
                    help="origin of a web UI hosted elsewhere that may call this API, e.g. "
                         "https://rtvio.vercel.app (repeatable; fnmatch wildcards allowed; default: "
                         "$RTVIO_STUDIO_CORS_ORIGINS, comma-separated). Requires a password.")
    ap.add_argument("--open", action="store_true", help="open the page in a browser")
    args = ap.parse_args()
    if args.web_host not in ("127.0.0.1", "localhost", "::1") and not args.password and not args.no_auth:
        ap.error("--web-host %s exposes the control page beyond this PC: set a password "
                 "(RTVIO_STUDIO_PASSWORD or --password), or pass --no-auth" % args.web_host)
    # A tunnel (cloudflared) reaches a 127.0.0.1 server too, so --web-host
    # alone cannot tell the server is public; a remote UI origin can.
    if args.cors_origin and not args.password:
        ap.error("--cors-origin means this API is used from the internet: set a password "
                 "(RTVIO_STUDIO_PASSWORD or --password)")

    studio = Studio(args.data_root, args.phone_host, args.phone_port, viz_port=args.recon_viz_port,
                    warm_model=not args.no_warm_model)
    httpd = ThreadingHTTPServer((args.web_host, args.port),
                                make_handler(studio, args.password, tuple(args.cors_origin)))
    httpd.daemon_threads = True
    url = "http://%s:%d" % ("127.0.0.1" if args.web_host in ("0.0.0.0", "") else args.web_host, args.port)
    print("RTVIO Studio: %s%s" % (url, "  (password sign-in)" if args.password else ""))
    for o in args.cors_origin:
        print("  remote UI allowed from: %s" % o)
    if args.web_host in ("0.0.0.0", ""):
        for a in studio.lan:
            print("  remote: http://%s:%d%s" % (a, args.port, "  (Tailscale)" if a.startswith("100.") else ""))
    print("phone: set Server IP to one of %s, port %d" % (", ".join(studio.lan) or "<this PC's LAN IP>",
                                                          args.phone_port))
    drone_ip = studio.settings["drone"].get("ip")
    print("drone: %s" % ("%s (MAVLink :%s + video)" % (drone_ip, studio.settings["drone"].get("mavlink_port"))
                         if drone_ip else "set its IP in the page's Drone connection card"))
    print("sessions: %s" % studio.sessions_root, flush=True)
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
