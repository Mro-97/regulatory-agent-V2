"""src/redis_client.py — client Redis minimal (protocole RESP), sans dépendance.

Le projet n'utilise qu'une poignée de commandes Redis : `PING`, `EXISTS`,
`DEL`, `LPUSH`, `LRANGE`, `LREM`, `INCR`, `EXPIRE`. La bibliothèque `redis`
était donc une dépendance externe pour du texte échangé sur un socket : le
protocole RESP s'encode et se décode en quelques dizaines de lignes.

Source unique de vérité de l'accès Redis : les files de validation, le
rate-limiter et le Watcher passent tous par `nouveau_client()`.

Le socket appartient à un thread dédié ; les méthodes exposées sont `async`
et reprennent le contrat de `redis.asyncio` (`await client.lpush(...)`), ce
qui évite de modifier les appelants. Chaque commande est bornée dans le temps :
un Redis figé ne bloque pas l'API.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import threading
from typing import Any

logger = logging.getLogger(__name__)


class ErreurRedisError(Exception):
    """Redis a renvoyé une erreur applicative (`-ERR ...`)."""


class RedisIndisponibleError(OSError):
    """Connexion impossible, coupée, ou délai dépassé."""


class ReponseTronqueeError(ErreurRedisError):
    """La réponse RESP est incomplète : le socket a été coupé en cours de lecture."""

    def __init__(self) -> None:  # noqa: D107 — documenté par la classe
        super().__init__("réponse Redis tronquée ou connexion fermée")


class PrefixeInconnuError(ErreurRedisError):
    """La réponse RESP commence par un type que le protocole ne définit pas."""

    def __init__(self, prefixe: bytes) -> None:  # noqa: D107 — documenté par la classe
        super().__init__(f"préfixe RESP inconnu : {prefixe!r}")
        self.prefixe = prefixe


def encoder_commande(*arguments: object) -> bytes:
    r"""Encode une commande en tableau RESP de chaînes binaires.

    Format : `*N\\r\\n` puis, pour chaque argument, `$longueur\\r\\n<octets>\\r\\n`.
    """
    morceaux = [f"*{len(arguments)}\r\n".encode()]
    for argument in arguments:
        donnee = str(argument).encode("utf-8")
        morceaux.append(f"${len(donnee)}\r\n".encode())
        morceaux.append(donnee + b"\r\n")
    return b"".join(morceaux)


def _lire_ligne(fichier: Any) -> bytes:
    """Lit une ligne RESP terminée par CRLF (CRLF exclu)."""
    ligne: bytes = fichier.readline()
    if not ligne.endswith(b"\r\n"):
        raise ReponseTronqueeError
    return ligne[:-2]


def decoder_reponse(fichier: Any) -> Any:
    """Décode une réponse RESP depuis `fichier` (objet fichier binaire).

    Gère les cinq types réellement renvoyés par les commandes utilisées :
    `+` chaîne simple, `-` erreur, `:` entier, `$` chaîne binaire, `*` tableau.
    """
    prefixe = fichier.read(1)
    if not prefixe:
        raise ReponseTronqueeError
    if prefixe == b"+":
        return _lire_ligne(fichier).decode("utf-8", "replace")
    if prefixe == b"-":
        raise ErreurRedisError(_lire_ligne(fichier).decode("utf-8", "replace"))
    if prefixe == b":":
        return int(_lire_ligne(fichier))
    if prefixe == b"$":
        taille = int(_lire_ligne(fichier))
        if taille == -1:
            return None
        return fichier.read(taille + 2)[:-2].decode("utf-8", "replace")
    if prefixe == b"*":
        nombre = int(_lire_ligne(fichier))
        if nombre == -1:
            return None
        return [decoder_reponse(fichier) for _ in range(nombre)]
    raise PrefixeInconnuError(prefixe)


# Marqueur de fin de vie du thread propriétaire du socket.
_FIN = object()


class _ConnexionRedis:
    """Connexion TCP Redis, ouverte à la demande et protégée par un verrou.

    Le socket n'est pas partagé entre plusieurs commandes simultanées : un
    verrou sérialise les échanges, ce qui correspond au protocole (une requête,
    une réponse). Ce choix remplace un thread propriétaire dédié, dont
    l'implémentation précédente laissait le thread mourir sur
    `set_result` d'un futur déjà annulé par `wait_for` — puis toute commande
    suivante restait bloquée.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        password: str,
        db: int,
        timeout_secondes: float,
    ) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._db = db
        self._timeout = timeout_secondes
        self._socket: socket.socket | None = None
        self._fichier: Any = None
        self._verrou = threading.Lock()

    def _connecter(self) -> None:
        """Ouvre le socket, s'authentifie et sélectionne la base si besoin."""
        if self._socket is not None:
            return
        sock = socket.create_connection((self._host, self._port), self._timeout)
        sock.settimeout(self._timeout)
        self._socket = sock
        self._fichier = sock.makefile("rb")
        if self._password:
            self._echanger("AUTH", self._password)
        if self._db:
            self._echanger("SELECT", self._db)

    def _fermer(self) -> None:
        """Ferme le socket (idempotent)."""
        with contextlib.suppress(OSError):
            if self._fichier is not None:
                self._fichier.close()
        with contextlib.suppress(OSError):
            if self._socket is not None:
                self._socket.close()
        self._fichier = None
        self._socket = None

    def _echanger(self, *arguments: object) -> Any:
        """Envoie une commande et lit la réponse (socket déjà ouvert)."""
        self._socket.sendall(encoder_commande(*arguments))  # type: ignore[union-attr]
        return decoder_reponse(self._fichier)

    def executer(self, *arguments: object) -> Any:
        """Exécute une commande ; retente une fois si la connexion est morte."""
        with self._verrou:
            derniere: Exception | None = None
            for _ in range(2):
                try:
                    self._connecter()
                    return self._echanger(*arguments)
                except ErreurRedisError:
                    raise
                except (OSError, RedisIndisponibleError) as exc:
                    derniere = exc
                    self._fermer()
                    logger.debug("Redis : reconnexion après %s", exc)
            raise RedisIndisponibleError(str(derniere))

    def fermer(self) -> None:
        """Ferme la connexion sous verrou."""
        with self._verrou:
            self._fermer()


class ClientRedis:
    """Client Redis asynchrone minimal — contrat de `redis.asyncio`.

    Seules les commandes utilisées par le projet sont exposées : élargir cette
    surface doit se justifier par un appelant réel (YAGNI).
    """

    def __init__(  # noqa: D107 — documenté par la classe
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 6379,
        password: str = "",
        db: int = 0,
        timeout_secondes: float = 1.0,
    ) -> None:
        self._delai = timeout_secondes
        self._connexion = _ConnexionRedis(
            host=host,
            port=port,
            password=password,
            db=db,
            timeout_secondes=timeout_secondes,
        )

    async def _commander(self, *arguments: object) -> Any:
        """Exécute la commande dans un thread et borne l'attente.

        Le socket étant bloquant, l'appel part dans le pool de threads ;
        `wait_for` garantit qu'un Redis figé ne bloque pas l'API. Le résultat
        n'est jamais perdu : le socket reste utilisable pour la commande
        suivante (la connexion n'est pas recyclée à chaque appel).
        """
        return await asyncio.wait_for(
            asyncio.to_thread(self._connexion.executer, *arguments),
            timeout=self._delai + 1.0,
        )

    # -- commandes ---------------------------------------------------------

    async def ping(self) -> bool:
        """`True` si le serveur répond."""
        return str(await self._commander("PING")).upper() == "PONG"

    async def exists(self, cle: str) -> int:
        """Nombre de clés existantes parmi celles passées."""
        return int(await self._commander("EXISTS", cle))

    async def delete(self, *cles: str) -> int:
        """Supprime les clés ; retourne le nombre réellement supprimées."""
        return int(await self._commander("DEL", *cles))

    async def lpush(self, cle: str, *valeurs: str) -> int:
        """Empile en tête de liste ; retourne la taille de la liste."""
        return int(await self._commander("LPUSH", cle, *valeurs))

    async def lrange(self, cle: str, debut: int, fin: int) -> list[str]:
        """Éléments de liste entre `debut` et `fin` (indices Redis)."""
        resultat = await self._commander("LRANGE", cle, debut, fin)
        return list(resultat or [])

    async def lrem(self, cle: str, compte: int, valeur: str) -> int:
        """Retire `compte` occurrences de `valeur` (0 = toutes)."""
        return int(await self._commander("LREM", cle, compte, valeur))

    async def incr(self, cle: str) -> int:
        """Incrémente la clé et retourne sa nouvelle valeur."""
        return int(await self._commander("INCR", cle))

    async def expire(self, cle: str, secondes: int) -> bool:
        """Pose un TTL sur la clé ; `True` si la clé existe."""
        return bool(await self._commander("EXPIRE", cle, secondes))

    async def aclose(self) -> None:
        """Ferme la connexion (contrat `redis.asyncio` conservé)."""
        await asyncio.to_thread(self._connexion.fermer)


def nouveau_client(
    *,
    host: str,
    port: int,
    password: str = "",
    db: int = 0,
    timeout_secondes: float = 1.0,
) -> ClientRedis:
    """Fabrique un client Redis (source unique de construction)."""
    return ClientRedis(
        host=host,
        port=port,
        password=password,
        db=db,
        timeout_secondes=timeout_secondes,
    )
