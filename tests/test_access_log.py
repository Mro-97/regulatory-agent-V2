"""tests/test_access_log.py — journal d'accès HTTP structuré.

Une ligne `acces ...` par requête, avec le motif du refus pour les
4xx et une empreinte de clé (jamais la clé en clair).
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from src import api as api_module
from src.access_log import _empreinte_cle

CLE = "cle-de-test-0123456789abcdef"


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_module.app)


@pytest.fixture
def logs_acces(caplog):  # noqa: ANN001, ANN201
    caplog.set_level(logging.INFO, logger="acces")
    return caplog


def _lignes(caplog) -> list[str]:  # noqa: ANN001
    return [r.getMessage() for r in caplog.records if r.name == "acces"]


def test_empreinte_cle_valeurs() -> None:
    assert _empreinte_cle(None) == "absente"
    assert _empreinte_cle("   ") == "absente"
    assert _empreinte_cle("pas-la-bonne") == "invalide"
    fp = _empreinte_cle(CLE)
    assert len(fp) == 8 and fp not in ("absente", "invalide")


def test_empreinte_cle_juge_sur_le_magasin_pas_sur_config(monkeypatch):  # noqa: ANN001, ANN201
    """Régression : la validité se juge sur le MAGASIN, pas sur `cfg.api_key`.

    En production `API_KEY` est vide (configuration exigée par le boot) et les
    clés vivent hashées (`API_KEYS_HACHEES` / `data/api_keys.json`). L'ancienne
    comparaison à `cfg.api_key` journalisait toute clé valide du magasin comme
    `invalide` — et attribuait une empreinte à n'importe quelle chaîne
    inconnue : l'inverse de ce qu'un journal de sécurité doit montrer.
    """
    import hashlib

    from config import cfg
    from src.auth import recharger_magasin

    cle_hashee = "rak_cle_du_magasin_hashe_0123456789"
    empreinte_attendue = hashlib.sha256(cle_hashee.encode()).hexdigest()
    monkeypatch.setattr(cfg, "api_key", "", raising=False)
    monkeypatch.setattr(cfg, "api_keys_str", "", raising=False)
    monkeypatch.setattr(
        cfg,
        "api_keys_hachees_str",
        f"{empreinte_attendue}:admin:test",
        raising=False,
    )
    recharger_magasin()
    try:
        assert _empreinte_cle(cle_hashee) == empreinte_attendue[:8]
        assert _empreinte_cle("rak_cle_inconnue") == "invalide"
    finally:
        recharger_magasin()


def test_acces_200_ligne_structuree(client, logs_acces) -> None:  # noqa: ANN001
    client.get("/health")
    ligne = _lignes(logs_acces)[-1]
    assert ligne.startswith("acces user=")
    assert "chemin=/health" in ligne
    assert "statut=200" in ligne
    assert "motif=-" in ligne


def test_acces_logue_la_question_et_le_client_id(client, logs_acces) -> None:  # noqa: ANN001
    client.post(
        "/ask",
        json={"question": "Quel est le délai de notification d'une violation ?"},
        headers={"X-API-Key": CLE, "X-Client-Id": "poste-alice"},
    )
    ligne = _lignes(logs_acces)[-1]
    assert "user=poste-alice" in ligne
    assert "délai de notification" in ligne


def test_acces_user_repli_sur_x_user(client, logs_acces) -> None:  # noqa: ANN001
    client.get("/health", headers={"X-User": "julien"})
    assert "user=julien" in _lignes(logs_acces)[-1]


def test_acces_401_motif_cle_absente(client, logs_acces) -> None:  # noqa: ANN001
    client.post("/ask", json={"question": "Obligations RGPD ?"})
    ligne = _lignes(logs_acces)[-1]
    assert "statut=401" in ligne
    assert "motif=cle_absente" in ligne


def test_acces_401_motif_cle_invalide(client, logs_acces) -> None:  # noqa: ANN001
    client.post("/ask", json={"question": "Q ?"}, headers={"X-API-Key": "mauvaise"})
    ligne = _lignes(logs_acces)[-1]
    assert "statut=401" in ligne
    assert "motif=cle_invalide" in ligne
    assert "cle=invalide" in ligne


def test_acces_403_motif_origine_refusee(client, logs_acces) -> None:  # noqa: ANN001
    client.post(
        "/ask",
        json={"question": "Q ?"},
        headers={"X-API-Key": CLE, "Origin": "https://evil.example"},
    )
    ligne = _lignes(logs_acces)[-1]
    assert "statut=403" in ligne
    assert "motif=origine_refusee" in ligne
    assert "origin=https://evil.example" in ligne


def test_cle_jamais_en_clair(client, logs_acces) -> None:  # noqa: ANN001
    client.post("/ask", json={"question": "Q ?"}, headers={"X-API-Key": CLE})
    assert all(CLE not in ligne for ligne in _lignes(logs_acces))
