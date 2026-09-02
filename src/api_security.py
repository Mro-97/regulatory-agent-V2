"""src/api_security.py — Middlewares, dépendances et rate limiting FastAPI.

Extraits de src/api.py (§12 étape 6). Regroupe la politique CSP,
les deux middlewares HTTP (en-têtes de sécurité, limite de taille), les
dépendances FastAPI (`verifier_auth`, `verifier_origine`,
`verifier_rate_limit`) et le limiteur de débit en mémoire.

Le fichier `src/api.py` importe `installer_middlewares(app)` et les
`Depends()` exposés ici — la logique métier reste dans api.py.
"""

from __future__ import annotations

import hmac
import time
from collections import defaultdict
from threading import Lock
from typing import TYPE_CHECKING

from config import cfg
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

if TYPE_CHECKING:
    from src.rate_limit_redis import RateLimiterRedis

_CSP_POLITIQUE = (
    # M3 : défense en profondeur — restreint les origines de scripts,
    # styles, images et connexions du frontend. Autorise fonts Google
    # (utilisées par le template index.html). `'unsafe-inline'` sur les
    # styles reste toléré pour les SVG/style inline du template ; on
    # évite `unsafe-inline` sur les scripts.
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


_METHODES_AVEC_BODY = {"POST", "PUT", "PATCH", "DELETE"}
_MSG_TRANSFER_ENCODING_REFUSE = (
    "Transfer-Encoding non autorisé — Content-Length requis."
)
_MSG_REQUETE_TROP_VOLUMINEUSE = "Requête trop volumineuse."


def installer_middlewares(app: FastAPI) -> None:
    """Attache les 2 middlewares de sécurité (en-têtes + taille) sur `app`."""

    @app.middleware("http")
    async def en_tetes_securite(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Ajoute les en-têtes de sécurité à toutes les réponses."""
        reponse = await call_next(request)
        _appliquer_entetes_securite(reponse)
        return reponse

    @app.middleware("http")
    async def limite_taille_requete(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Rejette 413 (body trop grand) ou 411 (Transfer-Encoding non-identity)."""
        refus = _controler_taille_et_encoding(request)
        if refus is not None:
            return refus
        return await call_next(request)


def _appliquer_entetes_securite(reponse: Response) -> None:
    """Pose les en-têtes de sécurité par défaut (idempotent via setdefault)."""
    reponse.headers.setdefault("X-Content-Type-Options", "nosniff")
    reponse.headers.setdefault("X-Frame-Options", "DENY")
    reponse.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    reponse.headers.setdefault(
        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
    )
    reponse.headers.setdefault("Content-Security-Policy", _CSP_POLITIQUE)
    reponse.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    reponse.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")


def _controler_taille_et_encoding(request: Request) -> JSONResponse | None:
    """Retourne un JSONResponse d'erreur si taille ou Transfer-Encoding invalide."""
    longueur = request.headers.get("Content-Length")
    if (
        longueur
        and longueur.isdigit()
        and int(longueur) > cfg.taille_max_requete_octets
    ):
        return JSONResponse(
            status_code=413, content={"detail": _MSG_REQUETE_TROP_VOLUMINEUSE}
        )
    if request.method in _METHODES_AVEC_BODY:
        te = (request.headers.get("Transfer-Encoding") or "").strip().lower()
        if te and te != "identity":
            return JSONResponse(
                status_code=411,
                content={"detail": _MSG_TRANSFER_ENCODING_REFUSE},
            )
    return None


def verifier_auth(request: Request) -> None:
    """Exige une clé API valide (fail-closed : API_KEY vide = refus).

    Un `.strip()` défensif est appliqué des deux côtés : sinon un copier-coller
    de la clé qui embarque un espace ou un retour ligne (typique quand on
    colle depuis un .env dans un prompt) tombe systématiquement en 401
    (`hmac.compare_digest` étant strict au caractère près).
    """
    attendue = (cfg.api_key or "").strip()
    if not attendue:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentification non configurée.",
        )
    fournie = request.headers.get("X-API-Key", "").strip()
    if not fournie or not hmac.compare_digest(fournie, attendue):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé API invalide.",
        )


def verifier_origine(request: Request) -> None:
    """Rejette les requêtes cross-site sur les mutations (anti-CSRF)."""
    origine = request.headers.get("Origin")
    if origine is None:
        return
    if origine in cfg.cors_origins:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Origine non autorisée.",
    )


# À terme, remplacer par Redis : `LimiteurDebit` ne sert plus que de
# fallback à `RateLimiterRedis` (src/rate_limit_redis.py) quand Redis est
# injoignable. Le comptage nominal, partagé entre workers, se fait côté
# Redis avec la clé composite `{api_key}:{ip}`.
class LimiteurDebit:
    """Limiteur de débit en mémoire (fenêtre glissante), clé = adresse IP.

    Le suivi est plafonné à `max_cles` entrées distinctes ; les entrées
    dont tous les horodatages sont hors fenêtre sont purgées à chaque
    appel. Sans ce plafond, un port-scan ou un flood d'IP sources faisait
    croître `_horodatages` indéfiniment (fuite mémoire).

    Le limiteur est un objet mono-process : en multi-worker (gunicorn -w N),
    la limite effective est × N. `main.valider_configuration_demarrage()`
    signale ce cas au boot.
    """  # noqa: RUF002 — typographie française légitime dans la docstring

    def __init__(  # noqa: D107
        self,
        max_requetes: int,
        fenetre_secondes: int,
        max_cles: int = 10_000,
    ) -> None:
        self.max_requetes = max_requetes
        self.fenetre_secondes = fenetre_secondes
        self.max_cles = max_cles
        self._horodatages: defaultdict[str, list[float]] = defaultdict(list)
        self._verrou = Lock()

    def _purger(self, borne: float) -> None:
        """Retire les clés dont tous les horodatages sont hors fenêtre."""
        obsoletes = [
            k for k, ts in self._horodatages.items() if not ts or max(ts) <= borne
        ]
        for k in obsoletes:
            del self._horodatages[k]

    def autoriser(self, cle: str) -> bool:  # noqa: D102
        maintenant = time.monotonic()
        borne = maintenant - self.fenetre_secondes
        with self._verrou:
            # Purge opportuniste quand le dictionnaire dépasse le plafond.
            if len(self._horodatages) >= self.max_cles:
                self._purger(borne)
                if (
                    len(self._horodatages) >= self.max_cles
                    and cle not in self._horodatages
                ):
                    # Toujours saturé : on refuse la nouvelle clé plutôt que
                    # de laisser croître à l'infini.
                    return False
            valeurs = [t for t in self._horodatages[cle] if t > borne]
            self._horodatages[cle] = valeurs
            if len(valeurs) >= self.max_requetes:
                return False
            valeurs.append(maintenant)
            return True


_limiteur = LimiteurDebit(
    max_requetes=cfg.rate_limit_max_requetes,
    fenetre_secondes=cfg.rate_limit_fenetre_secondes,
)


def verifier_rate_limit(request: Request) -> None:
    """Limite le débit par IP sur les endpoints coûteux."""
    cle = request.client.host if request.client else "inconnu"
    if not _limiteur.autoriser(cle):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de requêtes, réessayez plus tard.",
        )


def get_rate_limiter() -> RateLimiterRedis:
    """Retourne le rate limiter nominal (Redis, fallback mémoire intégré)."""
    from src.rate_limit_redis import get_rate_limiter as _get_rate_limiter

    return _get_rate_limiter()


AuthDep = Depends(verifier_auth)
OrigineDep = Depends(verifier_origine)
DebitDep = Depends(verifier_rate_limit)
