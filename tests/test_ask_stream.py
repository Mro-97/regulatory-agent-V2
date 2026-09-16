"""tests/test_ask_stream.py — POST /ask/stream (Server-Sent Events).

Couvre le cadrage SSE (mode mock), le pont générateur-synchrone →
async de `_stream_sous_verrou`, et le garde-fou sans preuve de
`expliquer_stream`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src import api as api_module
from src.models import RequeteQuestion, SortieAgent
from src.orchestrator import Orchestrateur

CLE = "cle-de-test-0123456789abcdef"


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_module.app)


def test_ask_stream_mock_emet_token_puis_fin(client) -> None:  # noqa: ANN001
    rep = client.post(
        "/ask/stream",
        json={"question": "Obligations RGPD ?"},
        headers={"X-API-Key": CLE},
    )
    assert rep.status_code == 200, rep.text
    assert rep.headers["content-type"].startswith("text/event-stream")
    corps = rep.text
    assert "event: token" in corps
    assert "event: fin" in corps
    datas = [ligne for ligne in corps.splitlines() if ligne.startswith("data: ")]
    payload = json.loads(datas[-1].removeprefix("data: "))
    assert "request_id" in payload
    assert "niveau_confiance" in payload


def test_ask_stream_sans_cle_401(client) -> None:  # noqa: ANN001
    assert client.post("/ask/stream", json={"question": "x ?"}).status_code == 401


def test_stream_sous_verrou_pompe_les_fragments() -> None:
    orch = Orchestrateur(mode="mock")

    def _gen() -> Iterator[str]:
        yield "a"
        yield "b"
        yield "c"

    async def _run() -> list[str]:
        return [frag async for frag in orch._stream_sous_verrou(_gen)]

    assert asyncio.run(_run()) == ["a", "b", "c"]


def test_stream_sous_verrou_propage_exception() -> None:
    orch = Orchestrateur(mode="mock")

    def _gen() -> Iterator[str]:
        yield "a"
        raise RuntimeError("boom")

    async def _run() -> list[str]:
        return [frag async for frag in orch._stream_sous_verrou(_gen)]

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(_run())


def test_expliquer_stream_sans_preuve_message_aucun_passage() -> None:
    from src.agents.explainer import _MSG_AUCUN_PASSAGE, AgentExplainer

    agent = AgentExplainer(use_llm=True)
    fragments = list(agent.expliquer_stream(question="Q ?", evidences=[]))
    assert fragments == [_MSG_AUCUN_PASSAGE]
    assert agent._modele is None


def test_sequence_evenements_du_flux_reel() -> None:
    """Verrouille la séquence SSE du pipeline réel (refonte du 2026-09-16).

    La synthèse est produite par un générateur qui *yield* des tokens : un
    helper ne peut pas à la fois produire des fragments et rendre le texte
    concaténé. Ce test fige donc l'ordre observable des événements tel que le
    client SSE le reçoit — c'est exactement ce que la refonte pouvait casser.
    """

    class _FauxAgent:
        """Remplace l'Explainer LLM (aucun modèle chargé dans ce test)."""

        def expliquer_stream(self, **_kwargs: object) -> Iterator[str]:
            yield "Le "
            yield "texte."

    def _faux_retrieval(*_args: object, **_kwargs: object):  # noqa: ANN202
        async def _reponse():  # noqa: ANN202
            return [], SortieAgent(nom_agent="Retriever", machine="test")

        return _reponse()

    async def _run() -> list[tuple[str, dict]]:
        orch = Orchestrateur(mode="real")
        return [
            evenement
            async for evenement in orch._stream_pipeline_reel(
                RequeteQuestion(question="Quelles obligations ?"),
                uuid4(),
                "courante",
            )
        ]

    with (
        patch.object(Orchestrateur, "_agent_explainer", return_value=_FauxAgent()),
        patch.object(Orchestrateur, "_etape_retrieval", new=_faux_retrieval),
        patch.object(
            Orchestrateur, "_soumettre_validation_si_besoin", return_value=(False, None)
        ),
        patch.object(Orchestrateur, "_persister_audit", new=AsyncMock()),
    ):
        evenements = asyncio.run(_run())

    genres = [genre for genre, _ in evenements]
    assert genres[0] == "etape"
    assert genres[-1] == "fin"
    phases = [charge["phase"] for genre, charge in evenements if genre == "etape"]
    assert phases == ["recherche", "temporel", "synthese", "citations"]
    tokens = [charge["t"] for genre, charge in evenements if genre == "token"]
    assert "".join(tokens).strip() == "Le texte."
    payload = evenements[-1][1]
    assert "request_id" in payload
    assert "niveau_confiance" in payload
