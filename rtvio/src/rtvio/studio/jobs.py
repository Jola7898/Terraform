"""
GPU reconstruction queue and GPU-utilisation sampler for rtvio.studio.

Each reconstruction runs as its own `python -m rtvio.vggt_reconstruct`
subprocess, one at a time:
  * a single VGGT job already sizes its windows to fill the card (see
    vggt_reconstruct's auto window size), so two concurrent jobs would only
    fight over VRAM;
  * when the process exits, every byte of VRAM it held is returned - no
    fragmentation or leaked cache carried from one take into the next;
  * a crash, OOM or Cancel kills that job, never the server or the phone link.
Progress comes back through the progress.json file the job rewrites as it
goes (stage, window i/n, ETA), plus its full stdout in job.log.
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque


class GpuMonitor:
    """Samples nvidia-smi once a second. nvidia-smi rather than pynvml: it is
    present wherever the NVIDIA driver is, so this adds no dependency."""

    FIELDS = ("utilization.gpu", "memory.used", "memory.total", "power.draw",
              "temperature.gpu", "name")

    def __init__(self, period_s=1.0, keep=300):
        self.period_s = period_s
        self.samples = deque(maxlen=keep)
        self.name = None
        self.error = None

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="gpu-monitor").start()
        return self

    def _loop(self):
        cmd = ["nvidia-smi", "--query-gpu=" + ",".join(self.FIELDS),
               "--format=csv,noheader,nounits"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        while True:
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=5,
                                     creationflags=flags).stdout.strip().splitlines()[0]
                util, mem, total, power, temp, name = [x.strip() for x in out.split(",", 5)]
                self.name = name
                self.samples.append({
                    "t": time.time(), "util": float(util), "mem_mb": float(mem),
                    "mem_total_mb": float(total),
                    "power_w": float(power) if power not in ("[N/A]", "") else None,
                    "temp_c": float(temp),
                })
                self.error = None
            except Exception as e:                       # noqa: BLE001
                self.error = "nvidia-smi unavailable: %s" % e
                time.sleep(10)
            time.sleep(self.period_s)

    def latest(self):
        return self.samples[-1] if self.samples else None

    def since(self, t0):
        return [s for s in self.samples if s["t"] >= t0]

    def snapshot(self, n=120):
        return {"name": self.name, "error": self.error, "samples": list(self.samples)[-n:]}


def next_recon_dir(session_dir):
    k = 1
    while os.path.exists(os.path.join(session_dir, "recon-%d" % k)):
        k += 1
    return os.path.join(session_dir, "recon-%d" % k)


def next_video_out_dir(video_root, video_path):
    """A fresh <video_root>/<basename>-<n>/ for a one-shot video-file job -
    there is no existing session directory to nest a recon-N under, since
    the video lives wherever the user pointed us, not under video_root."""
    base = re.sub(r"[^\w.-]+", "_", os.path.splitext(os.path.basename(video_path))[0]) or "video"
    k = 1
    while True:
        d = os.path.join(video_root, "%s-%d" % (base, k))
        if not os.path.exists(d):
            return d
        k += 1


def build_command(kind, source, out_dir, params, viz_port=None):
    """Maps the web UI's reconstruction options onto vggt_reconstruct's (or,
    for a live take, vggt_live's --tail) CLI.
    kind: "recording" (source = session dir), "video" (source = video file),
    or "live" (source = session dir, reconstructed as it records - see
    vggt_live.py's STUDIO-INTEGRATED LIVE PATH)."""
    if kind == "live":
        cmd = [sys.executable, "-u", "-m", "rtvio.vggt_live", "--tail", source,
               "--out", out_dir, "--progress", os.path.join(out_dir, "progress.json")]
    else:
        cmd = [sys.executable, "-u", "-m", "rtvio.vggt_reconstruct",
               ("--video" if kind == "video" else "--from-recording"), source,
               "--out", out_dir, "--progress", os.path.join(out_dir, "progress.json")]
    flag_map = {
        "window_frames": "--window-frames", "overlap": "--overlap",
        "frame_stride": "--frame-stride", "conf_percentile": "--conf-percentile",
        "poisson_depth": "--poisson-depth", "gps_mode": "--gps-mode",
        "voxel_factor": "--voxel-factor", "min_views": "--min-views",
        "max_frames": "--max-frames", "intrinsics": "--intrinsics",
    }
    if kind == "live":
        # A live take is reconstructed frame by frame as it arrives, so there
        # is no whole file to stride through or truncate up front - vggt_live
        # --tail has no --frame-stride/--max-frames of its own.
        flag_map = {k: v for k, v in flag_map.items() if k not in ("frame_stride", "max_frames")}
    for key, flag in flag_map.items():
        v = params.get(key)
        if v not in (None, "", "auto"):
            cmd += [flag, str(v)]
    if params.get("enhance"):
        cmd.append("--enhance")                  # every kind: recording, video, live
    if kind == "video" and params.get("adaptive_frames", False):
        cmd.append("--keyframes")
        if params.get("keyframe_shift"):
            cmd += ["--keyframe-shift", str(params["keyframe_shift"])]
    if kind == "video" and params.get("drone_fisheye"):
        fov = [params.get("fisheye_hfov") or 124, params.get("fisheye_vfov") or 60]
        cmd += ["--fisheye-fov"] + [str(float(v)) for v in fov]
    if params.get("window_frames") == "auto":
        cmd += ["--window-frames", "auto"]
    if not params.get("masking", False):
        cmd.append("--no-masking")
    if params.get("extras", False):
        cmd.append("--extras")
    if viz_port:
        # --no-viz-hold: this process's lifecycle is owned by ReconQueue,
        # which needs it to actually exit when done so the next queued job
        # can start - see vggt_reconstruct.py's matching --no-viz-hold help.
        cmd += ["--live-viz", "--viz-port", str(viz_port), "--no-viz-hold"]
    return cmd


class WarmWorker:
    """One long-lived `python -m rtvio.vggt_worker` process with VGGT already
    on the GPU. Jobs run inside it one at a time, so a reconstruction starts
    with the model loaded instead of spending the first stretch of every job
    reading a 5 GB checkpoint. It is (re)started as soon as it is missing,
    so the model is loading again right after a Cancel or a crash rather than
    when the next video arrives.

    Cost: the model's VRAM stays reserved while the Studio runs. Start the
    Studio with --no-warm-model to get the old one-process-per-job behaviour."""

    def __init__(self, cwd=None, env=None):
        self.cwd, self.env = cwd, env
        self.proc = None
        self.ready = threading.Event()
        self._results = queue.Queue()
        self._lock = threading.Lock()

    def ensure(self):
        """The running worker, starting one if there is none."""
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return self.proc
            self.ready.clear()
            results = self._results = queue.Queue()
            logdir = os.path.join(self.cwd or ".", "data")
            os.makedirs(logdir, exist_ok=True)
            errlog = open(os.path.join(logdir, "vggt_worker.log"), "a", encoding="utf-8", errors="replace")
            try:
                proc = subprocess.Popen(
                    [sys.executable, "-u", "-m", "rtvio.vggt_worker"], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=errlog, cwd=self.cwd, env=self.env, text=True,
                    encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            finally:
                errlog.close()
            self.proc = proc
            threading.Thread(target=self._read, args=(proc, results), daemon=True, name="warm-reader").start()
            print("[recon] warm VGGT worker started (pid %d) - loading the model" % proc.pid, flush=True)
            return proc

    def _read(self, proc, results):
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("ready"):
                if proc is self.proc:
                    self.ready.set()
                    print("[recon] warm VGGT worker ready", flush=True)
            else:
                results.put(msg)
        results.put({"dead": True})

    def run(self, job_id, module, argv, log_path, on_proc=None):
        """Runs one job in the worker; its stdout/stderr go to log_path.
        Returns the exit code, or None if the worker could not run it or died
        (killed by Cancel, or crashed - out of memory takes the whole process)."""
        proc = self.ensure()
        results = self._results
        if on_proc:
            on_proc(proc)
        while not self.ready.wait(0.5):
            if proc.poll() is not None:
                return None
        try:
            proc.stdin.write(json.dumps({"id": job_id, "module": module, "argv": argv, "log": log_path}) + "\n")
            proc.stdin.flush()
        except OSError:
            return None
        while True:
            msg = results.get()
            if msg.get("dead"):
                return None
            if msg.get("done") == job_id:
                return msg.get("rc")


class ReconJob:
    _ids = iter(range(1, 1 << 30))

    def __init__(self, kind, source, params):
        """kind: "recording" (source = a rtvio.studio session directory),
        "video" (source = a plain video file path, no phone involved), or
        "live" (source = a session directory, reconstructed as it records)."""
        self.id = next(self._ids)
        self.kind = kind
        self.source = source
        self.session_id = os.path.basename(source.rstrip("\\/")) if kind in ("recording", "live") else None
        self.label = self.session_id or os.path.splitext(os.path.basename(source))[0]
        self.params = dict(params)
        self.out_dir = None
        self.viz_port = None       # set by ReconQueue._run when this job starts
        self.state = "queued"
        self.created = time.time()
        self.started = None
        self.finished = None
        self.returncode = None
        self.proc = None
        self.gpu_util_mean = None
        self.gpu_mem_peak_mb = None
        self.error = None

    def progress(self):
        if not self.out_dir:
            return None
        try:
            with open(os.path.join(self.out_dir, "progress.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def log_tail(self, n=40):
        if not self.out_dir:
            return []
        try:
            with open(os.path.join(self.out_dir, "job.log"), encoding="utf-8", errors="replace") as f:
                return f.read().splitlines()[-n:]
        except OSError:
            return []

    def snapshot(self):
        return {
            "id": self.id, "kind": self.kind, "session": self.session_id, "label": self.label,
            "state": self.state, "params": self.params,
            "out_dir": os.path.basename(self.out_dir) if self.out_dir else None,
            # Relative: server.py proxies /viz/ to the job's viewer, so the
            # link works from any machine that can reach the Studio page.
            "viz_url": "/viz/" if self.state == "running" and self.viz_port else None,
            "created": self.created, "started": self.started, "finished": self.finished,
            "elapsed_s": round((self.finished or time.time()) - self.started, 1) if self.started else None,
            "returncode": self.returncode, "error": self.error,
            "gpu_util_mean": self.gpu_util_mean, "gpu_mem_peak_mb": self.gpu_mem_peak_mb,
            "progress": self.progress(),
        }


class ReconQueue:
    def __init__(self, gpu_monitor=None, cwd=None, video_root=None, viz_port=None, warm=True):
        self.gpu = gpu_monitor
        self.warm = warm and os.environ.get("RTVIO_NO_WARM") != "1"
        self.worker = None
        self.cwd = cwd
        self.video_root = video_root
        self.viz_port = viz_port      # same port every job - they run one at a time (see module docstring)
        self.jobs = []
        self._lock = threading.Lock()
        self._wake = threading.Event()

    def start(self):
        if self.warm:
            self.worker = WarmWorker(self.cwd, dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8"))
            self.worker.ensure()                    # start loading VGGT now, before the first job
        threading.Thread(target=self._worker, daemon=True, name="recon-worker").start()
        return self

    def submit(self, session_dir, params):
        job = ReconJob("recording", session_dir, params)
        with self._lock:
            self.jobs.append(job)
        self._wake.set()
        print("[recon] queued job %d for %s" % (job.id, job.session_id), flush=True)
        return job

    def submit_video(self, video_path, params):
        job = ReconJob("video", video_path, params)
        with self._lock:
            self.jobs.append(job)
        self._wake.set()
        print("[recon] queued job %d for video %s" % (job.id, video_path), flush=True)
        return job

    def submit_live(self, session_dir, params):
        """Starts reconstructing a take as it records, instead of waiting for
        it to finish - see vggt_live.py's --tail. Submitted at record-start
        time (unlike submit(), which server.py calls once a take is already
        finalized), so this job's session_id is busy_with()-visible for the
        whole recording: server.py checks that before auto-queuing the normal
        post-recording batch job, so the two never run back to back on the
        same take."""
        job = ReconJob("live", session_dir, params)
        with self._lock:
            self.jobs.append(job)
        self._wake.set()
        print("[recon] queued live job %d for %s" % (job.id, job.session_id), flush=True)
        return job

    def cancel(self, job_id):
        with self._lock:
            job = next((j for j in self.jobs if j.id == job_id), None)
        if job is None:
            return False
        if job.state == "queued":
            job.state = "cancelled"
            return True
        if job.state == "running" and job.proc is not None:
            job.state = "cancelling"
            job.proc.terminate()
            return True
        return False

    def delete(self, job_id):
        """Removes a finished/failed/cancelled job's entry and deletes its
        out_dir from disk - for a "recording" job that's just one recon-N
        attempt (the session's captured frames are untouched); for a
        "video" job it's the job's whole output directory. Refuses a
        queued/running/cancelling job - cancel() it first."""
        with self._lock:
            job = next((j for j in self.jobs if j.id == job_id), None)
            if job is None:
                return False, "no such job"
            if job.state in ("queued", "running", "cancelling"):
                return False, "cancel it first"
            self.jobs.remove(job)
        if job.out_dir and os.path.isdir(job.out_dir):
            shutil.rmtree(job.out_dir, ignore_errors=True)
        return True, None

    def busy_with(self, session_id):
        with self._lock:
            return any(j.session_id == session_id and j.state in ("queued", "running", "cancelling")
                       for j in self.jobs)

    def snapshot(self):
        with self._lock:
            return [j.snapshot() for j in self.jobs[-30:]]

    def _next(self):
        with self._lock:
            return next((j for j in self.jobs if j.state == "queued"), None)

    def _worker(self):
        while True:
            job = self._next()
            if job is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            self._run(job)

    def _run(self, job):
        job.out_dir = (next_video_out_dir(self.video_root, job.source) if job.kind == "video"
                       else next_recon_dir(job.source))
        os.makedirs(job.out_dir, exist_ok=True)
        job.viz_port = self.viz_port
        cmd = build_command(job.kind, job.source, job.out_dir, job.params, self.viz_port)
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        job.state = "running"
        job.started = time.time()
        print("[recon] job %d: %s" % (job.id, " ".join(cmd)), flush=True)
        with open(os.path.join(job.out_dir, "job.log"), "w", encoding="utf-8") as log:
            log.write("$ %s\n\n" % " ".join(cmd))
            log.flush()
        if self.worker is not None and "-m" in cmd:
            m = cmd.index("-m")
            def set_proc(p):
                job.proc = p
            rc = self.worker.run(job.id, cmd[m + 1], cmd[m + 2:], os.path.join(job.out_dir, "job.log"), set_proc)
            if rc is None:
                job.returncode = -1
                if job.state != "cancelling":
                    job.error = "the VGGT worker process stopped (out of memory?) - see data/vggt_worker.log"
                threading.Thread(target=self.worker.ensure, daemon=True).start()   # warm a fresh one
            else:
                job.returncode = rc
        else:
            with open(os.path.join(job.out_dir, "job.log"), "a", encoding="utf-8") as log:
                try:
                    job.proc = subprocess.Popen(
                        cmd, stdout=log, stderr=subprocess.STDOUT, cwd=self.cwd, env=env,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    job.returncode = job.proc.wait()
                except OSError as e:
                    job.error = str(e)
                    job.returncode = -1
        job.finished = time.time()
        if self.gpu is not None:
            samples = self.gpu.since(job.started)
            if samples:
                job.gpu_util_mean = round(sum(s["util"] for s in samples) / len(samples), 1)
                job.gpu_mem_peak_mb = max(s["mem_mb"] for s in samples)
        if job.state == "cancelling":
            job.state = "cancelled"
        elif job.returncode == 0:
            job.state = "done"
        else:
            job.state = "failed"
            prog = job.progress() or {}
            job.error = job.error or prog.get("error") or "exit code %s" % job.returncode
        print("[recon] job %d %s (%.0f s, mean GPU util %s%%)"
              % (job.id, job.state, job.finished - job.started, job.gpu_util_mean), flush=True)
