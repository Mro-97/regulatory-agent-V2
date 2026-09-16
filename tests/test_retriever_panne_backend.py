"""tests/test_retriever_panne_backend.py — H5 : ne plus masquer une panne backend.

Avant, `_encoder_question_ou_vide` convertissait `InferenceError` en `[]` et
`_rechercher_passe` convertissait `VectorStoreError` en `[]` : `retrieve()`
renvoyait une liste vide et l'orchestrateur affichait « Aucun passage
réglementaire pertinent » alors que Qdrant ou le modèle était HS. Le repli
`_reponse_retrieval_indisponible` était inatteignable.
"""

from __future__ import annotations

import sys
import types
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

# Stubs MLX pour environnement non-Apple (les tests n'exécutent pas d'inférence).
for nom in ("mlx", "mlx.core", "mlx_lm", "mlx_lm.sample_utils", "mlx_embeddings"):
    if nom not in sys.modules:
        sys.modules[nom] = types.ModuleType(nom)
sys.modules["mlx.core"].eval = lambda *a, **k: None  # noqa: ARG005 — stub/signature

import pytest  # noqa: E402 — stubs MLX avant imports projet
from src.agents.retriever import (  # noqa: E402 — stubs MLX avant imports projet
    Retriever,
)
from src.errors import (  # noqa: E402 — stubs MLX avant imports projet
    InferenceError,
    VectorStoreError,
)


def _point(point_id: str, score: float, valid_to: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=point_id,
        score=score,
        payload={
            "chunk_id": point_id,
            "document_id": "DOC",
            "article_id": f"art_{point_id}",
            "texte_chunk": f"Texte {point_id}",
            "valid_from": "2020-01-01",
            "valid_to": valid_to,
        },
    )


def _retriever(client: MagicMock, top_k: int = 3) -> Retriever:
    retriever = Retriever(qdrant_client=client, top_k=top_k)
    retriever.embed_question = lambda q: [0.1] * 8  # noqa: ARG005 — stub/signature
    return retriever


class TestPanneQdrant:
    def test_deux_passes_en_echec_propage_vector_store_error(self) -> None:
        client = MagicMock()
        client.query_points.side_effect = ConnectionError("qdrant down")
        retriever = _retriever(client)
        with pytest.raises(VectorStoreError):
            retriever.retrieve(question="Q", date_contexte=date(2025, 6, 15))

    def test_une_passe_sur_deux_en_echec_reste_toleree(self) -> None:
        reponse = MagicMock()
        reponse.points = [_point("p1", 0.9, "2030-01-01")]
        client = MagicMock()
        client.query_points.side_effect = [ConnectionError("boom"), reponse]
        retriever = _retriever(client)
        evidences = retriever.retrieve(question="Q", date_contexte=date(2025, 6, 15))
        assert [e.chunk_id for e in evidences] == ["p1"]


class TestPanneEmbedding:
    def test_echec_embedding_propage_inference_error(self) -> None:
        client = MagicMock()
        retriever = Retriever(qdrant_client=client, top_k=3)

        def panne(_question: str) -> list[float]:
            raise InferenceError("indisponible")

        retriever.embed_question = panne
        with pytest.raises(InferenceError):
            retriever.retrieve(question="Q", date_contexte=date(2025, 6, 15))
        assert client.query_points.call_count == 0
