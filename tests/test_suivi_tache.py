"""tests/test_suivi_tache.py — GET /tache/{id} : suivi HITL côté demandeur.

`obtenir_tache` cherche la tâche dans les files pendantes ET traitées
(`traite_<file>`), pour qu'un demandeur puisse voir qu'elle a été
approuvée / rejetée après coup.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from src import api as api_module
from src.models import StatutValidation, TacheValidation, TypeFilePendante
from src.orchestrator import Orchestrateur
from src.orchestrator_validation import obtenir_tache

CLE = "cle-de-test-0123456789abcdef"


class FauxRedis:
    """lrange sur des listes JSON en mémoire ; le reste no-op."""

    def __init__(self, listes: dict[str, list[str]]) -> None:
        self._listes = listes

    async def lrange(self, nom: str, _a: int, _b: int) -> list[str]:
        return list(self._listes.get(nom, []))

    async def aclose(self) -> None:
        return None


def _tache_json(tid: uuid.UUID, statut: StatutValidation) -> str:
    return TacheValidation(
        tache_id=tid,
        type_file=TypeFilePendante.REPONSES,
        statut=statut,
        horodatage_creation=datetime.now(UTC),
    ).model_dump_json()


def test_obtenir_tache_trouve_dans_file_traitee() -> None:
    tid = uuid.uuid4()
    faux = FauxRedis(
        {"traite_pending_responses": [_tache_json(tid, StatutValidation.APPROUVE)]}
    )

    async def scenario() -> TacheValidation | None:
        return await obtenir_tache(lambda: _ready(faux), tid)

    tache = asyncio.run(scenario())
    assert tache is not None
    assert tache.statut is StatutValidation.APPROUVE


def test_obtenir_tache_absente_retourne_none() -> None:
    faux = FauxRedis({})

    async def scenario() -> TacheValidation | None:
        return await obtenir_tache(lambda: _ready(faux), uuid.uuid4())

    assert asyncio.run(scenario()) is None


async def _ready(obj: FauxRedis) -> FauxRedis:
    return obj


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_module.app)


def test_get_tache_200_slim(client, monkeypatch):  # noqa: ANN001, ANN201
    tid = uuid.uuid4()

    async def faux_obtenir(self, tache_id):  # noqa: ANN001, ANN202, ARG001
        return TacheValidation(
            tache_id=tache_id,
            type_file=TypeFilePendante.REPONSES,
            statut=StatutValidation.REJETE,
            horodatage_creation=datetime.now(UTC),
            commentaire_validateur="hors périmètre",
        )

    monkeypatch.setattr(Orchestrateur, "obtenir_tache", faux_obtenir)
    rep = client.get(f"/tache/{tid}", headers={"X-API-Key": CLE})
    assert rep.status_code == 200, rep.text
    body = rep.json()
    assert body["statut"] == "rejeté"
    assert body["commentaire_validateur"] == "hors périmètre"
    assert "contenu" not in body


def test_get_tache_404(client, monkeypatch):  # noqa: ANN001, ANN201
    async def faux_obtenir(self, tache_id):  # noqa: ANN001, ANN202, ARG001
        return None

    monkeypatch.setattr(Orchestrateur, "obtenir_tache", faux_obtenir)
    rep = client.get(f"/tache/{uuid.uuid4()}", headers={"X-API-Key": CLE})
    assert rep.status_code == 404


def test_get_tache_sans_cle_401(client):  # noqa: ANN001, ANN201
    assert client.get(f"/tache/{uuid.uuid4()}").status_code == 401
