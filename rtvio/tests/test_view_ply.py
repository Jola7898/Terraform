"""
CPU tests for rtvio/view_ply.py's PLY reading and subsampling. numpy only.

    python tests/test_view_ply.py
"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from rtvio.view_ply import load_points, read_header  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("%s %-64s %s" % ("PASS" if ok else "FAIL", name, detail))


def _write_binary(path, xyz, rgb):
    """Same vertex layout as vggt_reconstruct's cloud_raw.ply: xyz, normals, rgb, views."""
    dt = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("nx", "<f4"), ("ny", "<f4"), ("nz", "<f4"),
                   ("red", "u1"), ("green", "u1"), ("blue", "u1"), ("views", "<f4")])
    v = np.zeros(len(xyz), dt)
    v["x"], v["y"], v["z"] = xyz.T
    v["red"], v["green"], v["blue"] = rgb.T
    v["views"] = 2
    head = ("ply\nformat binary_little_endian 1.0\ncomment synthetic\nelement vertex %d\n" % len(xyz)
            + "".join("property float %s\n" % c for c in ("x", "y", "z", "nx", "ny", "nz"))
            + "property uchar red\nproperty uchar green\nproperty uchar blue\nproperty float views\n"
            + "element face 0\nproperty list uchar int vertex_indices\nend_header\n")
    with open(path, "wb") as f:
        f.write(head.encode("ascii"))
        f.write(v.tobytes())


def test_binary_full_and_subsampled():
    rng = np.random.default_rng(0)
    xyz = rng.normal(size=(5000, 3)) * 10 + np.array([500000.0, 2480000.0, 90.0])   # UTM-sized offsets
    rgb = rng.integers(0, 256, size=(5000, 3), dtype=np.uint8)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cloud.ply")
        _write_binary(p, xyz, rgb)
        fmt, n, props, _, _ = read_header(p)
        check("header: format, count and 10 vertex properties",
              fmt == "binary_little_endian" and n == 5000 and len(props) == 10)
        pts, col, centre, total, has_rgb = load_points(p, max_points=0)
        err = np.abs(pts.astype(np.float64) + centre - xyz.astype(np.float32)).max()
        check("all points: positions round-trip after re-centring", total == 5000 and err < 1e-1,
              "max err %.2e m" % err)
        check("all points: colours kept exactly", has_rgb and np.array_equal(col, rgb))
        check("centred output is float32 near zero", pts.dtype == np.float32 and np.abs(pts.mean(0)).max() < 1e-3)
        sub, scol, c2, _, _ = load_points(p, max_points=1000)
        back = sub.astype(np.float64) + c2
        stored = xyz.astype(np.float32).astype(np.float64)          # what the file actually holds
        d_min = np.array([np.abs(stored - r).max(1).min() for r in back[:50]])
        check("subsample: 1000 points, each one from the file", len(sub) == 1000 and len(scol) == 1000
              and d_min.max() < 1e-3, "worst match %.2e m" % d_min.max())


def test_ascii_without_colour():
    xyz = np.array([[0, 0, 0], [1, 0, 5], [0, 1, 10]], float)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "a.ply")
        with open(p, "w") as f:
            f.write("ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\n"
                    "property float z\nend_header\n")
            f.writelines("%g %g %g\n" % tuple(r) for r in xyz)
        pts, col, centre, total, has_rgb = load_points(p)
        check("ascii: 3 points, no colour -> height colours",
              total == 3 and not has_rgb and col.shape == (3, 3) and col[2, 0] > col[0, 0])
        check("ascii: centre is the mean", np.allclose(centre, xyz.mean(0)))


def test_rejects_non_ply():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.ply")
        with open(p, "w") as f:
            f.write("not a ply\n")
        try:
            read_header(p)
            check("non-PLY file rejected", False)
        except ValueError:
            check("non-PLY file rejected", True)


if __name__ == "__main__":
    test_binary_full_and_subsampled()
    test_ascii_without_colour()
    test_rejects_non_ply()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    raise SystemExit(1 if FAIL else 0)
