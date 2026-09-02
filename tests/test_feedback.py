"""tests/test_feedback.py — endpoint POST /feedback (signalement utilisateur).

Un signalement écrit une ligne JSONL dans `cfg.feedback_local_path` :
revue qualité + jeu de calibration des seuils de confiance.
"""

from __future__ import annotations

import json
import uuid

import pytest
from config import cfg
from fastapi.testclient import TestClient
from src import api as api_module

CLE = "cle-de-test-0123456789abcdef"


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_module.app)


@pytest.fixture
def chemin_feedback(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    p = tmp_path / "feedback.jsonl"
    monkeypatch.setattr(cfg, "feedback_local_path", p)
    return p


def test_feedback_valide_202_et_ligne_ecrite(client, chemin_feedback):  # noqa: ANN001, ANN201
    rid = str(uuid.uuid4())
    rep = client.post(
        "/feedback",
        json={"request_id": rid, "motif": "reponse_incorrecte", "commentaire": "faux"},
        headers={"X-API-Key": CLE},
    )
    assert rep.status_code == 202, rep.text
    assert rep.json()["enregistre"] is True
    lignes = chemin_feedback.read_text(encoding="utf-8").splitlines()
    assert len(lignes) == 1
    enr = json.loads(lignes[0])
    assert enr["request_id"] == rid
    assert enr["motif"] == "reponse_incorrecte"
    assert enr["commentaire"] == "faux"


def test_feedback_sans_cle_401(client, chemin_feedback):  # noqa: ANN001, ANN201
    rep = client.post(
        "/feedback",
        json={"request_id": str(uuid.uuid4()), "motif": "autre"},
    )
    assert rep.status_code == 401
    assert not chemin_feedback.exists()


def test_feedback_motif_invalide_422(client, chemin_feedback):  # noqa: ANN001, ANN201, ARG001
    rep = client.post(
        "/feedback",
        json={"request_id": str(uuid.uuid4()), "motif": "n_importe_quoi"},
        headers={"X-API-Key": CLE},
    )
    assert rep.status_code == 422


def test_feedback_request_id_manquant_422(client, chemin_feedback):  # noqa: ANN001, ANN201, ARG001
    rep = client.post(
        "/feedback",
        json={"motif": "autre"},
        headers={"X-API-Key": CLE},
    )
    assert rep.status_code == 422


def test_feedback_deux_signalements_append(client, chemin_feedback):  # noqa: ANN001, ANN201
    for motif in ("source_hors_sujet", "confiance_trompeuse"):
        rep = client.post(
            "/feedback",
            json={"request_id": str(uuid.uuid4()), "motif": motif},
            headers={"X-API-Key": CLE},
        )
        assert rep.status_code == 202
    assert len(chemin_feedback.read_text(encoding="utf-8").splitlines()) == 2
