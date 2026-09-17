"""src/https_redirection.py — Redirection http → https (ASGI pur).

Actif seulement si `cfg.forcer_https` est vrai : à n'activer que derrière un
terminateur TLS (proxy inverse). Le schéma d'origine n'est lu dans
`X-Forwarded-Proto` que si le pair direct est un `trusted_proxy` — sinon un
client pourrait se prétendre déjà en https pour éviter la redirection.

Répond 308 (Permanent Redirect) : méthode et corps sont préservés par les
clients, contrairement à un 301/302. `/health` est exclu pour ne pas casser
les sondes de disponibilité qui interrogent le port en clair.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import RedirectResponse

if TYPE_CHECKING:
    from fastapi import FastAPI

    from src.http_types import ASGIApp, EnvoyerASGI, PorteeASGI, RecevoirASGI

# Caractères autorisés dans l'hôte repris d'un en-tête `Host` avant de le
# recopier dans un en-tête `Location` : sans ce filtre, un `Host` forgé
# (`evil.test/x`) construirait une redirection ouverte.
_HOTE_VALIDE = re.compile(r"^[A-Za-z0-9.\-]+$")

# Ports par défaut : les omettre évite qu'un `Host` sans port explicite
# produise une redirection vers `https://exemple:80`.
_PORTS_PAR_DEFAUT = {"http": "80", "https": "443"}


def _port_valide(brut: object) -> str | None:
    """Port utilisable dans un `Location`, sinon None (valeur écartée)."""
    valeur = str(brut or "")
    if valeur.isdigit() and 0 < int(valeur) <= 65535:
        return valeur
    return None


def _hote_demande(scope: PorteeASGI) -> str | None:
    """Hôte vu par le client, depuis l'en-tête `Host` (format validé)."""
    for nom, valeur in scope.get("headers") or ():
        if nom.lower() == b"host":
            hote = valeur.decode("latin-1").strip()
            return hote if hote and _HOTE_VALIDE.match(hote) else None
    return None


def _cible_https(scope: PorteeASGI, port: str | None) -> str | None:
    """URL https équivalente au chemin demandé, ou None si l'hôte est invalide."""
    hote = _hote_demande(scope)
    if hote is None:
        return None
    if ":" in hote:
        hote, _, port_entete = hote.partition(":")
        port = port_entete or port
    suffixe = f":{port}" if port and port != _PORTS_PAR_DEFAUT["https"] else ""
    return f"https://{hote}{suffixe}{scope.get('path', '')}"


def middleware_redirection_https(application: ASGIApp) -> ASGIApp:
    """Fabrique de middleware ASGI : redirige http → https si `forcer_https`."""

    async def rediriger(
        scope: PorteeASGI, recevoir: RecevoirASGI, envoyer: EnvoyerASGI
    ) -> None:
        cible = _cible_https(scope, _port_valide(scope.get("server", ("", None))[1]))
        # Hôte inexploitable : on sert la requête telle quelle plutôt que de
        # produire une redirection forgée.
        if not _doit_rediriger(scope) or cible is None:
            await application(scope, recevoir, envoyer)
            return
        reponse = RedirectResponse(cible, status_code=308)
        await reponse(scope, recevoir, envoyer)

    return rediriger


def _doit_rediriger(scope: PorteeASGI) -> bool:
    """True si la requête doit être renvoyée en https."""
    from config import cfg
    from src.net import schema_origine

    if scope.get("type") != "http" or not cfg.forcer_https:
        return False
    if scope.get("path") == "/health":
        return False
    # `schema_origine` ne lit X-Forwarded-Proto que derrière un proxy de
    # confiance : sinon le pair TCP direct fait foi.
    return schema_origine(Request(scope)) == "http"


def installer_redirection_https(app: FastAPI) -> None:
    """Enregistre le middleware sur une application FastAPI."""
    app.add_middleware(middleware_redirection_https)
