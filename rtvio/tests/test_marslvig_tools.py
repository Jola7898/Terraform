"""
CPU tests for tools/marslvig_to_session.py helpers and tools/eval_trajectory.py. numpy only - no bag,
GPU, torch or OpenCV needed.

    python tests/test_marslvig_tools.py
"""
import csv
import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import eval_trajectory as ev  # noqa: E402
import marslvig_to_session as conv  # noqa: E402
from rtvio.stream.geodesy import enu_to_latlon  # noqa: E402

PASS, FAIL = [], []
REF = (22.4161188, 114.0427060, 91.0)


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("%s %-64s %s" % ("PASS" if ok else "FAIL", name, detail))


def _rot(axis, deg):
    a = np.radians(deg)
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + np.sin(a) * K + (1 - np.cos(a)) * K @ K


def test_gps_time_matches_the_bag():
    # HKairport_GNSS03 receiver_pvt: week 2285, tow 286166.4; its receiver_lla header (GPS time) read
    # 1698218966.4 while camera/RTK stamps (UTC) of the same moment read 1698218948.x.
    utc = conv.gps_week_tow_to_unix_utc(2285, 286166.4)
    check("GPS week/tow -> UTC reproduces the bag's 18 s GPS-UTC gap",
          abs(utc - 1698218948.4) < 1e-6 and abs(conv.gps_week_tow_to_unix_utc(2285, 286166.4, 0) - 1698218966.4) < 1e-6,
          "utc %.1f" % utc)


def _flight(hover=10.0, fly=30.0, speed=5.0, hz=5.0):
    t = np.arange(0, 2 * hover + fly, 1 / hz)
    x = np.clip(t - hover, 0, fly) * speed
    return t, np.stack([x, np.zeros_like(x), np.full_like(x, 80.0)], 1)


def test_flight_segment():
    t, enu = _flight()
    seg = conv.flight_segment(t, enu, min_speed=1.0, pad_s=2.0)
    check("flight segment = moving span + padding",
          seg is not None and abs(seg[0] - 8.0) < 0.7 and abs(seg[1] - 42.0) < 0.7, "segment %s" % (seg,))
    check("no movement -> no segment", conv.flight_segment(t, np.zeros_like(enu)) is None)


def test_nearest_pairs():
    pairs = conv.nearest_pairs([0.05, 1.0, 5.0], [0.0, 0.2, 0.4, 1.02], max_dt=0.1)
    check("nearest-time pairing respects max_dt", pairs == [(0, 0), (1, 3)], str(pairs))


def test_datum_offsets():
    rng = np.random.default_rng(0)
    t, enu = _flight()
    rtk, gnss = [], []
    for ti, p in zip(t, enu):
        lat, lon, alt = enu_to_latlon(*p, *REF)
        rtk.append({"t": ti, "lat": lat, "lon": lon, "alt": alt})
        q = p + np.array([rng.normal(0, 0.5), rng.normal(0, 0.5), -8.6 + rng.normal(0, 1.0)])
        lat, lon, alt = enu_to_latlon(*q, *REF)
        gnss.append({"t": ti + 0.01, "lat": lat, "lon": lon, "alt": alt})
    d = conv.datum_offsets(rtk, gnss)
    check("datum offset recovers the 8.6 m altitude-reference gap",
          d is not None and abs(d["rtk_minus_gnss_up_m"] - 8.6) < 0.3 and abs(d["rtk_minus_gnss_east_m"]) < 0.2,
          "up %.2f m over %d pairs" % (d["rtk_minus_gnss_up_m"], d["pairs"]))


def test_intrinsics_profile():
    calib = {"camera_intrinsic": [1444.43, 0.0, 1179.50, 0.0, 1444.34, 1044.90, 0.0, 0.0, 1.0],
             "camera_dist_coeffs": [-0.0560, 0.1180, 0.00122, 0.00064, -0.0627]}
    p = conv.intrinsics_profile(calib, 2448, 2048, "HK_GNSS(airport & island).yaml")
    check("calibration yaml -> rtvio pinhole profile",
          p["model"] == "pinhole" and p["fx"] == 1444.43 and p["cy"] == 1044.90 and p["k1"] == -0.056
          and p["k3"] == -0.0627 and (p["width"], p["height"]) == (2448, 2048))


def _write_session(d, t, enu_gt, up_offset):
    with open(os.path.join(d, "ground_truth_rtk.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "lat_deg", "lon_deg", "alt_m", "rtk_info_position"])
        for ti, p in zip(t, enu_gt):
            lat, lon, alt = enu_to_latlon(p[0], p[1], p[2] + up_offset, *REF)
            w.writerow(["%.6f" % ti, "%.10f" % lat, "%.10f" % lon, "%.4f" % alt, 50])
    with open(os.path.join(d, "session_meta.json"), "w") as f:
        json.dump({"datum": {"rtk_minus_gnss_up_m": up_offset}}, f)


def _write_run(d, t, centers, frame_desc):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "cameras.json"), "w") as f:
        json.dump({"frame": frame_desc,
                   "frames": [{"index": i, "time": float(ti), "center": list(map(float, c))}
                              for i, (ti, c) in enumerate(zip(t, centers))]}, f)
    with open(os.path.join(d, "CHECKPOINT_REPORT.json"), "w") as f:
        json.dump({"vggt_fps": 2.5, "peak_mb": 9400.0, "seam_fallbacks": 1, "seam_gps_scaled": 1}, f)


def test_evaluate_end_to_end():
    t, enu = _flight()
    t_frames = t[::2] + 0.1                                      # frames between RTK fixes -> interpolation
    gt_frames = np.interp(t_frames, t, enu[:, 0])
    gt_frames = np.stack([gt_frames, np.zeros_like(gt_frames), np.full_like(gt_frames, 80.0)], 1)
    desc = "ENU metres about %.7f, %.7f, %.1f m" % REF
    with tempfile.TemporaryDirectory() as d:
        _write_session(d, t, enu, up_offset=8.6)

        _write_run(os.path.join(d, "georef"), t_frames, gt_frames + [0.5, 0.0, -0.3], desc)
        r = ev.evaluate(os.path.join(d, "georef"), d)
        a = r["absolute"]
        check("georeferenced run: absolute errors after the datum shift",
              abs(a["horizontal"]["median_m"] - 0.5) < 0.02 and abs(a["vertical"]["median_m"] - 0.3) < 0.02
              and a["within_1m_3d"] == 1.0 and r["shape_sim3"]["rmse_m"] < 0.02,
              "h %.3f v %.3f shape %.3f" % (a["horizontal"]["median_m"], a["vertical"]["median_m"],
                                            r["shape_sim3"]["rmse_m"]))

        s, R, tr = 0.13, _rot([1, 2, 3], 50), np.array([4.0, -2.0, 7.0])
        _write_run(os.path.join(d, "relative"), t_frames, s * gt_frames @ R.T + tr,
                   "relative (VGGT units, not metres)")
        r2 = ev.evaluate(os.path.join(d, "relative"), d)
        check("relative run: no absolute score, Sim(3) shape error ~0, scale recovered",
              "absolute" not in r2 and r2["shape_sim3"]["rmse_m"] < 0.02 and abs(r2["shape_sim3"]["scale"] - 1 / s) < 1e-3,
              "shape %.4f scale %.3f" % (r2["shape_sim3"]["rmse_m"], r2["shape_sim3"]["scale"]))

        table = ev.table([r, r2])
        check("comparison table has a row per run", table.count("\n") == 3 and "georef" in table and "relative" in table)


def test_interp_gap_handling():
    t_gt = np.array([0.0, 0.2, 5.0, 5.2])
    llh = np.array([[0, 0, 0], [0, 0, 2], [0, 0, 4], [0, 0, 6]], float)
    vals, ok = ev.interp_gt([0.1, 2.0, 5.1, 9.0], t_gt, llh)
    check("ground-truth interpolation refuses gaps and extrapolation",
          ok.tolist() == [True, False, True, False] and abs(vals[0, 2] - 1.0) < 1e-9, str(ok.tolist()))


def test_video_keeps_capture_time():
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        check("video: skipped (no ffmpeg on PATH)", True)
        return
    # 10 Hz camera with frame 5 dropped: the video must hold frame 4 there, so video frame k is still t = k / 10.
    times = [0.0, 0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1]
    greys = [20 * i for i in range(len(times))]
    with tempfile.TemporaryDirectory() as d:
        names = []
        for i, g in enumerate(greys):
            names.append("%06d.jpg" % i)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=0x%02x%02x%02x:s=64x48" % (g, g, g), "-frames:v", "1", "-q:v", "2",
                            os.path.join(d, names[-1])], check=True)
        out = os.path.join(d, "flight.mp4")
        fps = conv.encode_video(d, names, times, out)
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", out, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True, check=True).stdout
        frames = np.frombuffer(raw, np.uint8).reshape(-1, 48, 64)
        got = [int(np.argmin(np.abs(np.array(greys) - f.mean()))) for f in frames]
        want = [int(np.searchsorted(times, k / fps + 1e-6, side="right") - 1) for k in range(len(frames))]
        check("video: 10 fps, 12 frames for 1.1 s, dropped frame held", fps == 10.0 and len(frames) == 12
              and got == want, "fps %s, frames %d, shown %s" % (fps, len(frames), got))
        check("video: concat script removed", not os.path.exists(os.path.join(d, "concat.txt")))

        gnss = [{"t": 100.0 + t, "lat": 22.4 + 1e-5 * t, "lon": 114.0, "alt": 90.0, "h_acc": 0.5}
                for t in (-9.0, -0.5, 0.25, 3.0)]
        csv_path = os.path.join(d, "gps.csv")
        conv.write_gps_csv(csv_path, gnss, 100.0, 95.0, 105.0)
        rows = list(csv.DictReader(open(csv_path)))
        check("gps.csv: video-relative seconds, window-trimmed, reader's columns",
              [float(r["timestamp_s"]) for r in rows] == [-0.5, 0.25, 3.0]
              and set(rows[0]) >= {"timestamp_s", "lat_deg", "lon_deg", "alt_m"})


if __name__ == "__main__":
    test_video_keeps_capture_time()
    test_gps_time_matches_the_bag()
    test_flight_segment()
    test_nearest_pairs()
    test_datum_offsets()
    test_intrinsics_profile()
    test_evaluate_end_to_end()
    test_interp_gap_handling()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    raise SystemExit(1 if FAIL else 0)
