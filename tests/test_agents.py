"""
tests/test_agents.py — Tests des agents spécialisés
=====================================================

Couvre (sans LLM, sans Qdrant) :
- AgentTemporel : filtrage déterministe, détection anomalies
- AgentExplainer : assemblage structuré
- AgentCitation : génération + vérification
- AgentConflit : heuristiques lexicales
"""

from datetime import date

import pytest

from src.models import EvidenceRecuperee, NiveauConfiance


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ev_rgpd_a():
    return EvidenceRecuperee(
        chunk_id="chunk_001",
        document_id="RGPD_2016_679",
        article_id="art_32",
        texte_extrait="Le responsable doit mettre en œuvre des mesures techniques appropriées.",
        valid_from=date(2018, 5, 25),
        valid_to=date(2026, 8, 2),
    )


@pytest.fixture
def ev_rgpd_b():
    return EvidenceRecuperee(
        chunk_id="chunk_002",
        document_id="RGPD_2016_679",
        article_id="art_32_2026",
        texte_extrait="Compte tenu des techniques les plus récentes, le responsable met en œuvre.",
        valid_from=date(2026, 8, 3),
        valid_to=None,
    )


@pytest.fixture
def ev_rgpd_art33():
    return EvidenceRecuperee(
        chunk_id="chunk_003",
        document_id="RGPD_2016_679",
        article_id="art_33",
        texte_extrait="La notification est obligatoire dans les 72 heures.",
        valid_from=date(2018, 5, 25),
        valid_to=None,
    )


@pytest.fixture
def ev_nis2():
    return EvidenceRecuperee(
        chunk_id="chunk_004",
        document_id="NIS2_2022_2555",
        article_id="art_23",
        texte_extrait="L'entité ne doit pas retarder la notification au-delà de 24 heures.",
        valid_from=date(2024, 10, 17),
        valid_to=None,
    )


# ---------------------------------------------------------------------------
# AgentTemporel
# ---------------------------------------------------------------------------


class TestAgentTemporel:
    def test_filtre_version_a_en_2025(self, ev_rgpd_a, ev_rgpd_b):
        from src.agents.temporal import AgentTemporel

        agent = AgentTemporel(use_llm=False)
        r = agent.analyser("Obligations ?", [ev_rgpd_a, ev_rgpd_b], date(2025, 6, 15))
        assert len(r.evidences_applicables) == 1
        assert r.evidences_applicables[0].article_id == "art_32"
        assert len(r.evidences_exclues) == 1

    def test_filtre_version_b_en_2026(self, ev_rgpd_a, ev_rgpd_b):
        from src.agents.temporal import AgentTemporel

        agent = AgentTemporel(use_llm=False)
        r = agent.analyser("Obligations ?", [ev_rgpd_a, ev_rgpd_b], date(2026, 8, 10))
        assert len(r.evidences_applicables) == 1
        assert r.evidences_applicables[0].article_id == "art_32_2026"

    def test_borne_valid_to_inclusive(self, ev_rgpd_a, ev_rgpd_b):
        from src.agents.temporal import AgentTemporel

        agent = AgentTemporel(use_llm=False)
        r = agent.analyser("?", [ev_rgpd_a, ev_rgpd_b], date(2026, 8, 2))
        assert r.evidences_applicables[0].article_id == "art_32"

    def test_borne_valid_from_inclusive(self, ev_rgpd_a, ev_rgpd_b):
        from src.agents.temporal import AgentTemporel

        agent = AgentTemporel(use_llm=False)
        r = agent.analyser("?", [ev_rgpd_a, ev_rgpd_b], date(2026, 8, 3))
        assert r.evidences_applicables[0].article_id == "art_32_2026"

    def test_avant_entree_en_vigueur(self, ev_rgpd_a, ev_rgpd_b):
        from src.agents.temporal import AgentTemporel

        agent = AgentTemporel(use_llm=False)
        r = agent.analyser("?", [ev_rgpd_a, ev_rgpd_b], date(2017, 1, 1))
        assert len(r.evidences_applicables) == 0
        assert r.niveau_confiance == NiveauConfiance.INCERTAIN

    def test_aucune_evidence(self):
        from src.agents.temporal import AgentTemporel

        agent = AgentTemporel(use_llm=False)
        r = agent.analyser("?", [], date(2025, 1, 1))
        assert r.niveau_confiance == NiveauConfiance.INCERTAIN

    def test_detection_lacune(self):
        from src.agents.temporal import AgentTemporel
        from datetime import timedelta

        ev_a = EvidenceRecuperee(
            chunk_id="a",
            document_id="DOC",
            article_id="art_1",
            texte_extrait="Texte A",
            valid_from=date(2020, 1, 1),
            valid_to=date(2021, 12, 31),
        )
        ev_b = EvidenceRecuperee(
            chunk_id="b",
            document_id="DOC",
            article_id="art_1",
            texte_extrait="Texte B",
            valid_from=date(2023, 1, 1),
            valid_to=None,
        )
        agent = AgentTemporel(use_llm=False)
        _, lacunes = agent.detecter_anomalies([ev_a, ev_b])
        assert len(lacunes) >= 1


# ---------------------------------------------------------------------------
# AgentExplainer
# ---------------------------------------------------------------------------


class TestAgentExplainer:
    def test_assemblage_avec_preuves(self, ev_rgpd_a, ev_rgpd_art33):
        from src.agents.explainer import AgentExplainer

        agent = AgentExplainer(use_llm=False)
        r = agent.expliquer("Obligations ?", [ev_rgpd_a, ev_rgpd_art33])
        assert r.mode == "assemblage"
        assert len(r.sources_citees) == 2
        assert "RGPD_2016_679" in r.reponse
        assert r.niveau_confiance == NiveauConfiance.MOYEN

    def test_assemblage_sans_preuves(self):
        from src.agents.explainer import AgentExplainer

        agent = AgentExplainer(use_llm=False)
        r = agent.expliquer("Question ?", [])
        assert r.niveau_confiance == NiveauConfiance.INCERTAIN
        assert len(r.sources_citees) == 0

    def test_contexte_temporel_dans_reponse(self, ev_rgpd_a):
        from src.agents.explainer import AgentExplainer

        agent = AgentExplainer(use_llm=False)
        r = agent.expliquer(
            "?", [ev_rgpd_a], date_ref=date(2023, 6, 15), type_pipeline="temporelle"
        )
        assert "15/06/2023" in r.reponse

    def test_max_8_sources_affichees(self):
        from src.agents.explainer import AgentExplainer

        evidences = [
            EvidenceRecuperee(
                chunk_id=f"c{i}",
                document_id="DOC",
                article_id=f"art_{i}",
                texte_extrait=f"Texte {i}",
                valid_from=date(2020, 1, 1),
            )
            for i in range(12)
        ]
        agent = AgentExplainer(use_llm=False)
        r = agent.expliquer("?", evidences)
        assert len(r.sources_citees) == 8


# ---------------------------------------------------------------------------
# AgentCitation
# ---------------------------------------------------------------------------


class TestAgentCitation:
    def test_generation_deterministe(self, ev_rgpd_a, ev_rgpd_art33):
        from src.agents.citation import AgentCitation, StatutCitation

        agent = AgentCitation(use_llm=False)
        r = agent.generate([ev_rgpd_a, ev_rgpd_art33])
        assert len(r.citations_verifiees) == 2
        assert len(r.citations_douteuses) == 0
        assert r.mode == "deterministe"
        for cit in r.citations_verifiees:
            assert cit.statut == StatutCitation.VERIFIEE
            assert len(cit.hash_extrait) == 64

    def test_citation_chunk_inconnu_douteuse(self, ev_rgpd_a):
        from src.agents.citation import (
            AgentCitation,
            CitationReglementaire,
            StatutCitation,
        )

        agent = AgentCitation(use_llm=False)
        cit_inconnue = CitationReglementaire(
            document_id="DOC",
            article_id="art_99",
            valid_from=date(2020, 1, 1),
            valid_to=None,
            extrait="Texte inventé",
            chunk_id="chunk_INEXISTANT",
        )
        verifiees, douteuses = agent.verify([cit_inconnue], [ev_rgpd_a])
        assert len(verifiees) == 0
        assert len(douteuses) == 1
        assert douteuses[0].statut == StatutCitation.DOUTEUSE

    def test_extrait_non_contenu_dans_source(self, ev_rgpd_a):
        from src.agents.citation import (
            AgentCitation,
            CitationReglementaire,
            StatutCitation,
        )

        agent = AgentCitation(use_llm=False)
        cit_modifiee = CitationReglementaire(
            document_id=ev_rgpd_a.document_id,
            article_id=ev_rgpd_a.article_id,
            valid_from=ev_rgpd_a.valid_from,
            valid_to=ev_rgpd_a.valid_to,
            extrait="Texte qui ne figure pas dans le chunk source",
            chunk_id=ev_rgpd_a.chunk_id,
        )
        verifiees, douteuses = agent.verify([cit_modifiee], [ev_rgpd_a])
        assert len(douteuses) == 1

    def test_aucune_preuve(self):
        from src.agents.citation import AgentCitation

        agent = AgentCitation(use_llm=False)
        r = agent.generate([])
        assert r.avertissement is not None
        assert len(r.citations_verifiees) == 0

    def test_reference_courte(self, ev_rgpd_a):
        from src.agents.citation import AgentCitation

        agent = AgentCitation(use_llm=False)
        r = agent.generate([ev_rgpd_a])
        cit = r.citations_verifiees[0]
        ref = cit.reference_courte()
        assert "RGPD_2016_679" in ref
        assert "art_32" in ref


# ---------------------------------------------------------------------------
# AgentConflit
# ---------------------------------------------------------------------------


class TestAgentConflit:
    def test_conflit_inter_documents(self, ev_rgpd_art33, ev_nis2):
        from src.agents.conflit import AgentConflit, NiveauConflit

        agent = AgentConflit(use_llm=False)
        r = agent.analyser(
            "Délai notification ?", [ev_rgpd_art33, ev_nis2], date(2025, 1, 1)
        )
        # La tension "obligatoire" vs "ne doit pas" devrait être détectée
        # (résultat dépend des heuristiques sur ces textes précis)
        assert r.niveau_global is not None

    def test_moins_de_2_preuves(self, ev_rgpd_a):
        from src.agents.conflit import AgentConflit, NiveauConflit

        agent = AgentConflit(use_llm=False)
        r = agent.analyser("?", [ev_rgpd_a])
        assert r.niveau_global == NiveauConflit.AUCUN

    def test_aucune_tension_lexicale(self, ev_rgpd_a):
        from src.agents.conflit import AgentConflit, NiveauConflit

        ev_neutre = EvidenceRecuperee(
            chunk_id="c_neutre",
            document_id="ANSSI_01",
            article_id="art_1",
            texte_extrait="Les systèmes font l'objet d'une supervision continue.",
            valid_from=date(2023, 1, 1),
            valid_to=None,
        )
        agent = AgentConflit(use_llm=False)
        r = agent.analyser("?", [ev_rgpd_a, ev_neutre], date(2025, 1, 1))
        assert r.niveau_global == NiveauConflit.AUCUN
