"""tests/test_retriever_score_borne.py — le score Qdrant est borné à [0, 1].

`EvidenceRecuperee.score_similarite` est contraint `ge=0.0, le=1.0`. Une
collection non normalisée (ou une distance autre que le cosinus) peut
renvoyer un score légèrement > 1.0 : sans bornage, la ValidationError faisait
jeter le chunk par `point_vers_evidence` — la meilleure preuve disparaissait.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.agents.retriever_helpers import point_vers_evidence


def _point(score: float) -> SimpleNamespace:
    return SimpleNamespace(
        id="p1",
        score=score,
        payload={
            "chunk_id": "chunk_1",
            "document_id": "DOC",
            "article_id": "art_1",
            "texte_chunk": "Texte réglementaire.",
            "valid_from": "2020-01-01",
            "valid_to": None,
        },
    )


class TestScoreBorne:
    def test_score_superieur_a_un_borne(self) -> None:
        evidence = point_vers_evidence(_point(1.23))
        assert evidence is not None
        assert evidence.score_similarite == 1.0

    def test_score_negatif_borne_a_zero(self) -> None:
        evidence = point_vers_evidence(_point(-0.2))
        assert evidence is not None
        assert evidence.score_similarite == 0.0

    def test_score_normal_inchange(self) -> None:
        evidence = point_vers_evidence(_point(0.7512))
        assert evidence is not None
        assert evidence.score_similarite == 0.7512

    def test_valid_to_absent_vaut_en_vigueur(self) -> None:
        point = _point(0.5)
        del point.payload["valid_to"]
        evidence = point_vers_evidence(point)
        assert evidence is not None
        assert evidence.valid_to is None
