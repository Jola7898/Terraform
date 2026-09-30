/* Home page backdrop: a slowly rotating point cloud that is being connected into a mesh.
   Wireframe solids (icosahedron, cube, octahedron) spin in perspective among drifting points;
   nearby points are linked by faint lines, and a scan line sweeps down the page lighting up
   whatever it crosses - a light-weight nod to capture -> points -> surface.
   Purely decorative: pointer-events none, paused when the tab is hidden or the Studio is showing,
   and drawn once (no animation) when the OS asks for reduced motion. */
(function () {
  const cv = document.getElementById("bgfx");
  if (!cv) return;
  const ctx = cv.getContext("2d");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let W = 0, H = 0, dpr = 1, U = 1;               // css size, device pixel ratio, unit = min(W, H)
  let acc = [122, 167, 232], acc2 = [95, 195, 181], light = false;

  function hex(c) {
    c = c.trim();
    const m = /^#([0-9a-f]{6})$/i.exec(c);
    return m ? [1, 3, 5].map((i) => parseInt(m[1].substr(i - 1, 2), 16)) : null;
  }
  function readColors() {
    const cs = getComputedStyle(document.documentElement);
    acc = hex(cs.getPropertyValue("--accent")) || acc;
    acc2 = hex(cs.getPropertyValue("--accent-2")) || acc2;
    const t = document.documentElement.dataset.theme;
    light = t ? t === "light" : matchMedia("(prefers-color-scheme: light)").matches;
  }
  // Home: full backdrop. Studio: the same scene, smaller, fewer pieces and fainter, so it stays out of the way of the tools.
  const MODES = { home: { scale: 1, shapes: 5, field: 64, alpha: 1 }, studio: { scale: 0.55, shapes: 3, field: 26, alpha: 0.6 } };
  let mode = MODES.home;
  const rgba = (c, a) => "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + (a * mode.alpha).toFixed(3) + ")";

  // ---- solids: vertices on the unit sphere-ish, edges = pairs at the minimum distance
  function solid(verts) {
    let min = Infinity;
    for (let i = 0; i < verts.length; i++) for (let j = i + 1; j < verts.length; j++) min = Math.min(min, dist3(verts[i], verts[j]));
    const edges = [];
    for (let i = 0; i < verts.length; i++) for (let j = i + 1; j < verts.length; j++) if (dist3(verts[i], verts[j]) < min * 1.05) edges.push([i, j]);
    const faces = [];
    for (let i = 0; i < verts.length; i++) for (let j = i + 1; j < verts.length; j++) for (let k = j + 1; k < verts.length; k++) {
      if (dist3(verts[i], verts[j]) < min * 1.05 && dist3(verts[j], verts[k]) < min * 1.05 && dist3(verts[i], verts[k]) < min * 1.05) faces.push([i, j, k]);
    }
    return { verts, edges, faces };
  }
  const dist3 = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
  const phi = (1 + Math.sqrt(5)) / 2;
  const ICOSA = solid([[-1, phi, 0], [1, phi, 0], [-1, -phi, 0], [1, -phi, 0], [0, -1, phi], [0, 1, phi], [0, -1, -phi], [0, 1, -phi],
    [phi, 0, -1], [phi, 0, 1], [-phi, 0, -1], [-phi, 0, 1]].map((v) => v.map((x) => x / Math.hypot(1, phi))));
  const CUBE = solid([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1], [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]].map((v) => v.map((x) => x / Math.sqrt(3))));
  const OCTA = solid([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]);

  // cx, cy are fractions of the viewport; r is a fraction of U; spin in rad/s
  const SHAPES = [
    { s: ICOSA, cx: 0.16, cy: 0.30, r: 0.20, rx: 0.10, ry: 0.16, ph: 0.0 },
    { s: CUBE, cx: 0.86, cy: 0.20, r: 0.15, rx: 0.12, ry: -0.14, ph: 1.7 },
    { s: OCTA, cx: 0.78, cy: 0.74, r: 0.17, rx: -0.09, ry: 0.13, ph: 3.1 },
    { s: ICOSA, cx: 0.10, cy: 0.82, r: 0.11, rx: 0.14, ry: -0.11, ph: 4.4 },
    { s: CUBE, cx: 0.48, cy: 0.52, r: 0.09, rx: -0.13, ry: 0.10, ph: 5.2 },
  ];
  let field = [];       // drifting cloud points, in viewport fractions + depth
  function seed() {
    let r = 1234567;
    const rnd = () => ((r = (r * 1664525 + 1013904223) >>> 0) / 4294967296);
    field = Array.from({ length: 64 }, () => ({
      x: rnd(), y: rnd(), z: rnd() * 2 - 1,
      vx: (rnd() - 0.5) * 0.010, vy: (rnd() - 0.5) * 0.008, vz: (rnd() - 0.5) * 0.02,
    }));
  }
  seed();

  function resize() {
    dpr = Math.min(devicePixelRatio || 1, 2);
    W = innerWidth; H = innerHeight; U = Math.min(W, H);
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  const D = 3.2;                                  // camera distance for the perspective divide
  function project(x, y, z, cx, cy, r) {
    const s = D / (D + z);
    return [cx + x * r * s, cy + y * r * s, s];
  }

  function frame(t) {
    ctx.clearRect(0, 0, W, H);
    const sweep = ((t / 9000) % 1.25) * (H + 200) - 100;        // scan line y, top to bottom, then a pause
    const glow = (y) => Math.exp(-Math.pow((y - sweep) / (0.06 * H + 20), 2));
    const pts = [];                                             // every projected vertex, for the connecting lines

    // solids
    for (const sh of SHAPES.slice(0, mode.shapes)) {
      const a = t / 1000 * sh.ry + sh.ph, b = t / 1000 * sh.rx + sh.ph * 0.5;
      const ca = Math.cos(a), sa = Math.sin(a), cb = Math.cos(b), sb = Math.sin(b);
      const cx = sh.cx * W, cy = sh.cy * H, r = sh.r * U * 1.6 * mode.scale;
      const P = sh.s.verts.map((v) => {
        const x1 = v[0] * ca + v[2] * sa, z1 = -v[0] * sa + v[2] * ca;        // spin about Y
        const y2 = v[1] * cb - z1 * sb, z2 = v[1] * sb + z1 * cb;             // tilt about X
        return project(x1, y2, z2, cx, cy, r);
      });
      // faint faces: the surface "filling in"
      for (const f of sh.s.faces) {
        const A = P[f[0]], B = P[f[1]], C = P[f[2]];
        const g = Math.max(glow(A[1]), glow(B[1]), glow(C[1]));
        const front = (B[0] - A[0]) * (C[1] - A[1]) - (B[1] - A[1]) * (C[0] - A[0]);
        if (front > 0 || g > 0.05) {
          ctx.fillStyle = rgba(acc2, (light ? 0.02 : 0.03) + g * (light ? 0.07 : 0.10));
          ctx.beginPath(); ctx.moveTo(A[0], A[1]); ctx.lineTo(B[0], B[1]); ctx.lineTo(C[0], C[1]); ctx.closePath(); ctx.fill();
        }
      }
      ctx.lineWidth = 1;
      for (const [i, j] of sh.s.edges) {
        const A = P[i], B = P[j], g = Math.max(glow(A[1]), glow(B[1]));
        ctx.strokeStyle = rgba(acc, (light ? 0.20 : 0.17) * ((A[2] + B[2]) / 2) + g * 0.32);
        ctx.beginPath(); ctx.moveTo(A[0], A[1]); ctx.lineTo(B[0], B[1]); ctx.stroke();
      }
      for (const p of P) pts.push(p);
    }

    // drifting cloud points
    const dt = 1 / 60;
    for (const q of field.slice(0, mode.field)) {
      q.x += q.vx * dt; q.y += q.vy * dt; q.z += q.vz * dt;
      if (q.x < -0.05) q.x = 1.05; else if (q.x > 1.05) q.x = -0.05;
      if (q.y < -0.05) q.y = 1.05; else if (q.y > 1.05) q.y = -0.05;
      if (q.z < -1 || q.z > 1) q.vz *= -1;
      const s = D / (D + q.z);
      pts.push([q.x * W, q.y * H, s]);
    }

    // connections between nearby points
    const maxD = Math.max(90, 0.16 * U * (mode === MODES.home ? 1 : 0.8));
    for (let i = 0; i < pts.length; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        const dx = pts[i][0] - pts[j][0], dy = pts[i][1] - pts[j][1];
        const d2 = dx * dx + dy * dy;
        if (d2 > maxD * maxD) continue;
        const k = 1 - Math.sqrt(d2) / maxD;
        const g = Math.max(glow(pts[i][1]), glow(pts[j][1]));
        ctx.strokeStyle = rgba(acc, k * (light ? 0.13 : 0.11) + g * 0.20 * k);
        ctx.beginPath(); ctx.moveTo(pts[i][0], pts[i][1]); ctx.lineTo(pts[j][0], pts[j][1]); ctx.stroke();
      }
    }

    // dots on top
    for (const p of pts) {
      const g = glow(p[1]);
      ctx.fillStyle = rgba(g > 0.2 ? acc2 : acc, (light ? 0.30 : 0.26) * p[2] + g * 0.45);
      ctx.beginPath(); ctx.arc(p[0], p[1], (1.1 + g * 1.8) * p[2], 0, 6.2832); ctx.fill();
    }
  }

  let raf = 0;
  function loop(t) {
    raf = requestAnimationFrame(loop);
    if (document.hidden) return;
    mode = document.body.dataset.view === "studio" ? MODES.studio : MODES.home;
    frame(t);
  }
  function start() {
    resize(); readColors();
    if (reduce) { mode = document.body.dataset.view === "studio" ? MODES.studio : MODES.home; frame(4000); return; }
    cancelAnimationFrame(raf); raf = requestAnimationFrame(loop);
  }
  addEventListener("resize", () => { resize(); if (reduce) frame(4000); });
  addEventListener("themechange", () => { readColors(); if (reduce) frame(4000); });
  addEventListener("hashchange", () => { if (reduce) requestAnimationFrame(() => { mode = document.body.dataset.view === "studio" ? MODES.studio : MODES.home; frame(4000); }); });
  start();
})();
