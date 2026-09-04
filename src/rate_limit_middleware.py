"""src/rate_limit_middleware.py — Rate limiting HTTP en middleware Starlette.

`DebitDep` (dépendance FastAPI) s'exécute après le parsing du body : une
requête avec JSON invalide était rejetée en 422 sans jamais toucher le
compteur, ce qui permettait à un attaquant de bombarder l'endpoint sans
épuiser son quota. Ce middleware fait le comptage AVANT le routage :
chaque requête vers un endpoint sensible incrémente le compteur, même
si elle finit par échouer à la validation Pydantic.

Le comptage est délégué à `RateLimiterRedis` (src/rate_limit_redis.py),
clé composite `{api_key}:{client_ip}` partagée entre workers via Redis.
Si Redis est KO, le limiteur retombe automatiquement sur le compteur
mémoire de `src.api_security` (que les tests monkey-patchent).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from fastapi import FastAPI, Request, Response
    from starlette.middleware.base import RequestResponseEndpoint

# Endpoints rate-limités par IP : les coûteux (pipeline MLX) et ceux qui
# écrivent sur disque (/feedback). Les lectures et mutations validées
# humainement (/pending, /tache, /approve, /reject) sont peu coûteuses,
# authentifiées et pollées par l'UI — hors quota. Le comptage se fait
# AVANT l'auth et le parsing du body.
_CHEMINS_RATE_LIMITES: frozenset[str] = frozenset(
    {"/ask", "/ask/stream", "/ingest", "/feedback"}
)

_MSG_TROP_DE_REQUETES = "Trop de requêtes, réessayez plus tard."


def _cle_client(request: Request) -> str:
    """IP du client d'origine (via proxy de confiance si configuré)."""
    from src.net import ip_client

    return ip_client(request)


def _scope_cle(request: Request) -> str:
    """Portée du compteur : empreinte de la clé API si valide, sinon `invalide`.

    Sans cette normalisation, faire varier l'en-tête `X-API-Key` à chaque
    requête créait un compteur neuf à chaque fois → contournement total du
    rate-limit avant l'auth (brute-force de clé, flood de logs). Toutes les
    requêtes non authentifiées d'une IP partagent donc un seul seau.
    """
    import hashlib

    from src.api_security import cle_api_valide

    fournie = (request.headers.get("X-API-Key") or "").strip()
    if fournie and cle_api_valide(fournie):
        return hashlib.sha256(fournie.encode("utf-8")).hexdigest()[:12]
    return "invalide"


def _est_rate_limite(chemin: str) -> bool:
    """True si `chemin` est un endpoint sensible à rate-limiter."""
    return chemin in _CHEMINS_RATE_LIMITES


def _reponse_429() -> JSONResponse:
    """Fabrique la réponse 429 avec message clair (utilisée par le middleware)."""
    return JSONResponse(status_code=429, content={"detail": _MSG_TROP_DE_REQUETES})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Comptage `{api_key}:{ip}` avant parsing du body — 429 si quota dépassé."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Incrémente le compteur puis délègue, ou renvoie 429 si épuisé."""
        if not _est_rate_limite(request.url.path):
            return await call_next(request)
        # Import paresseux : évite de charger `redis.asyncio` au démarrage et
        # laisse les tests monkey-patcher `src.api_security._limiteur`.
        from src.rate_limit_redis import get_rate_limiter

        limiteur = get_rate_limiter()
        if not await limiteur.is_allowed(_scope_cle(request), _cle_client(request)):
            return _reponse_429()
        return await call_next(request)


def installer_rate_limit(app: FastAPI) -> None:
    """Attache `RateLimitMiddleware` à `app` — à appeler avant tout autre middleware.

    En Starlette, le dernier `add_middleware` s'exécute en premier ; ce
    middleware doit donc être ajouté après les autres pour être atteint
    avant eux (comptage effectué avant les checks de taille, d'en-têtes
    de sécurité et de parsing du body).
    """
    app.add_middleware(RateLimitMiddleware)
