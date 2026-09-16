"""src/security_headers.py — en-têtes de sécurité HTTP, posés sur TOUTE réponse.

Extrait de `src/api_security.py` pour être importable par le middleware de
rate-limit (`src/rate_limit_middleware.py`) sans créer de cycle d'imports.

Les réponses produites par un middleware qui court-circuite le reste de la
pile (`429` du rate-limit, `413`/`411` de la limite de taille) ne
traversaient pas `src.api_security._appliquer_entetes_securite`, qui est un
middleware plus interne : elles sortaient sans CSP, sans `X-Frame-Options`,
sans HSTS et sans masquage du `Server`. Ce module est la source unique des
en-têtes, appelée par chaque producteur de réponse.
"""

from __future__ import annotations

from typing import TypeVar

from starlette.responses import Response

# Le type CONCRET de la réponse est préservé (`JSONResponse` reste un
# `JSONResponse`) : les appelants qui fabriquent un refus typé
# `JSONResponse | None` ne cassent pas sous mypy --strict.
_TReponse = TypeVar("_TReponse", bound=Response)

# CSP : défense en profondeur — restreint les origines de scripts, styles,
# images et connexions du frontend. Autorise fonts Google (utilisées par le
# template index.html). `'unsafe-inline'` sur les styles reste toléré pour
# les SVG/style inline du template ; on évite `unsafe-inline` sur les scripts.
CSP_POLITIQUE = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'self'"
)

PERMISSIONS_POLICY = (
    "accelerometer=(), autoplay=(), camera=(), display-capture=(), "
    "encrypted-media=(), fullscreen=(self), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), midi=(), payment=(), usb=(), "
    "xr-spatial-tracking=()"
)

# Fingerprint : nom applicatif figé, ni version ni technologie.
NOM_SERVEUR = "regulatory-agent"


def appliquer_entetes_securite(reponse: _TReponse) -> _TReponse:
    """Pose les en-têtes de sécurité (idempotent via `setdefault`).

    Retourne `reponse` — même type concret — pour permettre l'usage en
    expression (`return appliquer_entetes_securite(JSONResponse(...))`).
    """
    reponse.headers.setdefault("X-Content-Type-Options", "nosniff")
    reponse.headers.setdefault("X-Frame-Options", "DENY")
    reponse.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    reponse.headers.setdefault(
        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
    )
    reponse.headers.setdefault("Content-Security-Policy", CSP_POLITIQUE)
    reponse.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    reponse.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    reponse.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
    reponse.headers["Server"] = NOM_SERVEUR
    return reponse
