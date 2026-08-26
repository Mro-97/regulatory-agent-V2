"""
tests/test_temporal_scenarios.py — Tests pour le raisonnement temporel (B9)

Vérifie que l'Agent temporel retourne la bonne version d'un article
en fonction de la date de contexte fournie.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.agents.temporal import AgentTemporel
from src.models import EvidenceRecuperee


def _paire_versions_rgpd() -> list[EvidenceRecuperee]:
    """Deux versions successives d'un même article, chaînées à la journée près."""
    return [
        EvidenceRecuperee(
            chunk_id="doc_A",
            document_id="RGPD",
            article_id="art_32",
            texte_extrait="Version A",
            score_similarite=0.9,
            valid_from=date(2018, 5, 25),
            valid_to=date(2026, 8, 2),
        ),
        EvidenceRecuperee(
            chunk_id="doc_B",
            document_id="RGPD",
            article_id="art_32_2026",
            texte_extrait="Version B",
            score_similarite=0.9,
            valid_from=date(2026, 8, 3),
            valid_to=None,
        ),
    ]


class TestTemporalFiltering:

    def test_version_applicable_avant_coupure(self):
        """Date antérieure à la coupure → doit retourner la version A."""
        agent = AgentTemporel()
        resultat = agent.analyser(
            question="Q",
            evidences=_paire_versions_rgpd(),
            date_contexte=date(2025, 6, 15),
        )
        assert len(resultat.evidences_applicables) == 1
        assert resultat.evidences_applicables[0].article_id == "art_32"

    def test_version_applicable_apres_coupure(self):
        """Date postérieure à la coupure → doit retourner la version B."""
        agent = AgentTemporel()
        resultat = agent.analyser(
            question="Q",
            evidences=_paire_versions_rgpd(),
            date_contexte=date(2027, 1, 1),
        )
        assert len(resultat.evidences_applicables) == 1
        assert resultat.evidences_applicables[0].article_id == "art_32_2026"

    def test_jour_exact_coupure_borne_valid_to_incluse(self):
        """Le 2026-08-02, la version A est encore applicable (borne valid_to incluse)."""
        agent = AgentTemporel()
        resultat = agent.analyser(
            question="Q",
            evidences=_paire_versions_rgpd(),
            date_contexte=date(2026, 8, 2),
        )
        assert [e.article_id for e in resultat.evidences_applicables] == ["art_32"]

    def test_jour_exact_coupure_borne_valid_from_incluse(self):
        """Le 2026-08-03, seule la version B est applicable (borne valid_from incluse)."""
        agent = AgentTemporel()
        resultat = agent.analyser(
            question="Q",
            evidences=_paire_versions_rgpd(),
            date_contexte=date(2026, 8, 3),
        )
        assert [e.article_id for e in resultat.evidences_applicables] == ["art_32_2026"]

    def test_date_hors_intervalle_raisonnable_leve_valueerror(self):
        """B5 : une date_contexte hors [1900, 2100] doit lever ValueError."""
        agent = AgentTemporel()
        with pytest.raises(ValueError):
            agent.analyser(
                question="Q",
                evidences=_paire_versions_rgpd(),
                date_contexte=date(9999, 12, 31),
            )

    def test_date_calendaire_invalide_leve_valueerror_a_la_construction(self):
        """date(2025, 2, 30) n'existe pas — la construction du `date` échoue."""
        with pytest.raises(ValueError):
            date(2025, 2, 30)
