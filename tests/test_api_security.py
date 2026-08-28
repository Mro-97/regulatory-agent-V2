"""
tests/test_api_security.py — Tests de sécurité de l'API
=========================================================

Couvre :
- Authentification : 401 sans clé, 503 si clé non configurée.
- CORS : origines non autorisées refusées.
- Anti-CSRF : en-tête Origin cross-site refusé sur les mutations.
- Rate limiting : 429 au-delà du quota.
- Limite de taille : 413 pour un corps trop volumineux.
- Masquage des erreurs : jamais de détails internes au client.
- Schémas : /docs désactivé par défaut, question trop longue rejetée.
- En-têtes de sécurité présents.
"""

from __future__ import annotations

import json
import uuid

import pytest
from config import cfg
from fastapi.testclient import TestClient
from src import api as api_module
from src.api import LimiteurDebit

CLE = "cle-de-test-0123456789abcdef"


class TestTransferEncoding:
    """H1 : rejeter Transfer-Encoding non-identity sur les mutations."""

    def test_chunked_sur_ask_refuse(self, client):  # noqa: ANN001, ANN201
        rep = client.post(
            "/ask",
            headers={
                "X-API-Key": cfg.api_key or CLE,
                "Content-Type": "application/json",
                "Transfer-Encoding": "chunked",
            },
            content=b'{"question":"test"}',
        )
        assert rep.status_code == 411, rep.text
        assert "Transfer-Encoding" in rep.json()["detail"]

    def test_get_health_avec_TE_ignore(self, client):  # noqa: ANN001, ANN201
        # GET n'est pas concerné par la protection (pas de body attendu).
        rep = client.get("/health", headers={"Transfer-Encoding": "chunked"})
        assert rep.status_code == 200


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(api_module.app)


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------


class TestAuthentification:
    def test_health_public(self, client):  # noqa: ANN001, ANN201
        rep = client.get("/health")
        assert rep.status_code == 200
        assert rep.json()["statut"] == "ok"

    def test_health_ne_fuit_ni_app_ni_version(self, client):  # noqa: ANN001, ANN201
        """M4 : /health public — ne doit exposer ni le nom ni la version
        de l'application (fingerprinting)."""
        donnees = client.get("/health").json()
        assert "app" not in donnees
        assert "version" not in donnees

    def test_interface_web_ne_fuit_pas_la_cle_api(self, client):  # noqa: ANN001, ANN201
        """C1 : la page HTML publique / ne doit JAMAIS embarquer la clé API.
        Elle est saisie côté navigateur et stockée en sessionStorage.
        Voir web/static/app.js — fonction _obtenirCle()."""
        rep = client.get("/")
        assert rep.status_code == 200
        assert "__API_KEY__" not in rep.text
        assert "{{ api_key }}" not in rep.text
        if cfg.api_key:
            assert cfg.api_key not in rep.text

    def test_ask_sans_cle_refuse(self, client):  # noqa: ANN001, ANN201
        rep = client.post("/ask", json={"question": "Obligations RGPD ?"})
        assert rep.status_code == 401

    def test_ask_cle_invalide_refuse(self, client):  # noqa: ANN001, ANN201
        rep = client.post(
            "/ask",
            json={"question": "Obligations RGPD ?"},
            headers={"X-API-Key": "mauvaise-cle"},
        )
        assert rep.status_code == 401

    def test_pending_sans_cle_refuse(self, client):  # noqa: ANN001, ANN201
        assert client.get("/pending").status_code == 401

    def test_approve_sans_cle_refuse(self, client):  # noqa: ANN001, ANN201
        rep = client.post("/approve", json={"tache_id": str(uuid.uuid4())})
        assert rep.status_code == 401

    def test_reject_sans_cle_refuse(self, client):  # noqa: ANN001, ANN201
        rep = client.post("/reject", json={"tache_id": str(uuid.uuid4())})
        assert rep.status_code == 401

    def test_ingest_sans_cle_refuse(self, client):  # noqa: ANN001, ANN201
        rep = client.post(
            "/ingest", json={"source": "EUR-Lex", "url": "https://example.org"}
        )
        assert rep.status_code == 401

    def test_sans_cle_configuree_service_indisponible(self, client, monkeypatch):  # noqa: ANN001, ANN201
        monkeypatch.setattr(cfg, "api_key", "")
        rep = client.post(
            "/ask",
            json={"question": "Obligations RGPD ?"},
            headers={"X-API-Key": "nimporte-quoi"},
        )
        assert rep.status_code == 503


# ---------------------------------------------------------------------------
# Accès autorisé
# ---------------------------------------------------------------------------


class TestAccesAutorise:
    def test_ask_avec_cle_ok(self, client):  # noqa: ANN001, ANN201
        rep = client.post(
            "/ask",
            json={"question": "Obligations RGPD ?"},
            headers={"X-API-Key": CLE},
        )
        assert rep.status_code == 200
        donnees = rep.json()
        assert donnees["niveau_confiance"] is not None
        assert "mock" in donnees["reponse"].lower()

    def test_ingest_avec_cle_ok(self, client):  # noqa: ANN001, ANN201
        rep = client.post(
            "/ingest",
            json={
                "source": "EUR-Lex",
                "url": "https://eur-lex.europa.eu/example",
                "contenu_json": {"id": "EXEMPLE_2024_1"},
            },
            headers={"X-API-Key": CLE},
        )
        assert rep.status_code == 202


# ---------------------------------------------------------------------------
# CORS et anti-CSRF
# ---------------------------------------------------------------------------


class TestCorsEtOrigine:
    def test_origine_autorisee(self, client):  # noqa: ANN001, ANN201
        rep = client.post(
            "/ask",
            json={"question": "Obligations RGPD ?"},
            headers={"X-API-Key": CLE, "Origin": "http://testserver"},
        )
        assert rep.status_code == 200
        assert rep.headers.get("access-control-allow-origin") == "http://testserver"

    def test_origine_non_autorisee_aucun_header_cors(self, client):  # noqa: ANN001, ANN201
        rep = client.get("/health", headers={"Origin": "https://evil.example"})
        assert rep.headers.get("access-control-allow-origin") is None

    def test_mutation_origine_cross_site_refusee(self, client):  # noqa: ANN001, ANN201
        rep = client.post(
            "/ask",
            json={"question": "Obligations RGPD ?"},
            headers={"X-API-Key": CLE, "Origin": "https://evil.example"},
        )
        assert rep.status_code == 403


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_debit_depasse_429(self, client):  # noqa: ANN001, ANN201
        original = api_module._limiteur
        try:
            api_module._limiteur = LimiteurDebit(max_requetes=2, fenetre_secondes=60)
            premier = client.post(
                "/ask",
                json={"question": "Question rate limit ?"},
                headers={"X-API-Key": CLE},
            )
            second = client.post(
                "/ask",
                json={"question": "Question rate limit 2 ?"},
                headers={"X-API-Key": CLE},
            )
            troisieme = client.post(
                "/ask",
                json={"question": "Question rate limit 3 ?"},
                headers={"X-API-Key": CLE},
            )
            assert premier.status_code == 200
            assert second.status_code == 200
            assert troisieme.status_code == 429
        finally:
            api_module._limiteur = original

    def test_limiteur_unitaire(self):  # noqa: ANN201
        limiteur = LimiteurDebit(max_requetes=3, fenetre_secondes=60)
        assert limiteur.autoriser("ip-a")
        assert limiteur.autoriser("ip-a")
        assert limiteur.autoriser("ip-a")
        assert not limiteur.autoriser("ip-a")
        # Une autre IP n'est pas affectée
        assert limiteur.autoriser("ip-b")


# ---------------------------------------------------------------------------
# Limites de taille et schémas
# ---------------------------------------------------------------------------


class TestLimitesEtSchemas:
    def test_requete_trop_volumineuse_413(self, client):  # noqa: ANN001, ANN201
        gros_payload = "a" * (cfg.taille_max_requete_octets + 1000)
        rep = client.post(
            "/ask",
            content=json.dumps({"question": gros_payload}),
            headers={"Content-Type": "application/json", "X-API-Key": CLE},
        )
        assert rep.status_code == 413

    def test_question_trop_longue_422(self, client):  # noqa: ANN001, ANN201
        rep = client.post(
            "/ask",
            json={"question": "x" * 5000},
            headers={"X-API-Key": CLE},
        )
        assert rep.status_code == 422

    def test_contenu_ingestion_trop_volumineux_422(self, client):  # noqa: ANN001, ANN201
        contenu = {"cle": "x" * (1024 * 1024 + 1)}
        rep = client.post(
            "/ingest",
            json={"source": "EUR-Lex", "contenu_json": contenu},
            headers={"X-API-Key": CLE},
        )
        assert rep.status_code == 422

    def test_docs_desactive_par_defaut(self, client):  # noqa: ANN001, ANN201
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404


# ---------------------------------------------------------------------------
# Masquage des erreurs
# ---------------------------------------------------------------------------


class TestMasquageErreurs:
    def test_erreur_interne_masquee(self, client, monkeypatch):  # noqa: ANN001, ANN201
        from src.orchestrator import Orchestrateur

        def exploser(self, tache_id, decision, commentaire=None):  # noqa: ANN001, ANN202, ARG001
            raise RuntimeError("/chemin/secret/interne")

        monkeypatch.setattr(Orchestrateur, "valider_tache", exploser)
        rep = client.post(
            "/approve",
            json={"tache_id": str(uuid.uuid4())},
            headers={"X-API-Key": CLE},
        )
        assert rep.status_code == 500
        detail = rep.json()["detail"]
        assert "Erreur interne" in detail
        assert "chemin" not in detail

    def test_tache_introuvable_404(self, client, monkeypatch):  # noqa: ANN001, ANN201
        from src.orchestrator import Orchestrateur

        def introuvable(self, tache_id, decision, commentaire=None):  # noqa: ANN001, ANN202, ARG001
            raise ValueError("Tâche introuvable")  # noqa: TRY003

        monkeypatch.setattr(Orchestrateur, "valider_tache", introuvable)
        rep = client.post(
            "/approve",
            json={"tache_id": str(uuid.uuid4())},
            headers={"X-API-Key": CLE},
        )
        assert rep.status_code == 404


# ---------------------------------------------------------------------------
# En-têtes de sécurité
# ---------------------------------------------------------------------------


class TestEnTetesSecurite:
    def test_en_tetes_presents(self, client):  # noqa: ANN001, ANN201
        rep = client.get("/health")
        assert rep.headers.get("x-content-type-options") == "nosniff"
        assert rep.headers.get("x-frame-options") == "DENY"
        assert rep.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        assert "max-age" in rep.headers.get("strict-transport-security", "")

    def test_csp_present_et_strict(self, client):  # noqa: ANN001, ANN201
        """M3 : politique CSP en place, sans 'unsafe-inline' sur les scripts."""
        rep = client.get("/health")
        csp = rep.headers.get("content-security-policy", "")
        assert csp, "Content-Security-Policy manquant"
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        # unsafe-inline peut apparaître sur style-src mais JAMAIS sur script-src.
        assert "script-src 'self'" in csp
        script_dir = next(
            d for d in csp.split(";") if d.strip().startswith("script-src")
        )
        assert "unsafe-inline" not in script_dir
        assert "unsafe-eval" not in script_dir
