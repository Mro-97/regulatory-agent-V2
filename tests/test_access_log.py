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


def test_acces_200_ligne_structuree(client, logs_acces) -> None:  # noqa: ANN001
    client.get("/health")
    ligne = _lignes(logs_acces)[-1]
    assert ligne.startswith("acces ip=")
    assert "chemin=/health" in ligne
    assert "statut=200" in ligne
    assert "motif=-" in ligne


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
