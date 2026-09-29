"""
MARS-LVIG ROS1 bag -> RTVIO recorded-session directory, for `vggt_reconstruct --from-recording`.

    python tools/marslvig_to_session.py --bag HKairport_GNSS03.bag \
        --calib "HK_GNSS(airport & island).yaml" --out /tmp/hkairport_gnss03 [--stride 2]

Writes frames/NNNNNN.jpg (the bag's JPEGs, not re-encoded), frame_timestamps.json, gps_data.json
(standalone u-blox ZED-F9P fixes: what the pipeline gets), camera_intrinsics.json, and - never read
by the pipeline - ground_truth_rtk.csv (DJI RTK) plus session_meta.json for tools/eval_trajectory.py.
Needs `pip install rosbags opencv-python pyyaml`.
"""
import argparse
import csv
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from rtvio.stream.geodesy import latlon_to_enu  # noqa: E402

GPS_EPOCH_UNIX = 315964800          # 1980-01-06T00:00:00Z
GPS_UTC_LEAP_S = 18                 # GPS - UTC since 2017-01-01; the bags are from 2023

T_CAMERA = "/left_camera/image/compressed"
T_RTK = "/dji_osdk_ros/rtk_position"
T_RTK_INFO = "/dji_osdk_ros/rtk_info_position"
T_PVT = "/ublox_driver/receiver_pvt"


def gps_week_tow_to_unix_utc(week, tow, leap_s=GPS_UTC_LEAP_S):
    return GPS_EPOCH_UNIX + week * 604800 + tow - leap_s


def enu_track(lat, lon, alt, ref=None):
    ref = ref or (lat[0], lon[0], alt[0])
    return np.array([latlon_to_enu(a, b, c, *ref) for a, b, c in zip(lat, lon, alt)])


def flight_segment(t, enu, min_speed=1.0, pad_s=2.0, window_s=1.0):
    """[start, end] from the first to the last moment horizontal speed exceeds min_speed, padded; None if never."""
    t = np.asarray(t, float)
    speed = np.zeros(len(t))
    for i in range(len(t)):
        j0 = np.searchsorted(t, t[i] - window_s / 2)
        j1 = min(np.searchsorted(t, t[i] + window_s / 2, side="right") - 1, len(t) - 1)
        if j1 > j0 and t[j1] > t[j0]:
            speed[i] = np.linalg.norm(enu[j1, :2] - enu[j0, :2]) / (t[j1] - t[j0])
    moving = np.flatnonzero(speed > min_speed)
    if len(moving) == 0:
        return None
    return float(t[moving[0]] - pad_s), float(t[moving[-1]] + pad_s)


def nearest_pairs(t_a, t_b, max_dt):
    """Index pairs (i, j) with t_b[j] the nearest to t_a[i] and |dt| <= max_dt; t_b sorted."""
    t_b = np.asarray(t_b, float)
    out = []
    for i, t in enumerate(t_a):
        j = int(np.clip(np.searchsorted(t_b, t), 1, len(t_b) - 1))
        j = j - 1 if abs(t_b[j - 1] - t) <= abs(t_b[j] - t) else j
        if abs(t_b[j] - t) <= max_dt:
            out.append((i, j))
    return out


def datum_offsets(rtk, gnss, max_dt=0.1):
    """Median RTK - GNSS offset (east, north, up) over time-matched pairs: the altitude references differ."""
    pairs = nearest_pairs([g["t"] for g in gnss], [r["t"] for r in rtk], max_dt)
    if not pairs:
        return None
    ref = (rtk[0]["lat"], rtk[0]["lon"], rtk[0]["alt"])
    d = np.array([np.subtract(latlon_to_enu(rtk[j]["lat"], rtk[j]["lon"], rtk[j]["alt"], *ref),
                              latlon_to_enu(gnss[i]["lat"], gnss[i]["lon"], gnss[i]["alt"], *ref))
                  for i, j in pairs])
    med = np.median(d, axis=0)
    return {"rtk_minus_gnss_east_m": float(med[0]), "rtk_minus_gnss_north_m": float(med[1]),
            "rtk_minus_gnss_up_m": float(med[2]), "pairs": len(pairs),
            "horizontal_spread_p90_m": float(np.percentile(np.linalg.norm(d[:, :2] - med[:2], axis=1), 90))}


def intrinsics_profile(calib, width, height, source):
    """rtvio camera_model profile (Brown-Conrady 'pinhole') from a MARS-LVIG calibration yaml."""
    K = np.asarray(calib["camera_intrinsic"], float).reshape(3, 3)
    k1, k2, p1, p2, k3 = [float(v) for v in calib["camera_dist_coeffs"]]
    return {"model": "pinhole", "width": int(width), "height": int(height),
            "fx": float(K[0, 0]), "fy": float(K[1, 1]), "cx": float(K[0, 2]), "cy": float(K[1, 2]),
            "k1": k1, "k2": k2, "p1": p1, "p2": p2, "k3": k3, "source": source}


def _stamp(msg):
    return msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bag", required=True)
    ap.add_argument("--calib", required=True, help="MARS-LVIG calibration yaml for this sequence")
    ap.add_argument("--out", required=True)
    ap.add_argument("--stride", type=int, default=1, help="keep every Nth in-flight frame (camera is 10 Hz)")
    ap.add_argument("--no-trim", action="store_true", help="keep hover/takeoff/landing frames")
    ap.add_argument("--min-speed", type=float, default=1.0, help="m/s that counts as flying, for trimming")
    args = ap.parse_args()

    import cv2
    import yaml
    from pathlib import Path
    from rosbags.highlevel import AnyReader

    with open(args.calib) as f:
        calib = yaml.safe_load(f)
    os.makedirs(os.path.join(args.out, "frames"), exist_ok=True)

    with AnyReader([Path(args.bag)]) as r:
        conns = {c.topic: c for c in r.connections}
        missing = [t for t in (T_CAMERA, T_RTK, T_PVT) if t not in conns]
        assert not missing, "bag lacks topics %s" % missing

        def read(topic):
            for c, t_ns, raw in r.messages(connections=[conns[topic]]):
                yield t_ns * 1e-9, r.deserialize(raw, c.msgtype)

        # Every clock is checked against the bag's own receive time instead of being assumed.
        lag = {}
        rtk = []
        for t_bag, m in read(T_RTK):
            rtk.append({"t": _stamp(m), "lat": m.latitude, "lon": m.longitude, "alt": m.altitude, "info": None})
            lag.setdefault("rtk", []).append(rtk[-1]["t"] - t_bag)
        if T_RTK_INFO in conns:
            info = [(t_bag, int(m.data)) for t_bag, m in read(T_RTK_INFO)]
            it = [a for a, _ in info]
            for i, j in nearest_pairs([x["t"] for x in rtk], it, 0.3):
                rtk[i]["info"] = info[j][1]
        gnss = []
        for t_bag, m in read(T_PVT):
            if not (m.valid_fix and m.fix_type >= 3):
                continue
            t = gps_week_tow_to_unix_utc(m.time.week, m.time.tow)
            gnss.append({"t": t, "lat": m.latitude, "lon": m.longitude, "alt": m.altitude,
                         "h_acc": float(m.h_acc), "v_acc": float(m.v_acc)})
            lag.setdefault("gnss_utc", []).append(t - t_bag)

        rtk_enu = enu_track([x["lat"] for x in rtk], [x["lon"] for x in rtk], [x["alt"] for x in rtk])
        seg = None if args.no_trim else flight_segment([x["t"] for x in rtk], rtk_enu, args.min_speed)
        if seg is None and not args.no_trim:
            print("WARNING: RTK never exceeds %.1f m/s - keeping the whole bag" % args.min_speed)

        frame_times, size, kept, seen = [], None, 0, 0
        for t_bag, m in read(T_CAMERA):
            t = _stamp(m)
            lag.setdefault("camera", []).append(t - t_bag)
            if seg is not None and not (seg[0] <= t <= seg[1]):
                continue
            seen += 1
            if (seen - 1) % args.stride:
                continue
            data = bytes(m.data)
            if size is None:
                img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                assert img is not None, "first camera frame does not decode as an image"
                size = (img.shape[1], img.shape[0])
            with open(os.path.join(args.out, "frames", "%06d.jpg" % kept), "wb") as f:
                f.write(data)
            frame_times.append(t)
            kept += 1

    assert kept >= 2, "only %d frames kept - check the segment/stride" % kept
    clock = {k: {"median_s": float(np.median(v)), "max_abs_s": float(np.max(np.abs(v)))} for k, v in lag.items()}
    bad = [k for k, v in clock.items() if abs(v["median_s"]) > 0.5]
    if bad:
        print("WARNING: header clocks disagree with bag receive time by > 0.5 s: %s" % bad)

    t0 = frame_times[0]
    lo, hi = frame_times[0] - 5.0, frame_times[-1] + 5.0
    with open(os.path.join(args.out, "frame_timestamps.json"), "w") as f:
        json.dump([round(t - t0, 6) for t in frame_times], f)
    with open(os.path.join(args.out, "gps_data.json"), "w") as f:
        json.dump([{"timestamp": round(g["t"] - t0, 6), "latitude_deg": g["lat"], "longitude_deg": g["lon"],
                    "altitude_m": g["alt"], "accuracy_m": g["h_acc"], "sigma_m": g["h_acc"],
                    "v_accuracy_m": g["v_acc"]} for g in gnss if lo <= g["t"] <= hi], f)
    with open(os.path.join(args.out, "camera_intrinsics.json"), "w") as f:
        json.dump(intrinsics_profile(calib, size[0], size[1], os.path.basename(args.calib)), f, indent=2)
    with open(os.path.join(args.out, "ground_truth_rtk.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "lat_deg", "lon_deg", "alt_m", "rtk_info_position"])
        for x in rtk:
            if lo <= x["t"] <= hi:
                w.writerow(["%.6f" % (x["t"] - t0), "%.10f" % x["lat"], "%.10f" % x["lon"], "%.4f" % x["alt"],
                            "" if x["info"] is None else x["info"]])

    in_seg = [g for g in gnss if lo <= g["t"] <= hi]
    meta = {
        "source_bag": os.path.basename(args.bag), "calib": os.path.basename(args.calib),
        "t0_unix_utc": t0, "frames": kept, "stride": args.stride, "image_size": list(size),
        "segment_unix_utc": seg, "span_s": frame_times[-1] - t0,
        "gnss_fixes": len(in_seg), "rtk_fixes": sum(lo <= x["t"] <= hi for x in rtk),
        "gnss_h_acc_median_m": float(np.median([g["h_acc"] for g in in_seg])) if in_seg else None,
        "gps_utc_leap_s": GPS_UTC_LEAP_S, "clock_vs_bag_receive_time": clock,
        "datum": datum_offsets([x for x in rtk if lo <= x["t"] <= hi], in_seg),
    }
    with open(os.path.join(args.out, "session_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
