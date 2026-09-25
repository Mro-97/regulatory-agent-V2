"""src/api_security.py — Middlewares, dépendances et rate limiting FastAPI.

Extraits de src/api.py (§12 étape 6). Regroupe les deux middlewares HTTP
(en-têtes de sécurité, limite de taille), les dépendances FastAPI
(`verifier_auth`, `verifier_origine`, `verifier_rate_limit`) et le limiteur
de débit en mémoire.

Les en-têtes de sécurité eux-mêmes vivent dans `src/security_headers.py`,
un module feuille : le middleware de rate-limit, plus externe que cette
pile, doit pouvoir les poser sur ses 429 sans importer ce module.

Le fichier `src/api.py` importe `installer_middlewares(app)` et les
`Depends()` exposés ici — la logique métier reste dans api.py.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from config import cfg
from src.auth import Role, identifier, magasin_configure
from src.auth_session import cle_depuis_requete, verifier_csrf_si_mutation
from src.http_types import SuiteRequete
from src.net import ip_client

# `LimiteurDebit` vit dans src/rate_limit_memory.py (module feuille) :
# `rate_limit_redis` en a besoin pour son repli, et l'importer d'ici
# créait un cycle. Ré-exporté pour les appelants historiques (tests).
from src.rate_limit_memory import (
    LimiteurDebit as LimiteurDebit,
)
from src.rate_limit_memory import (
    limiteur_memoire,
)
from src.rate_limit_redis import (  # ré-export : /health/details et l API
    get_rate_limiter as get_rate_limiter,
)
from src.security_headers import appliquer_entetes_securite

if TYPE_CHECKING:
    pass

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
        call_next: SuiteRequete,
    ) -> Response:
        """Ajoute les en-têtes de sécurité à toutes les réponses."""
        reponse = await call_next(request)
        appliquer_entetes_securite(reponse)
        return reponse

    @app.middleware("http")
    async def limite_taille_requete(
        request: Request,
        call_next: SuiteRequete,
    ) -> Response:
        """Rejette 413 (body trop grand) ou 411 (Transfer-Encoding non-identity)."""
        refus = _controler_taille_et_encoding(request)
        if refus is not None:
            return refus
        return await call_next(request)


def _refus_avec_entetes(statut: int, detail: str) -> JSONResponse:
    """Fabrique la réponse de refus en la complétant des en-têtes de sécurité."""
    return appliquer_entetes_securite(
        JSONResponse(status_code=statut, content={"detail": detail})
    )


def _refus_taille(request: Request) -> JSONResponse | None:
    """413 si `Content-Length` dépasse le plafond configuré, sinon None."""
    longueur = request.headers.get("Content-Length")
    if not longueur or not longueur.isdigit():
        return None
    if int(longueur) <= cfg.taille_max_requete_octets:
        return None
    return _refus_avec_entetes(413, _MSG_REQUETE_TROP_VOLUMINEUSE)


def _refus_encoding(request: Request) -> JSONResponse | None:
    """411 si une méthode à corps porte un `Transfer-Encoding` non-identity."""
    if request.method not in _METHODES_AVEC_BODY:
        return None
    te = (request.headers.get("Transfer-Encoding") or "").strip().lower()
    if not te or te == "identity":
        return None
    return _refus_avec_entetes(411, _MSG_TRANSFER_ENCODING_REFUSE)


def _controler_taille_et_encoding(request: Request) -> JSONResponse | None:
    """Retourne un JSONResponse d'erreur si taille ou Transfer-Encoding invalide.

    Les réponses de refus sont complétées par les en-têtes de sécurité :
    produites avant le middleware `en_tetes_securite`, elles sortaient
    auparavant sans CSP ni `X-Frame-Options` (cf. `src/security_headers.py`).
    """
    return _refus_taille(request) or _refus_encoding(request)


_MSG_AUTH_ABSENTE = "Authentification non configurée."
_MSG_CLE_INVALIDE = "Clé API invalide."
_MSG_ROLE_INSUFFISANT = "Rôle insuffisant pour cette opération."


def cle_api_valide(fournie: str | None) -> bool:
    """True si `fournie` correspond à une clé du magasin (hachée, temps constant)."""
    return identifier(fournie) is not None


def role_courant(request: Request) -> Role:
    """Résout le rôle porté par l'en-tête `X-API-Key` ou le cookie de session.

    503 si aucune clé n'est configurée (fail-closed), 401 si la clé fournie
    ne correspond à aucune entrée. Mémorise `(role, label)` sur
    `request.state` pour le journal d'accès et `/whoami`.

    Quand la clé vient du cookie, la requête doit en plus porter le jeton CSRF
    (double soumission, cf. src/auth_session.py) sur les méthodes mutantes : le
    navigateur attache le cookie tout seul, le jeton prouve l'intention.
    """
    if not magasin_configure():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _MSG_AUTH_ABSENTE)
    cle, depuis_cookie = cle_depuis_requete(request)
    if depuis_cookie:
        verifier_csrf_si_mutation(request)
    resultat = identifier(cle)
    if resultat is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _MSG_CLE_INVALIDE)
    role, label = resultat
    request.state.role = role
    request.state.cle_label = label
    return role


def exige_role(minimum: Role) -> Callable[[Request], Role]:
    """Fabrique une dépendance FastAPI : clé valide ET rôle ≥ `minimum` (403 sinon)."""

    def _dep(request: Request) -> Role:
        role = role_courant(request)
        if role < minimum:
            raise HTTPException(status.HTTP_403_FORBIDDEN, _MSG_ROLE_INSUFFISANT)
        return role

    return _dep


def verifier_auth(request: Request) -> None:
    """Exige une clé API valide (n'importe quel rôle). Compat descendante."""
    role_courant(request)


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
_limiteur = limiteur_memoire()


def verifier_rate_limit(request: Request) -> None:
    """Limite le débit par IP sur les endpoints coûteux."""
    cle = ip_client(request)
    if not _limiteur.autoriser(cle):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de requêtes, réessayez plus tard.",
        )


AuthDep = Depends(verifier_auth)
OrigineDep = Depends(verifier_origine)
DebitDep = Depends(verifier_rate_limit)

# Dépendances RBAC prêtes à l'emploi (cf. src/auth.Role).
UserDep = Depends(exige_role(Role.USER))
ValidateurDep = Depends(exige_role(Role.VALIDATEUR))
AdminDep = Depends(exige_role(Role.ADMIN))
