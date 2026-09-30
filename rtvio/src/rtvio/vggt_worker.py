"""
Persistent reconstruction worker: loads VGGT once, then runs reconstruction
jobs one after another without ever unloading it.

Started by rtvio.studio (jobs.WarmWorker) when the Studio starts, so the
model is already on the GPU when a video is uploaded or a phone/drone
connects - no ~5 GB checkpoint read at the start of every job. Not meant to
be run by hand.

Protocol (the Studio's side is jobs.WarmWorker):
  stdin   one JSON object per line: {"id", "module", "argv", "log"} - run
          `python -m <module> <argv...>` (vggt_reconstruct or vggt_live) here,
          with its stdout/stderr going to the file `log`
  stdout  {"ready": true} once the model is loaded, then {"done": id, "rc": n}
          after each job. This channel is a private dup of the original
          stdout; fd 1 itself is pointed at stderr so nothing else (torch,
          a stray print) can corrupt it.
Cancel = the Studio kills this process; it then starts a fresh one.
"""
import gc
import importlib
import json
import os
import sys
import traceback


def main():
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def send(obj):
        proto.write(json.dumps(obj) + "\n")
        proto.flush()

    import torch
    from . import vggt_reconstruct as vr

    device = "cuda" if torch.cuda.is_available() else "cpu"
    vr._load_vggt(device)
    send({"ready": True})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        job = json.loads(line)
        rc = 1
        real_out, real_err = sys.stdout, sys.stderr
        try:
            with open(job["log"], "a", encoding="utf-8", errors="replace", buffering=1) as log:
                sys.stdout = sys.stderr = log
                sys.argv = [job["module"]] + list(job["argv"])
                if device == "cuda":
                    torch.cuda.reset_peak_memory_stats()
                try:
                    importlib.import_module(job["module"]).main()
                    rc = 0
                except SystemExit as e:
                    rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
                except BaseException:                    # noqa: BLE001 - a failed job must not end the worker
                    traceback.print_exc()
                    rc = 1
                finally:
                    sys.stdout, sys.stderr = real_out, real_err
        except OSError:
            traceback.print_exc()
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()
        send({"done": job["id"], "rc": rc})


if __name__ == "__main__":
    main()
