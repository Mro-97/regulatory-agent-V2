"""scripts/recuperer_polices.py — héberge les polices du site en local.

`web/templates/index.html` chargeait Inter et JetBrains Mono depuis
`fonts.googleapis.com`, et la CSP autorisait explicitement ces deux domaines.
Chaque visiteur envoyait donc son IP et son User-Agent à Google, pour un outil
qui se décrit comme « inférence 100 % locale, aucune donnée transmise à
l'extérieur ». Les deux polices sont sous SIL Open Font License, qui autorise la
redistribution.

Une requête PAR GRAISSE : le CSS groupé de Google mêle tous les sous-ensembles
(latin, latin-ext, cyrillique, grec…) et tenter d'isoler le bloc latin par
expression régulière est fragile — une première version n'en avait extrait
qu'une graisse sur cinq. Demander chaque graisse séparément rend l'extraction
triviale et vérifiable.

Idempotent : relancé, il ne réécrit ni les fichiers ni le bloc `@font-face`.
"""

from __future__ import annotations

import logging
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_POLICES = RACINE / "web" / "static" / "fonts"
CSS = RACINE / "web" / "static" / "style.css"

# Un User-Agent de navigateur moderne est INDISPENSABLE : sans lui, Google sert
# de l'ancien TTF au lieu du WOFF2, bien plus lourd.
AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# (nom de famille, graisses à récupérer, styles)
FAMILLES: list[tuple[str, list[str], list[str]]] = [
    ("Inter", ["300", "400", "500", "600", "700"], ["normal"]),
    ("JetBrains Mono", ["400", "500"], ["normal"]),
]

MARQUEUR = "/* Polices hébergées localement"

logger = logging.getLogger("polices")
_URL_CSS = "https://fonts.googleapis.com/css2"
_FONT_FACE = re.compile(r"@font-face\s*\{(?P<corps>[^}]*)\}", re.DOTALL)
_URL_WOFF2 = re.compile(r"url\((?P<url>https://[^)]+\.woff2)\)")
_BLOC_LATIN = re.compile(r"/\* latin \*/(.*)", re.DOTALL)


def _telecharger(url: str) -> bytes:
    """Télécharge une ressource avec un User-Agent navigateur."""
    requete = urllib.request.Request(url, headers={"User-Agent": AGENT})  # noqa: S310
    with urllib.request.urlopen(requete, timeout=30) as reponse:  # noqa: S310
        return bytes(reponse.read())


def _css_pour(famille: str, graisse: str) -> str:
    """CSS Google pour une famille et une graisse uniques."""
    parametres = urllib.parse.urlencode({"family": f"{famille}:wght@{graisse}"})
    return _telecharger(f"{_URL_CSS}?{parametres}&display=swap").decode("utf-8")


def _woff2_latin(css: str, famille: str, graisse: str) -> tuple[str, bytes]:
    """URL et contenu du WOFF2 du sous-ensemble latin, ou abandon explicite."""
    latin = _BLOC_LATIN.search(css)
    if latin is None:
        raise SystemExit(  # noqa: TRY003 — message opérateur
            f"ABANDON : sous-ensemble latin absent pour {famille} {graisse}"
        )
    bloc = _FONT_FACE.search(latin.group(1))
    if bloc is None:
        raise SystemExit(  # noqa: TRY003 — message opérateur
            f"ABANDON : @font-face absent pour {famille} {graisse}"
        )
    url = _URL_WOFF2.search(bloc.group("corps"))
    if url is None:
        raise SystemExit(  # noqa: TRY003 — message opérateur
            f"ABANDON : WOFF2 absent pour {famille} {graisse}"
        )
    return url.group("url"), _telecharger(url.group("url"))


def _declaration(famille: str, graisse: str, style: str, fichier: str) -> str:
    """Bloc `@font-face` local."""
    return (
        "@font-face {\n"
        f"  font-family: '{famille}';\n"
        f"  font-style: {style};\n"
        f"  font-weight: {graisse};\n"
        "  font-display: swap;\n"
        f"  src: url('/static/fonts/{fichier}') format('woff2');\n"
        "}"
    )


def main() -> int:
    """Télécharge les polices et préfixe `style.css` (une seule fois)."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    DOSSIER_POLICES.mkdir(parents=True, exist_ok=True)
    declarations: list[str] = []
    for famille, graisses, styles in FAMILLES:
        for graisse in graisses:
            for style in styles:
                css = _css_pour(famille, graisse)
                _, contenu = _woff2_latin(css, famille, graisse)
                fichier = f"{famille.replace(' ', '')}-{graisse}-{style}.woff2"
                destination = DOSSIER_POLICES / fichier
                destination.write_bytes(contenu)
                taille = len(contenu) // 1024
                logger.info("  %s (%s Ko)", fichier, taille)
                declarations.append(_declaration(famille, graisse, style, fichier))

    if not declarations:
        raise SystemExit(  # noqa: TRY003 — message opérateur
            "ABANDON : aucune déclaration produite"
        )

    css = CSS.read_text(encoding="utf-8")
    if MARQUEUR in css:
        logger.info("Bloc déjà présent dans style.css — rien à réécrire.")
        return 0

    entete = (
        f"{MARQUEUR} (scripts/recuperer_polices.py).\n"
        "   Avant : chargées depuis fonts.googleapis.com, ce qui transmettait\n"
        "   l'IP et l'User-Agent de chaque visiteur à Google — incohérent avec\n"
        "   la promesse « aucune donnée transmise à l'extérieur ». Licences\n"
        "   SIL Open Font License, redistribution autorisée. */\n"
        + "\n".join(declarations)
        + "\n\n"
    )
    CSS.write_text(entete + css, encoding="utf-8")
    logger.info(
        "%s déclaration(s) @font-face écrites en tête de style.css",
        len(declarations),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
