"""tests/test_temporal_articles_distincts.py — M2/M3 de l'agent Temporel.

M2 : `art_3` et `art_10` sont des articles DISTINCTS, pas deux versions du
même article (`article_id.split("_")[0]` les réduisait tous les deux à
« art », produisant de faux chevauchements et une confiance FAIBLE).

M3 : une version ouverte (`valid_to=None`) supplantée par une version plus
récente est un chevauchement réel, qui n'était jamais signalé.
"""

from __future__ import annotations

from datetime import date

from src.agents.retriever_helpers import article_de_base
from src.agents.temporal import AgentTemporel, _grouper_versions_par_article
from src.models import EvidenceRecuperee, NiveauConfiance


def _ev(
    article_id: str,
    texte: str,
    valid_from: date,
    valid_to: date | None = None,
) -> EvidenceRecuperee:
    return EvidenceRecuperee(
        chunk_id=f"DOC_{article_id}",
        document_id="DOC",
        article_id=article_id,
        texte_extrait=texte,
        score_similarite=0.9,
        valid_from=valid_from,
        valid_to=valid_to,
    )


class TestArticleDeBase:
    def test_suffixe_version_retire(self) -> None:
        assert article_de_base("art_32_2026") == "art_32"
        assert article_de_base("art_32_v2") == "art_32"

    def test_articles_voisins_non_confondus(self) -> None:
        assert article_de_base("art_3") == "art_3"
        assert article_de_base("art_10") == "art_10"


class TestGroupementParArticle:
    def test_articles_distincts_non_groupees(self) -> None:
        groupes = _grouper_versions_par_article(
            [
                _ev("art_3", "Texte 3", date(2020, 1, 1)),
                _ev("art_10", "Texte 10", date(2020, 1, 1)),
            ]
        )
        assert len(groupes) == 2

    def test_versions_du_meme_article_groupees(self) -> None:
        groupes = _grouper_versions_par_article(
            [
                _ev("art_32", "Ancienne", date(2018, 5, 25), date(2026, 8, 2)),
                _ev("art_32_2026", "Nouvelle", date(2026, 8, 3)),
            ]
        )
        assert len(groupes) == 1


class TestAnomalies:
    def test_articles_distincts_pas_de_faux_chevauchement(self) -> None:
        agent = AgentTemporel(use_llm=False)
        evidences = [
            _ev("art_3", "Le responsable doit agir.", date(2020, 1, 1)),
            _ev("art_10", "Le responsable ne doit pas agir.", date(2020, 1, 1)),
        ]
        chevauchements, lacunes = agent.detecter_anomalies(evidences)
        assert chevauchements == []
        assert lacunes == []
        resultat = agent.analyser("Q", evidences, date(2025, 1, 1))
        assert resultat.niveau_confiance is NiveauConfiance.ELEVE

    def test_version_ouverte_non_close_signalee(self) -> None:
        agent = AgentTemporel(use_llm=False)
        evidences = [
            _ev("art_1", "Ancienne version", date(2020, 1, 1)),
            _ev("art_1_v2", "Nouvelle version", date(2024, 1, 1)),
        ]
        chevauchements, _ = agent.detecter_anomalies(evidences)
        assert len(chevauchements) == 1
        assert "version ouverte non close" in chevauchements[0]

    def test_versions_chainees_sans_anomalie(self) -> None:
        agent = AgentTemporel(use_llm=False)
        evidences = [
            _ev("art_32", "Ancienne", date(2018, 5, 25), date(2026, 8, 2)),
            _ev("art_32_2026", "Nouvelle", date(2026, 8, 3)),
        ]
        chevauchements, lacunes = agent.detecter_anomalies(evidences)
        assert chevauchements == []
        assert lacunes == []
