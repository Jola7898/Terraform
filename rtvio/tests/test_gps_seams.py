"""
CPU tests for GPS-guided seam scale checks (rtvio/gps_seams.py). No GPU or checkpoint needed.

    python tests/test_gps_seams.py
"""
import numpy as np

from rtvio.gps_seams import GpsSeamGuide

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("%s %-64s %s" % ("PASS" if ok else "FAIL", name, detail))


def _rot(axis, deg):
    a = np.radians(deg)
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + np.sin(a) * K + (1 - np.cos(a)) * K @ K


def _track(n=200, straight=False):
    """ENU camera centres: a 60 deg arc of radius 30 m at 80 m altitude (or a near-straight 60 m line)."""
    u = np.linspace(0, 1, n)
    if straight:
        return np.stack([60 * u, 0.3 * np.sin(6 * u), np.full(n, 80.0)], 1)
    a = np.radians(60) * u
    return np.stack([30 * np.cos(a), 30 * np.sin(a), np.full(n, 80.0)], 1)


def _scenario(rng, sigma=3.0, straight=False):
    """(guide primed on window 0, window-1 frame indices, window-1 local centres, true local->global scale)."""
    enu = _track(straight=straight)
    s_ge, R_ge, t_ge = 7.0, _rot([1, 2, 3], 40), np.array([5.0, -3.0, 1.0])        # global -> ENU
    glob = (enu - t_ge) @ R_ge / s_ge
    noisy = [e + rng.normal(scale=sigma, size=3) for e in enu]
    guide = GpsSeamGuide(noisy, sigma_m=sigma)
    guide.update({i: (None, glob[i], None) for i in range(64)})
    idxs = list(range(56, 120))
    s_lg, R_lg, t_lg = 0.37, _rot([0, 1, 1], -25), np.array([0.2, 0.1, -0.4])    # window-1 local -> global
    C_local = (glob[idxs] - t_lg) @ R_lg / s_lg
    return guide, idxs, C_local, s_lg


def test_healthy_dense_seams_not_overruled():
    rng = np.random.default_rng(0)
    for straight in (False, True):
        overruled, errs = 0, []
        for _ in range(300):
            guide, idxs, C, s_true = _scenario(rng, straight=straight)
            use, s_gps, sig = guide.check(idxs, C, s_true, dense=True)
            overruled += use
            errs.append(abs(np.log(s_gps / s_true)))
        check("healthy dense seam kept (%s track)" % ("straight" if straight else "arc"),
              overruled == 0, "overruled %d/300, median GPS scale error %.1f%%" % (overruled, 100 * np.median(errs)))


def test_collapsed_seam_overruled():
    rng = np.random.default_rng(1)
    caught, errs = 0, []
    for _ in range(100):
        guide, idxs, C, s_true = _scenario(rng)
        use, s_gps, _ = guide.check(idxs, C, 0.0085 * s_true, dense=True)
        caught += use
        errs.append(abs(np.log(s_gps / s_true)))
    check("dense seam collapsed to 0.0085x is overruled by GPS", caught == 100,
          "caught %d/100, GPS scale within %.1f%% (worst)" % (caught, 100 * max(errs)))


def test_small_disagreement_kept():
    rng = np.random.default_rng(2)
    kept = 0
    for _ in range(100):
        guide, idxs, C, s_true = _scenario(rng)
        kept += not guide.check(idxs, C, 1.10 * s_true, dense=True)[0]
    check("dense seam 10% off GPS is kept (vision beats consumer GPS)", kept == 100, "kept %d/100" % kept)


def test_fallback_takes_gps_scale():
    rng = np.random.default_rng(3)
    guide, idxs, C, s_true = _scenario(rng)
    use, s_gps, sig = guide.check(idxs, C, 3.0 * s_true, dense=False)
    check("fallback seam takes the GPS scale", use and abs(np.log(s_gps / s_true)) < 5 * sig,
          "s_gps/s_true %.3f, sigma %.1f%%" % (s_gps / s_true, 100 * sig))


def test_unobservable_cases_leave_vision_alone():
    rng = np.random.default_rng(4)
    guide, idxs, C, _ = _scenario(rng)
    fresh = GpsSeamGuide(guide.enu, sigma_m=3.0)
    check("no global GPS fit yet -> no intervention", fresh.check(idxs, C, 0.01, dense=False) == (False, None, None))

    hover = [np.array([0.0, 0.0, 80.0]) + rng.normal(scale=0.5, size=3) for _ in range(200)]
    g2 = GpsSeamGuide(hover, sigma_m=3.0)
    g2.G = guide.G
    check("hovering window (fixes span < 5 m) -> no intervention", g2.check(idxs, C, 0.01, dense=False)[0] is False)

    g3 = GpsSeamGuide([None] * 200, sigma_m=3.0)
    g3.update({i: (None, np.zeros(3), None) for i in range(64)})
    check("no fixes at all -> no global fit, no intervention",
          g3.G is None and g3.check(idxs, C, 0.01, dense=False)[0] is False)


def test_sigma_is_honest():
    rng = np.random.default_rng(5)
    sigs, errs = [], []
    for _ in range(300):
        guide, idxs, C, s_true = _scenario(rng, sigma=3.0)
        _, s_gps, sig = guide.check(idxs, C, s_true, dense=True)
        sigs.append(sig)
        errs.append(np.log(s_gps / s_true))
    ratio = np.std(errs) / np.median(sigs)
    check("predicted GPS scale sigma matches Monte-Carlo spread within 2x", 0.5 < ratio < 2.0,
          "predicted %.2f%%, observed %.2f%%" % (100 * np.median(sigs), 100 * np.std(errs)))


if __name__ == "__main__":
    test_healthy_dense_seams_not_overruled()
    test_collapsed_seam_overruled()
    test_small_disagreement_kept()
    test_fallback_takes_gps_scale()
    test_unobservable_cases_leave_vision_alone()
    test_sigma_is_honest()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    raise SystemExit(1 if FAIL else 0)
