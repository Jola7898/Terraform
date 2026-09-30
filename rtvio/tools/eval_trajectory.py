"""
Score reconstruction runs against a session's RTK ground truth (tools/marslvig_to_session.py output).

    python tools/eval_trajectory.py --session /tmp/hkairport_gnss03 --run out_global --run out_guided

Per run: absolute error of the georeferenced camera centres (horizontal / vertical, fraction within the
1 m target) - only for runs whose cameras.json is in GPS ENU - and trajectory-shape error after a
best-fit Sim(3) (every run), plus throughput and seam stats from CHECKPOINT_REPORT.json. Writes
eval.json into each run directory and prints a markdown comparison table.

Ground truth: DJI RTK, shifted vertically by session_meta's median RTK - GNSS offset, because the two
receivers report altitude against different references. Horizontal is left untouched. The camera -
RTK antenna lever arm is unknown (tens of cm) and not corrected.
"""
import argparse
import csv
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from rtvio.stream.geodesy import latlon_to_enu  # noqa: E402

ENU_REF_RE = re.compile(r"ENU metres about\s+([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)")


def parse_enu_ref(frame_desc):
    m = ENU_REF_RE.search(frame_desc or "")
    return tuple(float(v) for v in m.groups()) if m else None


def load_gt(session_dir):
    rows = list(csv.DictReader(open(os.path.join(session_dir, "ground_truth_rtk.csv"))))
    t = np.array([float(r["t"]) for r in rows])
    llh = np.array([[float(r["lat_deg"]), float(r["lon_deg"]), float(r["alt_m"])] for r in rows])
    order = np.argsort(t)
    return t[order], llh[order]


def interp_gt(t_query, t_gt, llh_gt, max_gap_s=0.5):
    """Linear interpolation of lat/lon/alt at t_query; valid only between two fixes <= max_gap_s apart."""
    t_query = np.asarray(t_query, float)
    j = np.clip(np.searchsorted(t_gt, t_query), 1, len(t_gt) - 1)
    t0, t1 = t_gt[j - 1], t_gt[j]
    valid = (t_query >= t_gt[0]) & (t_query <= t_gt[-1]) & (t1 - t0 <= max_gap_s)
    w = np.where(t1 > t0, (t_query - t0) / np.where(t1 > t0, t1 - t0, 1.0), 0.0)[:, None]
    return llh_gt[j - 1] * (1 - w) + llh_gt[j] * w, valid


def umeyama(src, dst):
    """Least-squares Sim(3) (s, R, t) with s R src + t ~ dst."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    U, D, Vt = np.linalg.svd(xd.T @ xs / len(src))
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1
    R = U @ S @ Vt
    s = np.trace(np.diag(D) @ S) / (xs ** 2).sum(1).mean()
    return s, R, mu_d - s * R @ mu_s


def _stats(e):
    return {"median_m": float(np.median(e)), "rmse_m": float(np.sqrt(np.mean(e ** 2))),
            "p90_m": float(np.percentile(e, 90)), "max_m": float(np.max(e))}


def _absolute(e):
    h, v, d3 = np.linalg.norm(e[:, :2], axis=1), np.abs(e[:, 2]), np.linalg.norm(e, axis=1)
    return {"horizontal": _stats(h), "vertical": _stats(v), "3d": _stats(d3),
            "within_1m_3d": float(np.mean(d3 <= 1.0)), "within_1m_horizontal": float(np.mean(h <= 1.0))}


def _plot(path, title, gt, aligned, georef):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.plot(gt[:, 0], gt[:, 1], "k-", lw=2, label="RTK ground truth")
    if georef is not None:
        ax.plot(georef[:, 0], georef[:, 1], "r-", lw=1, label="reconstruction (as georeferenced)")
    ax.plot(aligned[:, 0], aligned[:, 1], "b-", lw=1, label="reconstruction (best-fit Sim(3))")
    ax.plot(gt[0, 0], gt[0, 1], "go", label="start")
    ax.set_aspect("equal")
    ax.set_xlabel("East (m)")
    ax.set_ylabel("North (m)")
    ax.set_title(title)
    ax.legend()
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def evaluate(run_dir, session_dir):
    cams = json.load(open(os.path.join(run_dir, "cameras.json")))
    meta = json.load(open(os.path.join(session_dir, "session_meta.json")))
    t_gt, llh_gt = load_gt(session_dir)
    up_offset = ((meta.get("datum") or {}).get("rtk_minus_gnss_up_m")) or 0.0
    llh_gt = llh_gt - np.array([0.0, 0.0, up_offset])

    t = np.array([f["time"] for f in cams["frames"]])
    est = np.array([f["center"] for f in cams["frames"]], float)
    gt_llh, valid = interp_gt(t, t_gt, llh_gt)
    ref = parse_enu_ref(cams.get("frame"))
    enu_ref = ref or tuple(gt_llh[valid][0])
    gt = np.array([latlon_to_enu(*p, *enu_ref) for p in gt_llh])
    est, gt = est[valid], gt[valid]

    out = {"run": os.path.basename(os.path.normpath(run_dir)), "frames_scored": int(valid.sum()),
           "frames_total": len(t), "georeferenced": ref is not None, "gt_vertical_offset_applied_m": up_offset}
    if ref is not None:
        out["absolute"] = _absolute(est - gt)
        # The two receivers can disagree by a constant horizontal offset (HKairport_GNSS03: ~7.5 m, p90 spread
        # 2.2 m) - a reference-frame difference no GPS-anchored pipeline can see. Scored both ways.
        d = meta.get("datum") or {}
        en = np.array([d.get("rtk_minus_gnss_east_m") or 0.0, d.get("rtk_minus_gnss_north_m") or 0.0, 0.0])
        out["absolute_receiver_offset_removed"] = dict(_absolute(est - (gt - en)), offset_en_m=en[:2].tolist())
    s, R, tr = umeyama(est, gt)
    aligned = s * est @ R.T + tr
    ate = np.linalg.norm(aligned - gt, axis=1)
    out["shape_sim3"] = {**_stats(ate), "scale": float(s)}
    _plot(os.path.join(run_dir, "trajectory_vs_rtk.png"), out["run"], gt, aligned, est if ref is not None else None)

    rep_path = os.path.join(run_dir, "CHECKPOINT_REPORT.json")
    if os.path.exists(rep_path):
        rep = json.load(open(rep_path))
        out["run_stats"] = {k: rep.get(k) for k in ("gpu", "vggt_fps", "peak_mb", "wall_s", "span_s", "windows",
                                                    "seam_fallbacks", "seam_gps_scaled", "seam_scale_min", "n_pts")}
    with open(os.path.join(run_dir, "eval.json"), "w") as f:
        json.dump(out, f, indent=2)
    return out


def table(results):
    head = ("| run | georef | horiz median / p90 (m) | horiz median, receiver offset removed (m) | vert median (m) "
            "| <=1 m (3D) | shape ATE rmse (m) | fps | peak VRAM (GB) | fallback / GPS-scaled seams |")
    lines = [head, "|" + "---|" * 10]
    for r in results:
        a, st = r.get("absolute"), r.get("run_stats") or {}
        ao = r.get("absolute_receiver_offset_removed")
        lines.append("| %s | %s | %s | %s | %s | %s | %.2f | %s | %s | %s / %s |" % (
            r["run"], "yes" if r["georeferenced"] else "no",
            "%.2f / %.2f" % (a["horizontal"]["median_m"], a["horizontal"]["p90_m"]) if a else "-",
            "%.2f" % ao["horizontal"]["median_m"] if ao else "-",
            "%.2f" % a["vertical"]["median_m"] if a else "-",
            "%.0f%%" % (100 * a["within_1m_3d"]) if a else "-",
            r["shape_sim3"]["rmse_m"],
            "%.2f" % st["vggt_fps"] if st.get("vggt_fps") else "-",
            "%.1f" % (st["peak_mb"] / 1000) if st.get("peak_mb") else "-",
            st.get("seam_fallbacks", "-"), st.get("seam_gps_scaled", "-")))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True)
    ap.add_argument("--run", action="append", required=True, help="reconstruction output dir (repeatable)")
    args = ap.parse_args()
    results = [evaluate(r, args.session) for r in args.run]
    print(table(results))


if __name__ == "__main__":
    main()
