/* theme-init.js — applique le thème choisi AVANT le rendu du contenu.
   Chargé en tête de <body> (script bloquant, pas de defer) pour éviter
   tout flash. Externe et non "inline" : conforme à la CSP script-src 'self'. */
(function () {
  try {
    var s = localStorage.getItem("theme");
    if (["t-dim", "t-slate", "dark"].indexOf(s) > -1) {
      document.body.className = s;
    }
  } catch (e) {
    /* localStorage indisponible : on garde le thème par défaut (t-sepia). */
  }
})();
