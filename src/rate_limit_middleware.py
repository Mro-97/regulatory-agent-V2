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

from fastapi import Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from fastapi import FastAPI

    from src.http_types import (
        ASGIApp,
        EnvoyerASGI,
        PorteeASGI,
        RecevoirASGI,
    )

# Endpoints rate-limités par IP : les coûteux (pipeline MLX) et ceux qui
# écrivent sur disque (/feedback). Les lectures et mutations validées
# humainement (/pending, /tache, /approve, /reject) sont peu coûteuses,
# authentifiées et pollées par l'UI — hors quota. Le comptage se fait
# AVANT l'auth et le parsing du body.
_CHEMINS_RATE_LIMITES: frozenset[str] = frozenset(
    {"/ask", "/ask/stream", "/ingest", "/feedback", "/auth/session"}
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
    """Fabrique la réponse 429 avec message clair (utilisée par le middleware).

    Les en-têtes de sécurité sont posés ici : ce middleware est le plus
    externe de la pile, donc sa réponse ne traverse jamais le middleware
    `en_tetes_securite` (plus interne) et sortait sans CSP, sans
    `X-Frame-Options` ni masquage du `Server`.
    """
    from src.security_headers import appliquer_entetes_securite

    return appliquer_entetes_securite(
        JSONResponse(status_code=429, content={"detail": _MSG_TROP_DE_REQUETES})
    )


def middleware_rate_limit(
    application: ASGIApp,
) -> ASGIApp:
    """Middleware ASGI pur : compte le quota AVANT le parsing du corps.

    Écrit sous forme de fabrique ASGI plutôt qu'en héritant de
    `BaseHTTPMiddleware` : c'est la forme standard, elle évite d'importer les
    internes de Starlette, et elle ne fait rien d'autre que ce qui est décrit
    ici (moindre surprise). Le `scope` est transmis tel quel à la suite de la
    pile, seuls les chemins rate-limités sont interceptés.
    """

    async def _middleware(
        portee: PorteeASGI, receive: RecevoirASGI, send: EnvoyerASGI
    ) -> None:
        if portee["type"] != "http" or not _est_rate_limite(portee.get("path", "")):
            await application(portee, receive, send)
            return
        # Import paresseux : laisse les tests monkey-patcher le limiteur.
        from src.rate_limit_redis import get_rate_limiter

        requete = Request(portee, receive)
        limiteur = get_rate_limiter()
        if await limiteur.is_allowed(_scope_cle(requete), _cle_client(requete)):
            await application(portee, receive, send)
            return
        await _reponse_429()(portee, receive, send)

    return _middleware


def installer_rate_limit(app: FastAPI) -> None:
    """Attache `RateLimitMiddleware` à `app` — à appeler en DERNIER.

    En Starlette, les middlewares s'exécutent dans l'ordre inverse de leur
    ajout : le dernier ajouté est le plus EXTERNE. Ce middleware doit donc
    être enregistré après les autres pour être atteint en premier — c'est ce
    qui fait compter le quota avant les contrôles de taille, d'en-têtes et le
    parsing du body. Ses refus (429) portent leurs propres en-têtes de
    sécurité, puisqu'ils ne traversent pas le middleware d'en-têtes.
    """
    app.add_middleware(middleware_rate_limit)
