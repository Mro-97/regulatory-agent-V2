"""
tests/test_temporal_scenarios.py — Tests pour le raisonnement temporel (B9)

Vérifie que l'Agent temporel retourne la bonne version d'un article
en fonction de la date de contexte fournie.
"""

from __future__ import annotations

import pytest
from datetime import date

from src.agents.temporal import AgentTemporel
from src.models import EvidenceRecuperee


class TestTemporalFiltering:

    def test_version_applicable_avant_coupure(self):
        """Date antérieure à la version B → doit retourner la version A."""
        evidences = [
            EvidenceRecuperee(
                chunk_id="doc_A",
                document_id="RGPD",
                article_id="art_32",
                texte_extrait="Version A",
                valid_from=date(2018, 5, 25),
                valid_to=date(2026, 8, 2),
            ),
            EvidenceRecuperee(
                chunk_id="doc_B",
                document_id="RGPD",
                article_id="art_32_2026",
                texte_extrait="Version B",
                valid_from=date(2026, 8, 3),
                valid_to=None,
            ),
        ]

        agent = AgentTemporel()
        resultat = agent.filtrer(evidences, date_contexte=date(2025, 6, 15))

        assert len(resultat.applicables) == 1
        assert resultat.applicables[0].article_id == "art_32"

    def test_version_applicable_apres_coupure(self):
        """Date postérieure à la version A → doit retourner la version B."""
        evidences = [
            EvidenceRecuperee(
                chunk_id="doc_A",
                document_id="RGPD",
                article_id="art_32",
                texte_extrait="Version A",
                valid_from=date(2018, 5, 25),
                valid_to=date(2026, 8, 2),
            ),
            EvidenceRecuperee(
                chunk_id="doc_B",
                document_id="RGPD",
                article_id="art_32_2026",
                texte_extrait="Version B",
                valid_from=date(2026, 8, 3),
                valid_to=None,
            ),
        ]

        agent = AgentTemporel()
        resultat = agent.filtrer(evidences, date_contexte=date(2027, 1, 1))

        assert len(resultat.applicables) == 1
        assert resultat.applicables[0].article_id == "art_32_2026"

    def test_aucune_date_contexte(self):
        """Sans date de contexte, on prend la version ouverte (valid_to=None)."""
        evidences = [
            EvidenceRecuperee(
                chunk_id="doc_A",
                document_id="RGPD",
                article_id="art_32",
                texte_extrait="Version A",
                valid_from=date(2018, 5, 25),
                valid_to=date(2026, 8, 2),
            ),
            EvidenceRecuperee(
                chunk_id="doc_B",
                document_id="RGPD",
                article_id="art_32_2026",
                texte_extrait="Version B",
                valid_from=date(2026, 8, 3),
                valid_to=None,
            ),
        ]

        agent = AgentTemporel()
        resultat = agent.filtrer(evidences, date_contexte=None)

        assert len(resultat.applicables) == 1
        assert resultat.applicables[0].article_id == "art_32_2026"

    def test_date_invalide(self):
        """Si la date est invalide, lever une exception ou retourner une erreur."""
        evidences = [
            EvidenceRecuperee(
                chunk_id="doc_A",
                document_id="RGPD",
                article_id="art_32",
                texte_extrait="Version A",
                valid_from=date(2018, 5, 25),
                valid_to=date(2026, 8, 2),
            ),
        ]

        agent = AgentTemporel()

        with pytest.raises(ValueError, match="Date invalide"):
            agent.filtrer(evidences, date_contexte=date(2025, 2, 30))  # cette date n'existe pas
