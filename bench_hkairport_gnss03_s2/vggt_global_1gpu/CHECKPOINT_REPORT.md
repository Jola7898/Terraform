# VGGT reconstruction report

Output frame: ENU metres about 22.4161477, 114.0426832, 164.7 m
Frames: 1353 (stride 1)
Windows: 24 x up to 64 frames, 8 shared between neighbours
VGGT: 1676.4 s = 0.8 frames/s, peak VRAM 10497 MB (Tesla T4)
Wall time: 1788.4 s for 270.4 s of capture (0.15x real time)

## Cloud (cloud_raw.ply)

- 150778679 confident pixels fused into 39371663 voxels (voxel 0.1798), 21018984 points kept (seen by >= 2 frames, outlier-filtered)
- confidence gate: percentile 50 of each window's above-floor confidence

## Camera (lens and field of view)

- lens: pinhole calibration (HK_GNSS_airport_island.yaml), 81 x 71 deg - frames undistorted to a pinhole 81 x 71 deg (fx 1445.3 px at 2448x2048) before VGGT
- VGGT's own focal estimate: fx 1561.5 px (76 deg across) = 1.08x the calibrated 1445.3 px

## Window seams (Sim(3) alignment, vision only)

- scale correction per seam: min 0.8996, median 1.3786, max 1.9889 (1.0 = VGGT kept the same scale)
- median relative residual per seam: median 0.0091, worst 0.0316
- WARNING: 1 seam(s) had too little confident overlap and fell back to single-camera chaining - look for a fast pan or a textureless view there

## Motion blur (Laplacian variance of each frame - lower = blurrier)

- per-window median: min 195, median 302, max 819 across 24 windows
- blurriest single frame seen: 68 at frame 1313

## Georeferencing

- global Sim(3) to GPS over 1353 anchors: residual median 36.18 m, 90th pct 85.91 m