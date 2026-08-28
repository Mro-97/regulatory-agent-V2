"""
tests/test_bug3_retriever_famine.py — B3

Le quota rigide `part_a = ceil(top_k/2)` / `part_b = top_k - part_a`
appliqué avant tout arbitrage de score peut évincer un candidat de
passe A (transitoire) à haut score au profit d'un candidat de passe B
(permanent) à score bas :

  passe A : a0=0.90, a1=0.80, a2=0.70   (transitoires)
  passe B : b0=0.85, b1=0.50            (permanents)
  top_k=4, quota strict → prend a0,a1,b0,b1 → a2 (0.70) évincé par b1 (0.50).

Correction attendue : après avoir garanti la représentation de chaque
passe non vide (au moins 1 slot chacune), les slots restants sont
attribués par score global — a2 reste dans le résultat.
"""

from __future__ import annotations

import sys
import types
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

for nom in ("mlx", "mlx.core", "mlx_lm", "mlx_lm.sample_utils", "mlx_embeddings"):
    if nom not in sys.modules:
        sys.modules[nom] = types.ModuleType(nom)
sys.modules["mlx.core"].eval = lambda *a, **k: None

from src.agents.retriever import Retriever  # noqa: E402


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


def _retriever(passe_a, passe_b, top_k):
    client = MagicMock()
    ra, rb = MagicMock(), MagicMock()
    ra.points, rb.points = passe_a, passe_b
    client.query_points.side_effect = [ra, rb]
    r = Retriever(qdrant_client=client, top_k=top_k)
    r.embed_question = lambda q: [0.1] * 1024
    return r


class TestB3PasDEvictionParScoreBas:
    def test_transitoire_haut_score_pas_evincee_par_permanent_bas_score(self):
        passe_a = [
            _point("a0", 0.90, valid_to="2030-01-01"),
            _point("a1", 0.80, valid_to="2030-01-01"),
            _point("a2", 0.70, valid_to="2030-01-01"),
        ]
        passe_b = [
            _point("b0", 0.85, valid_to=None),
            _point("b1", 0.50, valid_to=None),
        ]
        r = _retriever(passe_a, passe_b, top_k=4)
        evidences = r.retrieve(question="Q", date_contexte=date(2025, 6, 15))
        ids = {e.chunk_id for e in evidences}

        assert len(evidences) == 4
        # a2 (0.70) doit être présent : il est meilleur que b1 (0.50).
        assert "a2" in ids, (
            f"a2 (score 0.70, transitoire) évincé par b1 (score 0.50, permanent) "
            f"malgré un score supérieur. Résultat: {ids}"
        )
        # b1 (le plus bas) doit être celui qui saute.
        assert "b1" not in ids, (
            f"b1 (0.50) présent alors que a2 (0.70) devait être préféré. Résultat: {ids}"
        )

    def test_passe_a_reste_representee_scores_bas(self):
        """Régression du fix B7 : passe A évincée par scores B tous meilleurs."""
        passe_a = [
            _point(f"a{i}", 0.50 - i * 0.01, valid_to="2030-01-01") for i in range(4)
        ]
        passe_b = [_point(f"b{i}", 0.90 - i * 0.01, valid_to=None) for i in range(4)]
        r = _retriever(passe_a, passe_b, top_k=4)
        evidences = r.retrieve(question="Q", date_contexte=date(2025, 6, 15))
        ids = {e.chunk_id for e in evidences}
        assert any(x.startswith("a") for x in ids), f"Passe A totalement évincée: {ids}"
