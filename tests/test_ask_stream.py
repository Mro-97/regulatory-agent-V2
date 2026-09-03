"""tests/test_ask_stream.py — POST /ask/stream (Server-Sent Events).

Couvre le cadrage SSE (mode mock), le pont générateur-synchrone →
async de `_stream_sous_verrou`, et le garde-fou sans preuve de
`expliquer_stream`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from src import api as api_module
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
