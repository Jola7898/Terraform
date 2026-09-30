/* Ribbon: page routing (Home / RTVIO Studio) and the per-page theme (Home dark, Studio light).
   Routes are hash-based so the server still serves one page:
     #/home            landing page
     #/studio          the Studio
     #/studio/settings the Studio, with the settings panel opened and scrolled to
   Plain "#section" links marked data-scroll scroll within the current page. */
(function () {
  const root = document.documentElement, body = document.body;
  const $ = (id) => document.getElementById(id);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };

  // ---- theme: fixed per page - Home is dark, the Studio is light (no toggle).
  // The choice is also written to localStorage so the live 3D viewer (recon_viz.py's
  // page, same origin) picks the same theme.
  function applyTheme(t) {
    if (root.dataset.theme === t) return;
    root.dataset.theme = t;
    store.set("rtvioTheme", t);
    window.dispatchEvent(new Event("themechange"));
  }

  // ---- routing
  let lastView = null;
  function route() {
    const h = location.hash || "#/home";
    const studio = h.startsWith("#/studio");
    const settings = h === "#/studio/settings";
    if (!studio && !h.startsWith("#/home") && h.startsWith("#/")) { location.hash = "#/home"; return; }
    if (h.startsWith("#") && !h.startsWith("#/")) return;         // an in-page anchor, not a route
    const view = studio ? "studio" : "home";
    body.dataset.view = view;
    applyTheme(studio ? "light" : "dark");
    $("viewHome").classList.toggle("hidden", studio);
    $("viewStudio").classList.toggle("hidden", !studio);
    document.querySelectorAll(".nav a").forEach((a) => {
      const r = a.dataset.route;
      a.classList.toggle("on", r === "settings" ? settings : r === "studio" ? studio && !settings : r === "home" && !studio);
    });
    document.title = studio ? "RTVIO Studio" : "RTVIO · Real-time visual reconstruction";
    if (settings) {
      $("settingsPanel").open = true;
      requestAnimationFrame(() => $("settingsPanel").scrollIntoView({ block: "start" }));
    } else if (view !== lastView) {
      window.scrollTo(0, 0);
    }
    if (view !== lastView && studio) window.dispatchEvent(new Event("resize"));
    lastView = view;
  }
  window.addEventListener("hashchange", route);
  route();

  // in-page section links
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-scroll]");
    if (!a) return;
    e.preventDefault();
    const t = document.querySelector(a.getAttribute("href"));
    if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
  });
})();
