"""src/auth_session.py — Session par cookie HttpOnly, protection CSRF.

Pourquoi ce module existe
-------------------------
L'interface web conservait la clé API en `sessionStorage`, donc lisible en
clair dans DevTools et par tout script de la page. Le cookie `HttpOnly` ferme
cette exposition : le navigateur l'envoie automatiquement, et JavaScript ne
peut jamais le lire.

Le prix à payer est CSRF : le navigateur attache le cookie à toute requête
vers l'origine, y compris déclenchée par un site tiers. La parade est la
« double soumission » — un second cookie, LISIBLE par le JS de la page, doit
être recopié dans l'en-tête `X-CSRF-Token`. Un site tiers peut faire partir le
cookie d'authentification, mais il ne peut pas lire le jeton CSRF pour le
recopier : la requête est refusée.

La session est sans état : le cookie porte la clé API, revalidée à chaque
requête contre le magasin (`src/auth`). Aucune session n'est créée ni stockée
côté serveur, et une clé révoquée cesse donc d'être acceptée immédiatement,
sans attendre l'expiration du cookie.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from src.auth import Role, identifier, magasin_configure

if TYPE_CHECKING:
    from fastapi import Request, Response

CLE_COOKIE = "ra_cle"
CLE_COOKIE_CSRF = "ra_csrf"
ENTETE_CSRF = "X-CSRF-Token"
METHODES_MUTANTES = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Durée de vie du cookie : une journée de travail. Le jeton CSRF suit la même
# durée, sinon une page restée ouverte perdrait sa capacité de mutation.
DUREE_SESSION_SECONDES = 12 * 3600

_MSG_AUTH_ABSENTE = "Authentification non configurée."
_MSG_CLE_INVALIDE = "Clé API invalide."
_MSG_CSRF_ABSENT = "Jeton CSRF manquant."
_MSG_CSRF_INVALIDE = "Jeton CSRF invalide."


@dataclass(frozen=True)
class SessionOuverte:
    """Résultat d'une ouverture de session : clé validée + jeton CSRF."""

    cle: str
    role: Role
    label: str
    jeton_csrf: str


def _en_clair(request: Request) -> bool:
    """True si la requête est arrivée en clair (pas de TLS vu par le serveur).

    `secure` suit le schéma réellement vu : inutile (et bloquant) en clair sur
    127.0.0.1, indispensable derrière un terminateur TLS.
    """
    return request.url.scheme != "https"


def poser_cookies(reponse: Response, request: Request, session: SessionOuverte) -> None:
    """Pose le cookie de session (HttpOnly) puis le jeton CSRF (lisible).

    Les deux cookies sont en `samesite="strict"` : l'interface est servie par
    la même origine que l'API, aucun flux cross-site légitime n'en a besoin. Le
    jeton CSRF n'est pas un secret — il ne protège que tant qu'une autre origine
    ne peut pas le lire.
    """
    en_clair = _en_clair(request)
    reponse.set_cookie(
        CLE_COOKIE,
        session.cle,
        path="/",
        max_age=DUREE_SESSION_SECONDES,
        samesite="strict",
        secure=not en_clair,
        httponly=True,
    )
    reponse.set_cookie(
        CLE_COOKIE_CSRF,
        session.jeton_csrf,
        path="/",
        max_age=DUREE_SESSION_SECONDES,
        samesite="strict",
        secure=not en_clair,
        httponly=False,
    )


def effacer_cookies(reponse: Response) -> None:
    """Retire les deux cookies (déconnexion)."""
    reponse.delete_cookie(CLE_COOKIE, path="/")
    reponse.delete_cookie(CLE_COOKIE_CSRF, path="/")


def ouvrir_session(cle_fournie: str | None) -> SessionOuverte:
    """Valide `cle_fournie` et prépare la session (503 / 401 en cas d'échec)."""
    if not magasin_configure():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _MSG_AUTH_ABSENTE)
    resultat = identifier(cle_fournie)
    if resultat is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _MSG_CLE_INVALIDE)
    role, label = resultat
    return SessionOuverte(
        cle=(cle_fournie or "").strip(),
        role=role,
        label=label,
        jeton_csrf=secrets.token_urlsafe(32),
    )


def cle_depuis_requete(request: Request) -> tuple[str | None, bool]:
    """Clé API et provenance : `(clé, True)` quand elle vient du cookie.

    L'en-tête `X-API-Key` prime : il reste le chemin des clients non
    navigateur (scripts, supervision), pour lesquels le CSRF n'a pas de sens.

    Sans en-tête NI cookie, la provenance n'est pas « cookie » : sinon une
    requête totalement anonyme déclenchait la vérification CSRF et recevait
    403 au lieu du 401 attendu (régression détectée par la suite existante).
    """
    entete = request.headers.get("X-API-Key")
    if entete:
        return entete, False
    du_cookie = request.cookies.get(CLE_COOKIE)
    return (du_cookie, True) if du_cookie else (None, False)


def verifier_csrf(request: Request) -> None:
    """Double soumission : l'en-tête doit égaler le cookie, en temps constant."""
    attendu = request.cookies.get(CLE_COOKIE_CSRF)
    if not attendu:
        raise HTTPException(status.HTTP_403_FORBIDDEN, _MSG_CSRF_ABSENT)
    if not hmac.compare_digest(attendu, request.headers.get(ENTETE_CSRF, "")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, _MSG_CSRF_INVALIDE)


def verifier_csrf_si_mutation(request: Request) -> None:
    """Vérifie le jeton CSRF sur les seules méthodes qui modifient l'état."""
    if request.method in METHODES_MUTANTES:
        verifier_csrf(request)
