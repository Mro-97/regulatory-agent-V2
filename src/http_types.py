"""src/http_types.py — contrats de types partagés par la couche HTTP.

Les middlewares Starlette reçoivent un `call_next` dont le type n'est pas
ré-exporté par FastAPI. Plutôt que d'importer les internes de Starlette
(`starlette.middleware.base.RequestResponseEndpoint`), ce module décrit le
CONTRAT réellement utilisé : appeler la suite de la pile et obtenir une
réponse. La dépendance de type reste ainsi exprimée par ce qu'on utilise, pas
par un chemin d'implémentation (moindre surprise, loi de Déméter).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Protocol

from fastapi import Request, Response


class SuiteRequete(Protocol):
    """Appelle la suite de la pile de middlewares.

    Équivalent structurel du `call_next` de Starlette : la signature est
    identique (positional-only), donc toute implémentation Starlette satisfait
    ce protocole — mypy vérifie la compatibilité sans import croisé.
    """

    def __call__(self, request: Request, /) -> Awaitable[Response]:
        """Rend la réponse produite par la suite de la pile."""
        ...


# Contrats ASGI (PEP 3333 côté serveur) : une application est un callable
# asynchrone, le reste de la pile lui passe le scope, le canal de réception et
# le canal d'envoi. Utilisés par le middleware de rate-limit écrit en ASGI pur.
PorteeASGI = dict[str, Any]
# Canaux ASGI : `receive` rend un message, `send` en consomme un.
MessageASGI = MutableMapping[str, Any]
RecevoirASGI = Callable[[], Awaitable[MessageASGI]]
EnvoyerASGI = Callable[[MessageASGI], Awaitable[None]]
ASGIApp = Callable[[PorteeASGI, RecevoirASGI, EnvoyerASGI], Awaitable[None]]
