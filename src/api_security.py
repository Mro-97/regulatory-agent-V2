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

import time
from collections import defaultdict
from collections.abc import Callable
from threading import Lock
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from config import cfg
from src.auth import Role, identifier, magasin_configure
from src.http_types import SuiteRequete
from src.security_headers import appliquer_entetes_securite

if TYPE_CHECKING:
    from src.rate_limit_redis import RateLimiterRedis

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
    """Résout le rôle porté par l'en-tête `X-API-Key`.

    503 si aucune clé n'est configurée (fail-closed), 401 si la clé fournie
    ne correspond à aucune entrée. Mémorise `(role, label)` sur
    `request.state` pour le journal d'accès et `/whoami`.
    """
    if not magasin_configure():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _MSG_AUTH_ABSENTE)
    resultat = identifier(request.headers.get("X-API-Key"))
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
class LimiteurDebit:
    """Limiteur de débit en mémoire (fenêtre glissante).

    Deux appelants, deux découpages — c'est volontaire et documenté :

    - `RateLimiterRedis` (chemin nominal, `src/rate_limit_redis.py`) lui
      passe la clé composite `{empreinte_cle}:{ip}` : le repli mémoire
      applique alors EXACTEMENT le même découpage que Redis, y compris
      pendant une panne ;
    - `verifier_rate_limit` (dépendance héritée, non montée sur les routes)
      lui passe l'IP seule.

    Le suivi est plafonné à `max_cles` entrées distinctes ; les entrées dont
    tous les horodatages sont hors fenêtre sont purgées à chaque appel. Sans
    ce plafond, un port-scan ou un flood d'IP sources faisait croître
    `_horodatages` indéfiniment (fuite mémoire).

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
    from src.net import ip_client

    cle = ip_client(request)
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

# Dépendances RBAC prêtes à l'emploi (cf. src/auth.Role).
UserDep = Depends(exige_role(Role.USER))
ValidateurDep = Depends(exige_role(Role.VALIDATEUR))
AdminDep = Depends(exige_role(Role.ADMIN))
