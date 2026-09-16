"""
Regression + robustness test for so3.umeyama_alignment, the per-window
georeferencing fit at the heart of vggt_reconstruct.py's GPS mode.

No committed test existed for this at all before this session - the
"verified exact to float precision" claim in docs/dev_notes/HANDOFF_SESSION1.md was an ad
hoc script, not a committed test, and it only ever checked the noiseless
case. SIH26158 key challenge (v) is explicitly "GPS inaccuracies and sensor
noise"; this file adds that missing coverage: an exact regression test for
the noiseless case (formalizing what should already have been committed),
plus a Monte-Carlo sweep of realistic GPS noise levels against the
pipeline's real per-window anchor count, checking where the resulting
position error crosses the PS's <=1m spatial-accuracy target.

    python tests/test_georeference_vggt.py
"""
import numpy as np

from rtvio.so3 import umeyama_alignment

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("%s %-70s %s" % ("PASS" if ok else "FAIL", name, detail))


def _synthetic_flight(n, seed=0, radius_m=30.0, altitude_m=80.0):
    """A plausible aerial-survey camera path: n points along a gentle arc at
    ~constant altitude, a few metres apart - the shape a drone's per-window
    camera centers actually have. Deliberately NOT n random points in a
    ball: umeyama_alignment is genuinely harder (worse-conditioned) on a
    near-straight path than on a well-spread cloud, and a real flight path
    is close to a line - this is the realistic difficulty, not an easy
    case picked to make the numbers look good."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0, np.pi / 3, n)  # a 60-degree arc, not a full circle
    pts = np.stack([radius_m * np.cos(t), radius_m * np.sin(t),
                     np.full(n, altitude_m)], axis=1)
    pts[:, 2] += rng.normal(scale=0.3, size=n)  # slight altitude wobble - keeps it non-planar/non-degenerate
    return pts


def _random_similarity(seed):
    """Stand-in for 'VGGT's arbitrary per-window frame': an unrelated
    scale/rotation/translation applied to true ENU positions to produce
    src - exactly the transform umeyama_alignment has to invert."""
    rng = np.random.default_rng(seed + 1000)
    axis = rng.normal(size=3)
    axis /= np.linalg.norm(axis)
    angle = rng.uniform(0, 2 * np.pi)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    R = np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * (K @ K)
    s = rng.uniform(0.3, 3.0)  # VGGT's own scale is arbitrary, not near 1
    t = rng.normal(scale=50.0, size=3)
    return s, R, t


def _src_for(dst_true, s0, R0, t0):
    """Invert dst_true = s0 * R0 @ src + t0 to get the matching src."""
    return ((dst_true - t0) @ np.linalg.inv(R0).T) / s0


def test_umeyama_recovers_exact_transform_noiseless():
    """The regression test that should already have existed: with zero
    noise, umeyama_alignment must recover the true (s, R, t) to float
    precision, and the aligned points must land exactly on the GPS
    ('dst') positions."""
    dst_true = _synthetic_flight(n=6, seed=1)
    s0, R0, t0 = _random_similarity(seed=1)
    src = _src_for(dst_true, s0, R0, t0)

    s, R, t = umeyama_alignment(src, dst_true, with_scale=True)
    aligned = s * (R @ src.T).T + t
    err = np.linalg.norm(aligned - dst_true, axis=1)
    check("noiseless: scale recovered to 1e-8", abs(s - s0) < 1e-8,
          "s=%.6f vs true %.6f" % (s, s0))
    check("noiseless: rotation recovered to 1e-8", np.allclose(R, R0, atol=1e-8), "")
    check("noiseless: aligned points match GPS to sub-mm", err.max() < 1e-6,
          "max err %.2e m" % err.max())


def test_gps_noise_sweep_against_1m_target():
    """Monte-Carlo: how much GPS noise survives into the georeferenced cloud
    vs. SIH26158's <=1m spatial-accuracy target, as a function of how many
    anchors the similarity fit gets.

    The anchor count is the whole story here, and the pipeline's has changed.
    The old batch path fit each window separately off 3-4 anchors
    (WINDOW_FRAMES=4), which is the n=4 row below and is why the <=1m target
    looked out of reach. What ships now is ONE fit over the whole trajectory:
    vggt_reconstruct._georeference_global feeds robust_sim3 every frame that
    has a fix within 1s, so a 10-minute flight logging GPS at 1Hz supplies
    ~600 anchors, not 4. This sweep covers both regimes so the crossover is
    on the record rather than asserted from the sigma/sqrt(N) argument alone.

    Note the flight path is held at the same 60-degree, 30m-radius arc for
    every n: only the number of fixes along it changes. That isolates the
    averaging effect and is the conservative choice - a real 10-minute flight
    also spreads its anchors over far more ground, which conditions the fit
    better than anything measured here.

    Horizontal/vertical sigmas are consumer/phone-GPS-realistic (rtvioapk's
    GpsCollector reports comparable accuracy_m values) - not survey-grade
    RTK, which the PS lists only as an OPTIONAL input."""
    NOISE_LEVELS_M = [0.0, 1.0, 3.0, 5.0]   # horizontal 1-sigma
    VERTICAL_FACTOR = 2.0                    # GPS altitude is typically worse than horizontal
    N_TRIALS = 200
    ANCHOR_COUNTS = (4, 12, 32, 64, 128, 256, 512)

    print("\n     fit anchors | horiz sigma (m) | mean pos error (m) | max pos error (m) | <=1m?")
    results = {}
    for n_anchors in ANCHOR_COUNTS:
        for sigma in NOISE_LEVELS_M:
            errs = []
            for trial in range(N_TRIALS):
                dst_true = _synthetic_flight(n=n_anchors, seed=trial)
                s0, R0, t0 = _random_similarity(seed=trial)
                src = _src_for(dst_true, s0, R0, t0)
                rng = np.random.default_rng(trial + 5000 + n_anchors)
                noise = rng.normal(scale=sigma, size=(n_anchors, 3))
                noise[:, 2] *= VERTICAL_FACTOR
                dst_noisy = dst_true + noise

                s, R, t = umeyama_alignment(src, dst_noisy, with_scale=True)
                aligned = s * (R @ src.T).T + t
                err = np.linalg.norm(aligned - dst_true, axis=1)
                errs.append(err.mean())
            mean_e, max_e = float(np.mean(errs)), float(np.max(errs))
            results[(n_anchors, sigma)] = (mean_e, max_e)
            print("  %14d | %15.1f | %19.3f | %17.3f | %s"
                  % (n_anchors, sigma, mean_e, max_e, "yes" if mean_e <= 1.0 else "NO"))

    check("noiseless case has ~zero error at n=4",
          results[(4, 0.0)][0] < 0.01, "mean %.4fm" % results[(4, 0.0)][0])
    check("more anchors (n=12) reduces mean error vs n=4 at the same noise (3m)",
          results[(12, 3.0)][0] < results[(4, 3.0)][0],
          "n=4: %.3fm, n=12: %.3fm" % (results[(4, 3.0)][0], results[(12, 3.0)][0]))

    # The estimator averages noise down as sigma/sqrt(N), so quadrupling the
    # anchors should halve the error. Asserted rather than asserted-by-argument,
    # because every accuracy claim downstream of this rests on it.
    ratio = results[(128, 3.0)][0] / results[(32, 3.0)][0]
    check("error scales as sigma/sqrt(N): 4x anchors halves it (within 15%)",
          abs(ratio - 0.5) / 0.5 < 0.15,
          "n=32: %.3fm -> n=128: %.3fm (ratio %.3f, ideal 0.500)"
          % (results[(32, 3.0)][0], results[(128, 3.0)][0], ratio))

    # The <=1m target itself. Now assertable because _georeference_global fits
    # the whole trajectory at once: at 1Hz a 10-minute flight brings ~600
    # anchors, well past both thresholds below.
    check("<=1m at 1m-sigma GPS once the fit gets >=12 anchors",
          results[(12, 1.0)][0] <= 1.0, "n=12: mean %.3fm" % results[(12, 1.0)][0])
    check("<=1m at 3m-sigma GPS once the fit gets >=128 anchors",
          results[(128, 3.0)][0] <= 1.0, "n=128: mean %.3fm" % results[(128, 3.0)][0])
    check("<=1m at 5m-sigma GPS once the fit gets >=512 anchors",
          results[(512, 5.0)][0] <= 1.0, "n=512: mean %.3fm" % results[(512, 5.0)][0])

    # Kept as the contrast: this is what the removed per-window fit could do,
    # and why the <=1m target used to look unreachable.
    check("per-window baseline (n=4) recorded for contrast (informational)",
          True, "4 anchors @1m GPS: mean=%.3fm max=%.3fm" % results[(4, 1.0)])

    return results


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    sys.exit(1 if FAIL else 0)
