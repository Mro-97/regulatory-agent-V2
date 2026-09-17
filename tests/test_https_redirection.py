"""tests/test_https_redirection.py — redirection http → https.

Le middleware est inactif par défaut (`FORCER_HTTPS=false`), donc la suite
générale ne l'exerce pas. Deux niveaux de test :

- le câblage ASGI via `TestClient` (redirection effective, `/health` exclu) ;
- la construction de la cible via `_cible_https`, testée directement : le
  `TestClient` écrase l'en-tête `Host` par sa propre base URL, ce qui rend le
  cas « port non standard » intestable à travers lui.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import cfg
from src.https_redirection import (
    _cible_https,
    _doit_rediriger,
    _hote_demande,
    installer_redirection_https,
)


def _application() -> FastAPI:
    """App minimale portant le seul middleware testé, pour isoler le comportement."""
    application = FastAPI()

    @application.get("/une-route")
    def une_route() -> dict[str, str]:
        return {"ok": "oui"}

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"statut": "ok"}

    installer_redirection_https(application)
    return application


def _client() -> TestClient:
    """Client en clair — le cas que le middleware doit intercepter."""
    return TestClient(_application(), base_url="http://testserver")


def _scope(hote: bytes | None = None, port_scope: int = 80) -> dict[str, object]:
    """Scope ASGI minimal tel que le reçoit le middleware."""
    entetes = [(b"host", hote)] if hote is not None else []
    return {
        "type": "http",
        "path": "/une-route",
        "scheme": "http",
        "headers": entetes,
        "server": ("testserver", port_scope),
    }


class TestRedirectionHttps:
    def test_desactive_sert_la_route(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Faux (défaut de production) : aucune redirection, la route répond."""
        monkeypatch.setattr(cfg, "forcer_https", False)
        reponse = _client().get("/une-route")
        assert reponse.status_code == 200
        assert reponse.json() == {"ok": "oui"}

    def test_actif_redirige_en_308(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Actif : la requête http repart en https (308, méthode préservée)."""
        monkeypatch.setattr(cfg, "forcer_https", True)
        reponse = _client().get("/une-route", follow_redirects=False)
        assert reponse.status_code == 308
        assert reponse.headers["location"] == "https://testserver/une-route"

    def test_health_n_est_jamais_redirige(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Les sondes interrogent le port en clair : /health répond 200 direct."""
        monkeypatch.setattr(cfg, "forcer_https", True)
        reponse = _client().get("/health", follow_redirects=False)
        assert reponse.status_code == 200

    def test_hote_forge_ne_produit_pas_de_redirection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un `Host` hors jeu de caractères est refusé, jamais recopié.

        Sans ce filtre, `Host: evil.test/chemin` construirait un `Location`
        vers un autre domaine : redirection ouverte.
        """
        monkeypatch.setattr(cfg, "forcer_https", True)
        reponse = _client().get(
            "/une-route", headers={"Host": "evil.test/chemin"}, follow_redirects=False
        )
        assert reponse.status_code == 200
        assert "location" not in reponse.headers


class TestConstructionDeLaCible:
    """`_cible_https` : port du `Host` prioritaire, jamais de 443 explicite."""

    @pytest.mark.parametrize(
        ("hote", "port_scope", "attendu"),
        [
            (b"testserver", 80, "https://testserver/une-route"),
            (b"exemple.test:443", 80, "https://exemple.test/une-route"),
            (b"exemple.test:8443", 80, "https://exemple.test:8443/une-route"),
            # Port de test non standard : sans port dans le Host, on le reprend.
            (b"testserver", 8080, "https://testserver:8080/une-route"),
        ],
    )
    def test_cibles(self, hote: bytes, port_scope: int, attendu: str) -> None:
        scope = _scope(hote, port_scope)
        assert _cible_https(scope, str(port_scope)) == attendu

    def test_hote_invalide_refuse(self) -> None:
        """Un hôte non conforme ne doit produire aucune cible."""
        assert _cible_https(_scope(b"evil.test/chemin"), "80") is None

    def test_absence_d_hote_refusee(self) -> None:
        """Sans en-tête `Host`, on ne peut pas construire de cible fiable."""
        assert _cible_https(_scope(None), "80") is None
        assert _hote_demande(_scope(None)) is None


class TestPredicatDeSchema:
    """Le schéma décide seul.

    `X-Forwarded-Proto` n'est pas cru sans `trusted_proxies`.
    """

    def test_http_redirige(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cfg, "forcer_https", True)
        assert _doit_rediriger(_scope(b"testserver")) is True

    def test_https_ne_redirige_pas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Déjà en https : rediriger ferait boucler le client."""
        monkeypatch.setattr(cfg, "forcer_https", True)
        scope = _scope(b"testserver") | {"scheme": "https"}
        assert _doit_rediriger(scope) is False

    def test_websocket_ignore(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Seul le trafic http est concerné."""
        monkeypatch.setattr(cfg, "forcer_https", True)
        scope = _scope(b"testserver") | {"type": "websocket"}
        assert _doit_rediriger(scope) is False

    def test_health_exclu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cfg, "forcer_https", True)
        scope = _scope(b"testserver") | {"path": "/health"}
        assert _doit_rediriger(scope) is False
