"""
tests/test_integration.py — Tests d'intégration pipeline complet
================================================================

Tests sans LLM, avec Qdrant en mémoire et Redis mocké.
Couvre le pipeline : ingestion → retrieval → temporal → explainer → citation.

Nécessite : qdrant-client installé.
Lance avec : python3 -m pytest tests/test_integration.py -v
"""

from __future__ import annotations

import asyncio
from datetime import date

from src.models import (
    DocumentReglementaire,
    EvidenceRecuperee,
    NiveauConfiance,
    RequeteQuestion,
)

# ---------------------------------------------------------------------------
# Fixtures partagées `doc_rgpd_json` et `client_qdrant_memoire`
# déplacées vers tests/conftest.py (§12 étape 6).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test modèle Pydantic depuis JSON
# ---------------------------------------------------------------------------


class TestIngestionPydantic:
    def test_charger_document_depuis_json(self, doc_rgpd_json):  # noqa: ANN001, ANN201
        doc = DocumentReglementaire.model_validate(doc_rgpd_json)
        assert doc.id == "RGPD_2016_679"
        assert len(doc.chapitres) == 1
        assert len(doc.chapitres[0].articles) == 2

    def test_hash_document_coherent(self, doc_rgpd_json):  # noqa: ANN001, ANN201
        doc = DocumentReglementaire.model_validate(doc_rgpd_json)
        h1 = doc.calculer_hash()
        h2 = doc.calculer_hash()
        assert h1 == h2

    def test_articles_applicables_2025(self, doc_rgpd_json):  # noqa: ANN001, ANN201
        doc = DocumentReglementaire.model_validate(doc_rgpd_json)
        applicables = doc.articles_applicables_a(date(2025, 6, 15))
        assert len(applicables) == 2  # art_32 et art_33, tous deux sans valid_to


# ---------------------------------------------------------------------------
# Test pipeline Retriever → Temporal → Explainer
# ---------------------------------------------------------------------------


class TestPipelineAgents:
    def test_pipeline_deterministe_sans_qdrant(self):  # noqa: ANN201
        """
        Pipeline complet sans Qdrant ni LLM.
        Injecte des EvidenceRecuperee directement dans l'Explainer.
        """
        from src.agents.explainer import AgentExplainer
        from src.agents.temporal import AgentTemporel

        evidences = [
            EvidenceRecuperee(
                chunk_id="c1",
                document_id="RGPD_2016_679",
                article_id="art_32",
                texte_extrait="Le responsable met en œuvre des mesures appropriées.",
                valid_from=date(2018, 5, 25),
                valid_to=None,
            ),
            EvidenceRecuperee(
                chunk_id="c2",
                document_id="RGPD_2016_679",
                article_id="art_33",
                texte_extrait="Notification dans les 72 heures.",
                valid_from=date(2018, 5, 25),
                valid_to=None,
            ),
        ]

        # Temporal
        temporal = AgentTemporel(use_llm=False)
        r_temporal = temporal.analyser(
            "Obligations RGPD ?",
            evidences,
            date(2025, 6, 15),
        )
        assert len(r_temporal.evidences_applicables) == 2
        assert r_temporal.niveau_confiance == NiveauConfiance.ELEVE

        # Explainer
        explainer = AgentExplainer(use_llm=False)
        r_explainer = explainer.expliquer(
            "Obligations RGPD ?",
            r_temporal.evidences_applicables,
            date_ref=date(2025, 6, 15),
            type_pipeline="temporelle",
        )
        assert r_explainer.mode == "assemblage"
        assert len(r_explainer.sources_citees) == 2
        assert "RGPD_2016_679" in r_explainer.reponse

    def test_pipeline_citation_verification(self):  # noqa: ANN201
        """Les citations générées depuis les preuves doivent toutes être vérifiées."""
        from src.agents.citation import AgentCitation, StatutCitation

        evidences = [
            EvidenceRecuperee(
                chunk_id=f"chunk_{i}",
                document_id="RGPD_2016_679",
                article_id=f"art_{i}",
                texte_extrait=f"Texte de l'article {i} — contenu réglementaire.",
                valid_from=date(2018, 5, 25),
                valid_to=None,
            )
            for i in range(4)
        ]

        agent = AgentCitation(use_llm=False)
        r = agent.generate(evidences)

        assert len(r.citations_verifiees) == 4
        assert len(r.citations_douteuses) == 0
        for cit in r.citations_verifiees:
            assert cit.statut == StatutCitation.VERIFIEE


# ---------------------------------------------------------------------------
# Test orchestrateur mode mock
# ---------------------------------------------------------------------------


class TestOrchestrateurMock:
    def test_mode_mock_sans_infrastructure(self):  # noqa: ANN201
        """L'orchestrateur en mode mock ne doit nécessiter aucune infra."""
        from src.orchestrator import Orchestrateur

        orch = Orchestrateur(mode="mock")

        async def _run():  # noqa: ANN202
            req = RequeteQuestion(question="Test question RGPD")
            rep = await orch.traiter(req)
            assert rep.request_id is not None
            assert "mock" in rep.reponse.lower() or len(rep.reponse) > 0
            assert rep.niveau_confiance is not None

        asyncio.run(_run())

    def test_classification_requetes(self):  # noqa: ANN201
        from src.orchestrator import _classifier_requete

        assert _classifier_requete("Question simple", None) == "courante"
        assert _classifier_requete("Applicable en 2023", None) == "temporelle"
        assert _classifier_requete("Version du 15/06/2025", None) == "temporelle"
        assert _classifier_requete("Q", date(2023, 6, 15)) == "temporelle"
        assert (
            _classifier_requete("Contradiction entre NIS2 et RGPD", None) == "conflit"
        )
        assert _classifier_requete("Incohérence entre les textes", None) == "conflit"


class TestOrchestrateurNonBloquant:
    def test_retrieval_bloquant_ne_gele_pas_la_boucle_asyncio(self, monkeypatch):  # noqa: ANN001, ANN201
        """
        Le retrieval (embedding + Qdrant, synchrone et potentiellement long)
        doit être déporté dans un thread (asyncio.to_thread) — sinon il
        gèlerait toute la boucle asyncio, donc /health, /pending et le
        Watcher, pendant toute sa durée.

        On simule un retrieval lent (time.sleep) et on vérifie qu'une tâche
        asyncio légère lancée en parallèle continue de progresser à
        intervalles réguliers pendant ce temps, au lieu d'être bloquée
        jusqu'à la fin du retrieval.
        """
        import time

        from src.models import SortieAgent
        from src.orchestrator import Orchestrateur

        orchestrateur = Orchestrateur(mode="real")

        class RetrieverLent:
            def retrieve(self, **kwargs):  # noqa: ANN003, ANN202, ARG002
                time.sleep(0.3)
                return []

        monkeypatch.setattr(
            orchestrateur, "_obtenir_retriever", lambda: RetrieverLent()
        )

        async def etape_explainer_rapide(  # noqa: ANN202
            question,  # noqa: ANN001, ARG001
            evidences,  # noqa: ANN001, ARG001
            type_pipeline,  # noqa: ANN001, ARG001
            date_ref=None,  # noqa: ANN001, ARG001
        ):
            return (
                "réponse simulée",
                NiveauConfiance.ELEVE,
                SortieAgent(nom_agent="Explainer", machine="Mac_B", contenu={}),
            )

        monkeypatch.setattr(orchestrateur, "_etape_explainer", etape_explainer_rapide)

        async def _run():  # noqa: ANN202
            t0 = time.monotonic()
            marqueurs: list[float] = []

            async def tache_legere():  # noqa: ANN202
                for _ in range(6):
                    marqueurs.append(time.monotonic() - t0)
                    await asyncio.sleep(0.05)

            await asyncio.gather(
                orchestrateur.traiter(
                    RequeteQuestion(question="Question test retrieval lent ?")
                ),
                tache_legere(),
            )
            return marqueurs

        marqueurs = asyncio.run(_run())

        # Le premier marqueur doit apparaître quasi immédiatement (~0 s).
        # Si le retrieval bloquait la boucle, tache_legere() n'aurait pu
        # démarrer qu'après les 0.3 s de sleep — ce test échouerait alors
        # avec marqueurs[0] proche de 0.3 au lieu de proche de 0.
        assert marqueurs[0] < 0.15, (
            f"tache_legere() n'a démarré qu'après {marqueurs[0]:.3f}s — "
            f"la boucle asyncio semble avoir été bloquée par le retrieval."
        )
        # Et elle doit continuer à progresser régulièrement pendant que le
        # retrieval tourne en arrière-plan dans son thread.
        ecarts = [b - a for a, b in zip(marqueurs, marqueurs[1:])]  # noqa: B905, RUF007
        assert all(ecart < 0.2 for ecart in ecarts), (
            f"tache_legere() n'a pas progressé régulièrement : {ecarts}"
        )
