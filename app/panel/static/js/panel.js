/* ZedUploader panel — theme toggle (cookie-persisted) + mobile drawer.
   Loaded as an external file so it satisfies CSP script-src 'self'.
   No external libraries. */
(function () {
  "use strict";

  function setCookie(name, value) {
    var secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie =
      name + "=" + value + "; Path=/; Max-Age=31536000; SameSite=Lax" + secure;
  }

  // ---- Theme toggle (persists to the panel_theme cookie, read server-side) --
  var themeBtn = document.getElementById("themeBtn");
  function applyIcon(theme) {
    if (themeBtn) themeBtn.textContent = theme === "dark" ? "🌙" : "☀️";
  }
  applyIcon(document.documentElement.getAttribute("data-theme") || "dark");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var root = document.documentElement;
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      applyIcon(next);
      setCookie("panel_theme", next);
    });
  }

  // ---- Mobile drawer -------------------------------------------------------
  var burger = document.getElementById("menuBtn");
  var overlay = document.getElementById("overlay");
  function closeDrawer() { document.body.classList.remove("drawer-open"); }
  if (burger) {
    burger.addEventListener("click", function () {
      document.body.classList.toggle("drawer-open");
    });
  }
  if (overlay) overlay.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeDrawer();
  });
})();
