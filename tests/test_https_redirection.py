"""tests/test_https_redirection.py — redirection http → https.

Le middleware est inactif par défaut (`FORCER_HTTPS=false`), donc la suite
générale ne l'exerce pas. Ces tests couvrent les deux branches et la
validation de l'hôte, qui protège d'une redirection ouverte.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import cfg
from src.https_redirection import _doit_rediriger, installer_redirection_https


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

    def test_health_n_est_jamais_redirige(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_port_443_omis_dans_la_cible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un 443 explicite ne doit pas apparaître dans la cible."""
        monkeypatch.setattr(cfg, "forcer_https", True)
        reponse = _client().get(
            "/une-route", headers={"Host": "exemple.test:443"}, follow_redirects=False
        )
        assert reponse.status_code == 308
        assert reponse.headers["location"] == "https://exemple.test/une-route"

    def test_port_non_standard_conserve(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un port non standard doit être conservé, sinon la cible est injoignable."""
        monkeypatch.setattr(cfg, "forcer_https", True)
        reponse = _client().get(
            "/une-route", headers={"Host": "exemple.test:8443"}, follow_redirects=False
        )
        assert reponse.status_code == 308
        assert reponse.headers["location"] == "https://exemple.test:8443/une-route"


class TestPredicatDeSchema:
    """Le schéma décide seul : X-Forwarded-Proto n'est pas cru sans proxy de confiance."""

    def _scope(self, schema: str) -> dict[str, object]:
        """Scope ASGI minimal tel que le reçoit le middleware."""
        return {
            "type": "http",
            "path": "/une-route",
            "scheme": schema,
            "headers": [],
            "server": ("testserver", 80),
        }

    def test_http_redirige(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cfg, "forcer_https", True)
        assert _doit_rediriger(self._scope("http")) is True

    def test_https_ne_redirige_pas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Déjà en https : rediriger ferait boucler le client."""
        monkeypatch.setattr(cfg, "forcer_https", True)
        assert _doit_rediriger(self._scope("https")) is False

    def test_websocket_ignore(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Seul le trafic http est concerné."""
        monkeypatch.setattr(cfg, "forcer_https", True)
        scope = self._scope("http") | {"type": "websocket"}
        assert _doit_rediriger(scope) is False
