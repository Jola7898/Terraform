"""GPS scale check for window seams (--gps-mode guided): overrules vision's seam scale only when it has failed."""
import numpy as np
import torch

from .fusion import robust_sim3, weighted_umeyama

MIN_SPREAD_M = 5.0
MIN_FIXES = 3
MAX_SCALE_SIGMA = 0.10
REJECT_RATIO = 1.25
REJECT_Z = 5.0
# The global fit must see enough of the flight before it may judge any seam: on MARS-LVIG HKairport_GNSS03 a fit
# from the first 2 windows (~24 s of slow early flight) put a healthy dense seam 10x off and cascaded to 20x scale.
MIN_WINDOWS_FOR_GLOBAL = 3
MIN_GLOBAL_SPREAD_M = 30.0
# A dense seam this consistent is a better relative-scale measurement than any GPS fit; it is never overruled.
HEALTHY_DENSE_RESID = 0.02
HEALTHY_DENSE_INLIERS = 0.5


def healthy_dense(seam):
    return (seam.get("method") == "dense-sim3" and seam.get("median_rel_residual") is not None
            and seam["median_rel_residual"] <= HEALTHY_DENSE_RESID
            and (seam.get("inlier_frac") or 0.0) >= HEALTHY_DENSE_INLIERS)


class GpsSeamGuide:
    """Running global-VGGT -> ENU Sim(3) from placed cameras, used to predict each new window's scale from GPS alone."""

    def __init__(self, frame_enu, sigma_m=3.0):
        self.enu = frame_enu
        self.sigma = float(sigma_m)
        self.G = None
        self.G_sigma_rel = None
        self.windows = 0

    def update(self, poses):
        self.windows += 1
        if self.windows < MIN_WINDOWS_FOR_GLOBAL:
            return
        ids = [i for i in poses if self.enu[i] is not None]
        if len(ids) < MIN_FIXES:
            return
        dst = np.array([self.enu[i] for i in ids], dtype=np.float64)
        if np.linalg.norm(dst.max(0) - dst.min(0)) < MIN_GLOBAL_SPREAD_M:
            return
        src = np.array([poses[i][1] for i in ids], dtype=np.float64)
        s, R, t, _ = robust_sim3(torch.from_numpy(src), torch.from_numpy(dst))
        self.G = (float(s), R.numpy(), t.numpy())
        self.G_sigma_rel = self.sigma / np.sqrt(((dst - dst.mean(0)) ** 2).sum())

    def window_scale(self, idxs, C_local):
        """(scale, relative sigma) mapping this window's local frame onto the global one, from its fixes; None if unobservable."""
        if self.G is None:
            return None
        sel = [j for j, i in enumerate(idxs) if self.enu[i] is not None]
        if len(sel) < MIN_FIXES:
            return None
        enu = np.array([self.enu[idxs[j]] for j in sel], dtype=np.float64)
        if np.linalg.norm(enu.max(0) - enu.min(0)) < MIN_SPREAD_M:
            return None
        sG, RG, tG = self.G
        y = (enu - tG) @ RG / sG
        x = np.asarray(C_local, dtype=np.float64)[sel]
        s, _, _ = weighted_umeyama(torch.from_numpy(x), torch.from_numpy(y), torch.ones(len(sel), dtype=torch.float64))
        # Umeyama's scale has std sigma / sqrt(sum |y - mean y|^2) for isotropic target noise sigma; the global
        # fit's own scale uncertainty adds in quadrature.
        sigma_rel = np.hypot(self.sigma / np.sqrt(((enu - enu.mean(0)) ** 2).sum()), self.G_sigma_rel or 0.0)
        return float(s), float(sigma_rel)

    def check(self, idxs, C_local, s_vision, seam):
        """(use_gps_scale, s_gps, sigma_rel) for one seam dict (method / median_rel_residual / inlier_frac)."""
        if healthy_dense(seam):
            return False, None, None
        est = self.window_scale(idxs, C_local)
        if est is None:
            return False, None, None
        s_gps, sig = est
        if sig > MAX_SCALE_SIGMA or s_gps <= 0:
            return False, s_gps, sig
        if seam.get("method") != "dense-sim3" or s_vision <= 0:
            return True, s_gps, sig
        dev = abs(np.log(s_vision / s_gps))
        return dev > max(np.log(REJECT_RATIO), REJECT_Z * sig), s_gps, sig
