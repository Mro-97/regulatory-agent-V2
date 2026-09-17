"""tests/test_auth_session.py — session par cookie HttpOnly et protection CSRF.

La clé API ne doit plus être lisible par le JavaScript de la page : elle part
dans un cookie `HttpOnly`. Le corollaire est le CSRF, couvert ici (double
soumission : le cookie ne suffit pas, il faut aussi le jeton recopié en
en-tête).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config import cfg
from src.api import app
from src.auth import hacher_cle
from src.auth_session import CLE_COOKIE, CLE_COOKIE_CSRF, ENTETE_CSRF

CLE = "cle-de-test-0123456789abcdef"


@pytest.fixture
def _magasin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Magasin d'une clé unique, rechargé pour le test."""
    from src.auth import recharger_magasin

    entrees = f"{hacher_cle(CLE)}:admin:test-session"
    monkeypatch.setattr(cfg, "api_keys_hachees_str", entrees)
    recharger_magasin()


def _ouvrir(client: TestClient) -> None:
    """Ouvre une session de test et vérifie que les cookies sont posés."""
    reponse = client.post("/auth/session", json={"cle": CLE})
    assert reponse.status_code == 200, reponse.text


class TestOuvertureDeSession:
    def test_cle_valide_pose_les_deux_cookies(self, _magasin: None) -> None:
        """Succès : cookie de session HttpOnly + jeton CSRF lisible."""
        client = TestClient(app)
        reponse = client.post("/auth/session", json={"cle": CLE})
        assert reponse.status_code == 200
        assert reponse.json()["role"] == "admin"
        entetes = reponse.headers.get_list("set-cookie")
        session = next(e for e in entetes if e.startswith(CLE_COOKIE + "="))
        csrf = next(e for e in entetes if e.startswith(CLE_COOKIE_CSRF + "="))
        # Le cookie d'authentification est invisible au JavaScript...
        assert "HttpOnly" in session
        # ...et le jeton CSRF doit rester lisible pour être recopié en en-tête.
        assert "HttpOnly" not in csrf
        assert "SameSite=strict" in session

    def test_cle_invalide_ne_pose_aucun_cookie(self, _magasin: None) -> None:
        """401 et aucun cookie : pas de session pour une clé inconnue."""
        reponse = TestClient(app).post("/auth/session", json={"cle": "rak_inconnue"})
        assert reponse.status_code == 401
        assert not reponse.headers.get_list("set-cookie")

    def test_cle_en_corps_jamais_en_url(self, _magasin: None) -> None:
        """La clé n'est pas un paramètre de requête (fuite par les journaux)."""
        client = TestClient(app)
        assert client.post("/auth/session?cle=" + CLE).status_code == 422


class TestRequetesAvecCookie:
    def test_get_authentifie_par_le_seul_cookie(self, _magasin: None) -> None:
        """Le cookie suffit pour authentifier une lecture (pas d'en-tête)."""
        client = TestClient(app)
        _ouvrir(client)
        reponse = client.get("/whoami")
        assert reponse.status_code == 200
        assert reponse.json()["role"] == "admin"

    def test_mutation_sans_jeton_csrf_refusee(self, _magasin: None) -> None:
        """403 : le cookie seul ne suffit pas pour une méthode mutante."""
        client = TestClient(app)
        _ouvrir(client)
        reponse = client.post(
            "/ask", json={"question": "Une question de test assez longue ?"}
        )
        assert reponse.status_code == 403

    def test_mutation_avec_jeton_csrf_valide(self, _magasin: None) -> None:
        """Avec le jeton recopié, la mutation passe la barrière CSRF.

        Le pipeline n'est pas exécuté pour autant (mode mock) : on vérifie
        seulement que le refus n'est plus un 403 CSRF.
        """
        client = TestClient(app)
        _ouvrir(client)
        jeton = client.cookies.get(CLE_COOKIE_CSRF)
        assert jeton
        reponse = client.post(
            "/ask",
            json={"question": "Une question de test assez longue ?"},
            headers={ENTETE_CSRF: jeton},
        )
        assert reponse.status_code != 403

    def test_jeton_csrf_falsifie_refuse(self, _magasin: None) -> None:
        """400/403 : un jeton qui ne correspond pas au cookie est rejeté."""
        client = TestClient(app)
        _ouvrir(client)
        reponse = client.post(
            "/ask",
            json={"question": "Une question de test assez longue ?"},
            headers={ENTETE_CSRF: "jeton-invente"},
        )
        assert reponse.status_code == 403

    def test_entete_api_key_prime_sur_le_cookie(self, _magasin: None) -> None:
        """Le chemin en-tête reste valable (scripts, supervision), sans CSRF.

        Un client non navigateur n'attache aucun cookie : la double soumission
        n'aurait pas de sens pour lui.
        """
        client = TestClient(app)
        _ouvrir(client)
        reponse = client.post(
            "/ask",
            json={"question": "Une question de test assez longue ?"},
            headers={"X-API-Key": CLE},
        )
        assert reponse.status_code != 403


class TestDeconnexion:
    def test_logout_efface_les_cookies(self, _magasin: None) -> None:
        """Après déconnexion, plus aucune authentification possible."""
        client = TestClient(app)
        _ouvrir(client)
        reponse = client.post("/auth/logout")
        assert reponse.status_code == 200
        effacements = [
            e
            for e in reponse.headers.get_list("set-cookie")
            if e.startswith(CLE_COOKIE)
        ]
        assert effacements
        assert all("Max-Age=0" in e or "max-age=0" in e for e in effacements)
