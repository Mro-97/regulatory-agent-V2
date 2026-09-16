"""tests/test_http_client_ssrf.py — épinglage d'adresse et redirections (H4).

Deux défauts corrigés ici :

- `httpx` résolvait le nom une SECONDE fois après le contrôle SSRF (« DNS
  rebinding ») : `_BackendValide` remplace la résolution par celle qui a été
  validée, tout en laissant le SNI et la vérification du certificat sur le nom
  d'origine ;
- `follow_redirects=True` faisait suivre un `302 Location:
  http://169.254.169.254/…` sans contrôle : les sauts sont maintenant suivis
  un par un, chacun revalidé, et la chaîne est bornée.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import pytest

from src.http_client import (
    MAX_REDIRECTIONS,
    ClientSortant,
    RedirectionsExcessivesError,
    _BackendValide,
)
from src.http_safety import UrlInterneRefuseeError


class SequenceEpuiseeError(RuntimeError):
    """La séquence de réponses simulées est épuisée (erreur de test)."""


def _sequence_epuisee() -> None:
    """Lève l'erreur de séquence épuisée (hors d'un `raise` littéral)."""
    raise SequenceEpuiseeError


class _TransportMock(httpx.AsyncBaseTransport):
    """Transport httpx simulé : enregistre chaque requête, rend une réponse."""

    def __init__(self, reponses: list[httpx.Response]) -> None:
        self.reponses = list(reponses)
        self.requetes: list[tuple[str, str]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requetes.append((request.method, str(request.url)))
        if not self.reponses:
            _sequence_epuisee()
        return self.reponses.pop(0)


def _reponse(url: str, statut: int, *, location: str | None = None) -> httpx.Response:
    """Fabrique une réponse httpx complète (requise par `raise_for_status`)."""
    entetes = {"location": location} if location else {}
    return httpx.Response(
        status_code=statut,
        headers=entetes,
        text="corps",
        request=httpx.Request("GET", url),
    )


def _client(reponses: list[httpx.Response]) -> tuple[ClientSortant, _TransportMock]:
    """Client dont le transport HTTP est simulé (aucune I/O réseau)."""
    transport = _TransportMock(reponses)
    return ClientSortant(client=httpx.AsyncClient(transport=transport)), transport


class _BackendInterne:
    """Backend sous-jacent simulé : enregistre les connexions demandées."""

    def __init__(self) -> None:
        self.connexions: list[tuple[str, int]] = []

    async def connect_tcp(
        self,
        hote: str,
        port: int,
        timeout: float | None = None,  # noqa: ARG002 — imposé par le protocole httpcore
        local_address: str | None = None,  # noqa: ARG002 — imposé par le protocole httpcore
        socket_options: object = None,  # noqa: ARG002 — imposé par le protocole httpcore
    ) -> str:
        self.connexions.append((hote, port))
        return "flux"

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ARG002 — imposé par le protocole httpcore
        socket_options: object = None,  # noqa: ARG002 — imposé par le protocole httpcore
    ) -> str:
        return path

    async def sleep(self, seconds: float) -> None:  # noqa: ARG002 — imposé par le protocole httpcore
        return None


class TestBackendValide:
    """Le backend réseau ne doit ouvrir de connexion que vers une IP validée."""

    def test_connexion_va_sur_lip_validee(self) -> None:
        """`connect_tcp(host)` se connecte à l'IP validée, jamais au nom."""
        interne = _BackendInterne()
        backend = _BackendValide(interne)  # type: ignore[arg-type]

        async def _run() -> None:
            with patch("src.http_client.valider_url", return_value=["93.184.216.34"]):
                flux = await backend.connect_tcp("exemple.test", 443)
            assert flux == "flux"

        asyncio.run(_run())
        assert interne.connexions == [("93.184.216.34", 443)]

    def test_adresse_interne_refusee_sans_connexion(self) -> None:
        """Une résolution interne fait échouer la connexion, sans aucune I/O."""
        interne = _BackendInterne()
        backend = _BackendValide(interne)  # type: ignore[arg-type]

        async def _run() -> None:
            with (
                patch(
                    "src.http_client.valider_url",
                    side_effect=UrlInterneRefuseeError(
                        "https://interne.test/", raison="adresse non publique"
                    ),
                ),
                pytest.raises(UrlInterneRefuseeError),
            ):
                await backend.connect_tcp("interne.test", 443)

        asyncio.run(_run())
        assert interne.connexions == []

    def test_premiere_ip_injoignable_seconde_essayee(self) -> None:
        """Une IP validée mais injoignable (IPv6 sans route) n'abandonne pas."""

        class _InterneVariable(_BackendInterne):
            async def connect_tcp(
                self,
                hote: str,
                port: int,
                timeout: float | None = None,  # noqa: ARG002 — imposé par le protocole httpcore
                local_address: str | None = None,  # noqa: ARG002 — imposé par le protocole httpcore
                socket_options: object = None,  # noqa: ARG002 — imposé par le protocole httpcore
            ) -> str:
                self.connexions.append((hote, port))
                if hote == "2606:4700::1111":
                    raise OSError("pas de route IPv6")  # noqa: TRY003 — cause simulée
                return "flux"

        interne = _InterneVariable()
        backend = _BackendValide(interne)  # type: ignore[arg-type]

        async def _run() -> None:
            with patch(
                "src.http_client.valider_url",
                return_value=["2606:4700::1111", "93.184.216.34"],
            ):
                flux = await backend.connect_tcp("exemple.test", 443)
            assert flux == "flux"

        asyncio.run(_run())
        assert interne.connexions == [("2606:4700::1111", 443), ("93.184.216.34", 443)]

    def test_socket_unix_refuse(self) -> None:
        """Un socket Unix sort du périmètre du Watcher : refus explicite."""
        backend = _BackendValide()

        async def _run() -> None:
            with pytest.raises(UrlInterneRefuseeError, match="Unix"):
                await backend.connect_unix_socket("/var/run/docker.sock")

        asyncio.run(_run())


class TestRedirectionsRevalidees:
    def test_redirection_vers_ip_interne_refusee(self) -> None:
        """Un 302 vers la metadata cloud est refusé avant d'être suivi."""
        client, transport = _client(
            [
                _reponse(
                    "https://public.example/", 302, location="http://169.254.169.254/"
                )
            ]
        )
        appels_valider: list[str] = []

        def _valider(url: str) -> list[str]:
            appels_valider.append(url)
            if "169.254.169.254" in url:
                raise UrlInterneRefuseeError(url, raison="adresse non publique")
            return ["93.184.216.34"]

        async def _run() -> None:
            with (
                patch("src.http_client.valider_url", side_effect=_valider),
                pytest.raises(UrlInterneRefuseeError),
            ):
                await client.requete("GET", "https://public.example/")

        asyncio.run(_run())
        # La cible de la redirection a bien été revalidée, et aucune requête
        # n'est partie vers l'adresse interne.
        assert appels_valider == [
            "https://public.example/",
            "http://169.254.169.254/",
        ]
        assert len(transport.requetes) == 1

    def test_redirection_relative_suivie(self) -> None:
        """Une redirection relative est résolue puis suivie."""
        client, transport = _client(
            [
                _reponse("https://public.example/a", 301, location="/b"),
                _reponse("https://public.example/b", 200),
            ]
        )

        async def _run() -> None:
            with patch("src.http_client.valider_url", return_value=["93.184.216.34"]):
                reponse = await client.requete("GET", "https://public.example/a")
            assert reponse.status_code == 200

        asyncio.run(_run())
        assert [r[1] for r in transport.requetes] == [
            "https://public.example/a",
            "https://public.example/b",
        ]

    def test_chaine_trop_longue_refusee(self) -> None:
        """Une boucle de redirections est bornée par `MAX_REDIRECTIONS`."""
        redirections = [
            _reponse(f"https://public.example/{i}", 302, location=f"/{i + 1}")
            for i in range(MAX_REDIRECTIONS + 2)
        ]
        client, _ = _client(redirections)

        async def _run() -> None:
            with (
                patch("src.http_client.valider_url", return_value=["93.184.216.34"]),
                pytest.raises(RedirectionsExcessivesError),
            ):
                await client.requete("GET", "https://public.example/0")

        asyncio.run(_run())

    def test_redirection_sans_location_retournee(self) -> None:
        """Un 302 sans `Location` est retourné tel quel (pas de boucle)."""
        client, _ = _client([_reponse("https://public.example/", 302)])

        async def _run() -> None:
            with patch("src.http_client.valider_url", return_value=["93.184.216.34"]):
                reponse = await client.requete("GET", "https://public.example/")
            assert reponse.status_code == 302

        asyncio.run(_run())

    def test_recuperer_rend_le_contenu_final(self) -> None:
        """`recuperer` rend le corps et le statut après redirection."""
        client, _ = _client(
            [
                _reponse("https://public.example/a", 302, location="/b"),
                _reponse("https://public.example/b", 200),
            ]
        )

        async def _run() -> None:
            with patch("src.http_client.valider_url", return_value=["93.184.216.34"]):
                resultat = await client.recuperer("https://public.example/a")
            assert resultat.contenu == "corps"
            assert resultat.statut == 200

        asyncio.run(_run())

    def test_url_refusee_avant_toute_requete(self) -> None:
        """Une URL refusée (adresse interne) ne produit aucune requête."""
        client, transport = _client([])

        async def _run() -> None:
            with pytest.raises(UrlInterneRefuseeError):
                await client.requete("GET", "http://127.0.0.1/")

        asyncio.run(_run())
        assert transport.requetes == []
