"""src/rate_limit_middleware.py — Rate limiting HTTP en middleware Starlette.

`DebitDep` (dépendance FastAPI) s'exécute après le parsing du body : une
requête avec JSON invalide était rejetée en 422 sans jamais toucher le
compteur, ce qui permettait à un attaquant de bombarder l'endpoint sans
épuiser son quota. Ce middleware fait le comptage AVANT le routage :
chaque requête vers un endpoint sensible incrémente le compteur, même
si elle finit par échouer à la validation Pydantic.

Le limiteur mémoire (`_limiteur`) est réutilisé depuis `src.api_security`
pour garder une seule source de vérité (les tests monkey-patchent cet
attribut). En multi-worker, la limite effective reste * N — cf. le
warning déjà émis par `main.valider_configuration_demarrage()`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from fastapi import FastAPI, Request, Response
    from starlette.middleware.base import RequestResponseEndpoint

# Chemins sensibles rate-limités par IP. Les endpoints de lecture
# (/health, /pending) et les mutations validées humainement (/approve,
# /reject) restent hors quota — leur coût serveur est minime.
_CHEMINS_RATE_LIMITES: frozenset[str] = frozenset({"/ask", "/ingest"})

_MSG_TROP_DE_REQUETES = "Trop de requêtes, réessayez plus tard."


def _cle_client(request: Request) -> str:
    """Retourne l'adresse IP du client (ou 'inconnu' si non renseignée)."""
    return request.client.host if request.client else "inconnu"


def _est_rate_limite(chemin: str) -> bool:
    """True si `chemin` correspond à un endpoint sensible à rate-limiter."""
    return chemin in _CHEMINS_RATE_LIMITES


def _reponse_429() -> JSONResponse:
    """Fabrique la réponse 429 avec message clair (utilisée par le middleware)."""
    return JSONResponse(status_code=429, content={"detail": _MSG_TROP_DE_REQUETES})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Comptage par IP avant parsing du body — bloque en 429 si quota dépassé."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Incrémente le compteur puis délègue, ou renvoie 429 si épuisé."""
        if not _est_rate_limite(request.url.path):
            return await call_next(request)
        # Résolution paresseuse pour respecter le monkey-patch des tests
        # (`src.api_security._limiteur` remplacé à chaud dans certains tests).
        from src.api_security import _limiteur

        if not _limiteur.autoriser(_cle_client(request)):
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
