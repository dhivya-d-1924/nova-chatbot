// Apply the last-used theme before first paint to avoid a flash (external file so the CSP can forbid inline scripts).
try {
  var t = localStorage.getItem("theme");
  if (t === "light") {
    document.documentElement.classList.replace("dark", "light");
    var m = document.querySelector('meta[name="theme-color"]');
    if (m) m.setAttribute("content", "#f4f6ff");
  }
} catch (e) { /* storage unavailable */ }
