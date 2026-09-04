"""tests/test_retriever_dedup.py — dédup des evidences identiques.

La collection Qdrant contient des doublons d'ingestion : le retriever
doit collapser les chunks (doc + article + texte) identiques en gardant
le meilleur score, sinon le top-k est pollué et la confiance faussée.
"""

from __future__ import annotations

from datetime import date

from src.agents.retriever_helpers import dedupliquer_evidences
from src.models import EvidenceRecuperee


def _ev(
    chunk_id: str, doc: str, art: str, texte: str, score: float
) -> EvidenceRecuperee:
    return EvidenceRecuperee(
        chunk_id=chunk_id,
        document_id=doc,
        article_id=art,
        texte_extrait=texte,
        score_similarite=score,
        valid_from=date(2018, 5, 25),
    )


def test_collapse_chunks_identiques_garde_meilleur_score() -> None:
    evidences = [
        _ev("c1", "RGPD", "art_33", "notification 72h", 0.51),
        _ev("c2", "RGPD", "art_33", "notification 72h", 0.53),
        _ev("c3", "RGPD", "art_33", "notification 72h", 0.49),
    ]
    out = dedupliquer_evidences(evidences)
    assert len(out) == 1
    assert out[0].score_similarite == 0.53


def test_articles_ou_docs_differents_sont_conserves() -> None:
    evidences = [
        _ev("c1", "RGPD", "art_33", "texte A", 0.50),
        _ev("c2", "RGPD", "art_34", "texte A", 0.48),
        _ev("c3", "NIS2", "art_33", "texte A", 0.47),
    ]
    out = dedupliquer_evidences(evidences)
    assert len(out) == 3


def test_preserve_ordre_d_entree() -> None:
    # Le tri appartient à l'appelant : dedup ne réordonne pas (sinon la
    # priorisation « article cité en tête » serait annulée).
    evidences = [
        _ev("c1", "RGPD", "art_1", "a", 0.30),
        _ev("c2", "RGPD", "art_2", "b", 0.70),
        _ev("c3", "RGPD", "art_3", "c", 0.50),
    ]
    scores = [e.score_similarite for e in dedupliquer_evidences(evidences)]
    assert scores == [0.30, 0.70, 0.50]


def test_dedup_garde_meilleur_score_a_la_position_du_premier() -> None:
    evidences = [
        _ev("c1", "RGPD", "art_1", "meme texte", 0.20),
        _ev("c2", "NIS2", "art_9", "autre", 0.90),
        _ev("c3", "RGPD", "art_1", "meme texte", 0.80),
    ]
    out = dedupliquer_evidences(evidences)
    assert [(e.document_id, e.score_similarite) for e in out] == [
        ("RGPD", 0.80),
        ("NIS2", 0.90),
    ]


def test_liste_vide() -> None:
    assert dedupliquer_evidences([]) == []
