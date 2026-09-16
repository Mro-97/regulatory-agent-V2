"""src/http_client.py — client HTTP sortant durci (SSRF, redirections bornées).

Le garde-fou SSRF (`src/http_safety.py`) validait l'URL *avant* la requête,
mais `httpx` résolvait ensuite le nom une seconde fois. Un domaine dont la
réponse DNS change entre les deux (« DNS rebinding ») passait donc le
contrôle et était contacté sur une adresse interne. Idem pour les
redirections : avec `follow_redirects=True`, un `302 Location:
http://169.254.169.254/` n'était jamais revalidé.

Deux mécanismes ici :

- `_BackendValide` : un backend réseau `httpcore` qui remplace la résolution
  DNS par celle que le garde-fou a validée. La connexion TCP part donc
  TOUJOURS vers une adresse publique contrôlée, alors que le handshake TLS
  continue d'utiliser `sni_hostname` — le SNI et la vérification du
  certificat portent donc sur le nom d'origine. (Épingler l'IP dans l'URL,
  comme le faisait une première version, cassait le TLS : `IP address
  mismatch` et `handshake failure` sur les sources réelles.)
- `ClientSortant` : suit les redirections manuellement, un saut à la fois,
  chaque cible repassant par `valider_url`, avec un nombre de sauts borné.

Aucune I/O sortante du Watcher ne doit court-circuiter `ClientSortant`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx
from httpcore._backends.auto import AutoBackend
from httpcore._backends.base import AsyncNetworkBackend, AsyncNetworkStream

from src.http_safety import UrlInterneRefuseeError, valider_url

logger = logging.getLogger(__name__)

# Nombre maximal de redirections suivies (un `302` en boucle ne doit pas
# tourner indéfiniment).
MAX_REDIRECTIONS = 5

_UA = (
    "RegulatoryAgentV2/0.1 (veille reglementaire locale; "
    "contact: admin@regulatory-agent.local)"
)


class RedirectionsExcessivesError(ValueError):
    """Trop de redirections successives : la chaîne est abandonnée."""

    def __init__(self, url_depart: str, maximum: int) -> None:  # noqa: D107 — docstring de classe
        super().__init__(
            f"plus de {maximum} redirections depuis {url_depart} — abandon."
        )
        self.url_depart = url_depart
        self.maximum = maximum


@dataclass(frozen=True)
class ReponseSortante:
    """Réponse HTTP finale (après redirections éventuelles)."""

    contenu: str
    url_finale: str
    statut: int


class _BackendValide(AsyncNetworkBackend):
    """Backend réseau qui ne se connecte qu'à des adresses validées.

    `httpcore` appelle `connect_tcp(host=…)` avec le nom d'hôte, puis effectue
    le handshake TLS sur `sni_hostname` (le nom d'origine). En ne remplaçant
    que la résolution et la connexion TCP, on obtient l'épinglage d'adresse
    sans toucher au SNI ni à la vérification du certificat.
    """

    def __init__(self, interne: AsyncNetworkBackend | None = None) -> None:
        self._interne = interne or AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> AsyncNetworkStream:
        """Se connecte à une IP validée pour `host`, jamais à sa résolution OS.

        Raises:
            UrlInterneRefuseeError: `host` vaut ou résout une adresse non
                publique (la validation est refaite ici : c'est ce qui ferme
                la fenêtre entre le contrôle et la connexion).
        """
        ips = valider_url(f"https://{host}:{port}/")
        derniere_erreur: Exception | None = None
        for ip in ips:
            try:
                return await self._interne.connect_tcp(
                    ip,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:  # noqa: BLE001 — on tente l'adresse suivante
                derniere_erreur = exc
                logger.debug("Connexion impossible à %s (pour %s) : %s", ip, host, exc)
        if derniere_erreur is not None:
            raise derniere_erreur
        raise UrlInterneRefuseeError(
            f"https://{host}:{port}/", raison="aucune adresse joignable"
        )

    async def connect_unix_socket(
        self, path: str, _timeout: float | None = None, _socket_options: Any = None
    ) -> AsyncNetworkStream:
        """Refuse les sockets Unix : hors du périmètre du Watcher.

        Un chemin de socket est un accès local arbitraire (Docker, Postgres…) :
        le Watcher ne récupère que des URLs http(s) publiques, donc tout appel
        ici est une anomalie qu'on refuse plutôt que de la relayer.
        """
        raise UrlInterneRefuseeError(path, raison="socket Unix non autorisé")

    async def sleep(self, seconds: float) -> None:
        """Délègue l'attente au backend sous-jacent."""
        await self._interne.sleep(seconds)


class _TransportValide(httpx.AsyncHTTPTransport):
    """`AsyncHTTPTransport` dont le pool de connexions utilise `_BackendValide`.

    `httpx` n'expose pas `network_backend` dans son constructeur, alors que le
    `httpcore.AsyncConnectionPool` sous-jacent l'accepte : on le remplace donc
    juste après la construction du transport, en s'appuyant sur l'attribut
    `_pool` de httpcore. Le reste du comportement (limites, keep-alive,
    HTTP/1.1, TLS) est inchangé.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        pool = getattr(self, "_pool", None)
        if pool is None:  # pragma: no cover — dépend de l'API interne de httpx
            message = (
                "httpx.AsyncHTTPTransport sans attribut `_pool` : l'épinglage "
                "d'adresse ne peut pas être installé (version de httpx "
                "incompatible)."
            )
            raise RuntimeError(message)
        pool._network_backend = _BackendValide(pool._network_backend)


class ClientSortant:
    """Client HTTP asynchrone qui ne se connecte qu'à des IP validées."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_redirections: int = MAX_REDIRECTIONS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialise le client (le transport peut être injecté en test)."""
        self.max_redirections = max_redirections
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            # Jamais de suivi automatique : chaque saut est revalidé à la main.
            follow_redirects=False,
            headers={"User-Agent": _UA},
            transport=_TransportValide(),
        )

    async def fermer(self) -> None:
        """Ferme le pool de connexions."""
        if not self._client.is_closed:
            await self._client.aclose()

    async def _une_requete(self, methode: str, url: str) -> httpx.Response:
        """Émet UNE requête vers `url` (résolution validée par le backend).

        La validation explicite est refaite ici pour deux raisons : donner un
        message clair AVANT toute I/O, et contrôler le schéma/port — le
        backend réseau ne voit que `host:port` et ne peut pas les vérifier.

        Raises:
            UrlInterneRefuseeError: schéma, port ou adresse cible refusés.
        """
        valider_url(url)
        return await self._client.request(methode, url)

    async def requete(self, methode: str, url: str) -> httpx.Response:
        """Émet `methode` sur `url` en suivant les redirections pas à pas.

        Chaque cible de redirection est validée comme l'URL initiale ; la
        chaîne est bornée à `max_redirections` sauts.

        Raises:
            UrlInterneRefuseeError: une cible (initiale ou redirigée) est refusée.
            RedirectionsExcessivesError: chaîne de redirections trop longue.
        """
        depart = url
        courante = url
        for _ in range(self.max_redirections + 1):
            reponse = await self._une_requete(methode, courante)
            if not reponse.is_redirect:
                return reponse
            location = reponse.headers.get("location")
            if not location:
                return reponse
            suivante = urljoin(courante, location)
            logger.info("Watcher — redirection %s -> %s", courante, suivante)
            courante = suivante
        raise RedirectionsExcessivesError(depart, self.max_redirections)

    async def recuperer(self, url: str) -> ReponseSortante:
        """`GET url` et retourne la réponse finale (contenu + URL effective)."""
        reponse = await self.requete("GET", url)
        reponse.raise_for_status()
        return ReponseSortante(
            contenu=reponse.text,
            url_finale=str(reponse.url),
            statut=reponse.status_code,
        )


__all__ = [
    "MAX_REDIRECTIONS",
    "ClientSortant",
    "RedirectionsExcessivesError",
    "ReponseSortante",
    "UrlInterneRefuseeError",
]
