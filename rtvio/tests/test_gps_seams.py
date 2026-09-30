"""
CPU tests for GPS-guided seam scale checks (rtvio/gps_seams.py). No GPU or checkpoint needed.

    python tests/test_gps_seams.py
"""
import numpy as np

from rtvio.gps_seams import GpsSeamGuide, MIN_WINDOWS_FOR_GLOBAL

PASS, FAIL = [], []

HEALTHY = {"method": "dense-sim3", "median_rel_residual": 0.005, "inlier_frac": 0.9}
POOR = {"method": "dense-sim3", "median_rel_residual": 0.12, "inlier_frac": 0.3}
FALLBACK = {"method": "single-camera fallback", "median_rel_residual": None, "inlier_frac": None}


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("%s %-64s %s" % ("PASS" if ok else "FAIL", name, detail))


def _rot(axis, deg):
    a = np.radians(deg)
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + np.sin(a) * K + (1 - np.cos(a)) * K @ K


def _track(n=400, straight=False):
    """ENU camera centres: a 60 deg arc of radius 150 m at 80 m altitude (or a near-straight 160 m line)."""
    u = np.linspace(0, 1, n)
    if straight:
        return np.stack([160 * u, 0.3 * np.sin(6 * u), np.full(n, 80.0)], 1)
    a = np.radians(60) * u
    return np.stack([150 * np.cos(a), 150 * np.sin(a), np.full(n, 80.0)], 1)


def _scenario(rng, sigma=3.0, straight=False, windows_placed=MIN_WINDOWS_FOR_GLOBAL):
    """Guide after `windows_placed` 64-frame windows (8 overlap); returns (guide, next window idxs, its local centres,
    true local->global scale)."""
    enu = _track(straight=straight)
    s_ge, R_ge, t_ge = 7.0, _rot([1, 2, 3], 40), np.array([5.0, -3.0, 1.0])        # global -> ENU
    glob = (enu - t_ge) @ R_ge / s_ge
    guide = GpsSeamGuide([e + rng.normal(scale=sigma, size=3) for e in enu], sigma_m=sigma)
    for w in range(windows_placed):
        guide.update({i: (None, glob[i], None) for i in range(56 * w + 64)})
    start = 56 * windows_placed
    idxs = list(range(start, start + 64))
    s_lg, R_lg, t_lg = 0.37, _rot([0, 1, 1], -25), np.array([0.2, 0.1, -0.4])    # next window local -> global
    return guide, idxs, (glob[idxs] - t_lg) @ R_lg / s_lg, s_lg


def test_healthy_dense_seam_is_never_overruled():
    # Regression: on MARS-LVIG HKairport_GNSS03 a GPS fit claimed a resid-0.001 dense seam was 10x off, and the
    # override cascaded to a 20x-scaled map that exhausted RAM in fusion.
    rng = np.random.default_rng(0)
    guide, idxs, C, s_true = _scenario(rng)
    use, _, _ = guide.check(idxs, C, 10.0 * s_true, HEALTHY)
    check("healthy dense seam kept even when GPS says it is 10x off", use is False)


def test_poor_dense_seams():
    rng = np.random.default_rng(1)
    for straight in (False, True):
        caught, kept, errs = 0, 0, []
        for _ in range(100):
            guide, idxs, C, s_true = _scenario(rng, straight=straight)
            use, s_gps, _ = guide.check(idxs, C, 0.0085 * s_true, POOR)
            caught += use
            errs.append(abs(np.log(s_gps / s_true)))
            kept += not guide.check(idxs, C, 1.10 * s_true, POOR)[0]
        check("poor dense seam: collapse overruled, 10%% kept (%s)" % ("straight" if straight else "arc"),
              caught == 100 and kept == 100,
              "caught %d/100, kept %d/100, worst GPS scale error %.1f%%" % (caught, kept, 100 * max(errs)))


def test_fallback_takes_gps_scale():
    rng = np.random.default_rng(3)
    guide, idxs, C, s_true = _scenario(rng)
    use, s_gps, sig = guide.check(idxs, C, 3.0 * s_true, FALLBACK)
    check("fallback seam takes the GPS scale", use and abs(np.log(s_gps / s_true)) < 5 * sig,
          "s_gps/s_true %.3f, sigma %.1f%%" % (s_gps / s_true, 100 * sig))


def test_global_fit_waits_for_enough_flight():
    rng = np.random.default_rng(4)
    guide, idxs, C, _ = _scenario(rng, windows_placed=MIN_WINDOWS_FOR_GLOBAL - 1)
    check("fewer than %d windows placed -> no global fit, no intervention" % MIN_WINDOWS_FOR_GLOBAL,
          guide.G is None and guide.check(idxs, C, 0.01, FALLBACK)[0] is False)

    short = GpsSeamGuide([np.array([0.25 * i, 0.0, 80.0]) for i in range(400)], sigma_m=3.0)   # 5 fps at 1.25 m/s
    for w in range(MIN_WINDOWS_FOR_GLOBAL):
        short.update({i: (None, np.array([0.01 * i, 0.0, 0.0]), None) for i in range(56 * w + 64)})
    check("GPS spread < 30 m after 3 windows -> still no global fit", short.G is None)


def test_unobservable_window_leaves_vision_alone():
    rng = np.random.default_rng(5)
    guide, idxs, C, _ = _scenario(rng)
    hover = GpsSeamGuide([np.array([0.0, 0.0, 80.0]) + rng.normal(scale=0.5, size=3) for _ in range(400)], 3.0)
    hover.G, hover.G_sigma_rel = guide.G, guide.G_sigma_rel
    check("hovering window (fixes span < 5 m) -> no intervention", hover.check(idxs, C, 0.01, FALLBACK)[0] is False)
    nofix = GpsSeamGuide([None] * 400, sigma_m=3.0)
    for w in range(MIN_WINDOWS_FOR_GLOBAL):
        nofix.update({i: (None, np.zeros(3), None) for i in range(56 * w + 64)})
    check("no fixes at all -> no global fit, no intervention",
          nofix.G is None and nofix.check(idxs, C, 0.01, FALLBACK)[0] is False)


def test_sigma_is_honest():
    rng = np.random.default_rng(6)
    sigs, errs = [], []
    for _ in range(300):
        guide, idxs, C, s_true = _scenario(rng)
        _, s_gps, sig = guide.check(idxs, C, s_true, FALLBACK)
        sigs.append(sig)
        errs.append(np.log(s_gps / s_true))
    ratio = np.std(errs) / np.median(sigs)
    check("predicted GPS scale sigma matches Monte-Carlo spread within 2x", 0.5 < ratio < 2.0,
          "predicted %.2f%%, observed %.2f%%" % (100 * np.median(sigs), 100 * np.std(errs)))


if __name__ == "__main__":
    test_healthy_dense_seam_is_never_overruled()
    test_poor_dense_seams()
    test_fallback_takes_gps_scale()
    test_global_fit_waits_for_enough_flight()
    test_unobservable_window_leaves_vision_alone()
    test_sigma_is_honest()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    raise SystemExit(1 if FAIL else 0)
