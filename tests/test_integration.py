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

import pytest
from src.models import (
    DocumentReglementaire,
    EvidenceRecuperee,
    NiveauConfiance,
    RequeteQuestion,
    SourceReglementaire,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def doc_rgpd_json() -> dict:
    """JSON canonique du document de test."""
    return {
        "id": "RGPD_2016_679",
        "titre": "Règlement (UE) 2016/679",
        "source": "EUR-Lex",
        "publication_date": "2016-05-04",
        "entry_into_force": "2018-05-25",
        "version": "2026-08-03",
        "themes": ["protection_donnees", "numerique"],
        "chapitres": [
            {
                "id": "chap4",
                "titre": "Sécurité",
                "articles": [
                    {
                        "id": "art_32",
                        "titre": "Sécurité du traitement",
                        "texte": (
                            "Compte tenu de l'état des connaissances, des coûts "
                            "de mise en œuvre et de la nature du traitement, le responsable "  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
                            "met en œuvre les mesures techniques et organisationnelles appropriées."  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
                        ),
                        "validite": {"valid_from": "2018-05-25", "valid_to": None},
                        "citations": ["art_33"],
                    },
                    {
                        "id": "art_33",
                        "titre": "Notification d'une violation",
                        "texte": (
                            "En cas de violation de données à caractère personnel, "
                            "le responsable notifie l'autorité de contrôle dans les 72 heures."  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
                        ),
                        "validite": {"valid_from": "2018-05-25", "valid_to": None},
                        "citations": [],
                    },
                ],
            }
        ],
        "textes_lies": [],
    }


@pytest.fixture
def client_qdrant_memoire():
    """Client Qdrant en mémoire pour les tests."""
    from qdrant_client import QdrantClient

    return QdrantClient(location=":memory:")


# ---------------------------------------------------------------------------
# Test modèle Pydantic depuis JSON
# ---------------------------------------------------------------------------


class TestIngestionPydantic:
    def test_charger_document_depuis_json(self, doc_rgpd_json):
        doc = DocumentReglementaire.model_validate(doc_rgpd_json)
        assert doc.id == "RGPD_2016_679"
        assert len(doc.chapitres) == 1
        assert len(doc.chapitres[0].articles) == 2

    def test_hash_document_coherent(self, doc_rgpd_json):
        doc = DocumentReglementaire.model_validate(doc_rgpd_json)
        h1 = doc.calculer_hash()
        h2 = doc.calculer_hash()
        assert h1 == h2

    def test_articles_applicables_2025(self, doc_rgpd_json):
        doc = DocumentReglementaire.model_validate(doc_rgpd_json)
        applicables = doc.articles_applicables_a(date(2025, 6, 15))
        assert len(applicables) == 2  # art_32 et art_33, tous deux sans valid_to


# ---------------------------------------------------------------------------
# Test pipeline ingestion → Qdrant
# ---------------------------------------------------------------------------


class TestPipelineIngestion:
    def test_chunking_et_upsert(self, doc_rgpd_json, client_qdrant_memoire):
        """Vérifie que l'ingestion produit des chunks indexés dans Qdrant."""
        import uuid

        from qdrant_client.http.models import Distance, PointStruct, VectorParams

        doc = DocumentReglementaire.model_validate(doc_rgpd_json)

        # Créer la collection en mémoire
        client_qdrant_memoire.create_collection(
            collection_name="test_collection",
            vectors_config=VectorParams(size=4, distance=Distance.COSINE),
        )

        # Chunking via Ingester
        from scripts.ingest import Ingester

        ingester = Ingester.__new__(Ingester)
        chunks = ingester.chunk_document(doc)
        assert len(chunks) >= 2

        # Upsert avec vecteurs factices
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[0.1, 0.2, 0.3, 0.4],
                payload={
                    "chunk_id": c.chunk_id,
                    "document_id": c.document_id,
                    "article_id": c.article_id,
                    "texte_chunk": c.texte_chunk,
                    "valid_from": c.valid_from.isoformat(),
                    "valid_to": c.valid_to.isoformat() if c.valid_to else None,
                    "source": c.source.value,
                    "themes": c.themes,
                },
            )
            for c in chunks
        ]
        client_qdrant_memoire.upsert(
            collection_name="test_collection",
            points=points,
            wait=True,
        )

        info = client_qdrant_memoire.get_collection("test_collection")
        assert (info.points_count or 0) >= 2

    def test_chunking_article_court(self):
        """Un article court doit produire un seul chunk."""
        from scripts.ingest import Ingester
        from src.models import (
            Chapitre,
            DocumentReglementaire,
            IntervalleValidite,
            VersionArticle,
        )

        doc = DocumentReglementaire(
            id="test",
            titre="Test",
            source=SourceReglementaire.AUTRE,
            publication_date=date(2020, 1, 1),
            entry_into_force=date(2020, 1, 1),
            version="2020-01-01",
            chapitres=[
                Chapitre(
                    id="c1",
                    articles=[
                        VersionArticle(
                            id="art_1",
                            titre="Test",
                            texte="Texte court.",
                            validite=IntervalleValidite(valid_from=date(2020, 1, 1)),
                        )
                    ],
                )
            ],
        )
        ingester = Ingester.__new__(Ingester)
        chunks = ingester.chunk_document(doc)
        assert len(chunks) >= 1
        assert any("Texte court" in c.texte_chunk for c in chunks)

    def test_chunking_article_long(self):
        """Vérifie que chunk_document produit un chunk par article avec le texte complet."""  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
        from scripts.ingest import Ingester
        from src.models import (
            Chapitre,
            DocumentReglementaire,
            IntervalleValidite,
            VersionArticle,
        )

        texte_long = " ".join(["Texte réglementaire."] * 200)
        doc = DocumentReglementaire(
            id="test",
            titre="Test",
            source=SourceReglementaire.AUTRE,
            publication_date=date(2020, 1, 1),
            entry_into_force=date(2020, 1, 1),
            version="2020-01-01",
            chapitres=[
                Chapitre(
                    id="c1",
                    articles=[
                        VersionArticle(
                            id="art_1",
                            titre="Art 1",
                            texte=texte_long,
                            validite=IntervalleValidite(valid_from=date(2020, 1, 1)),
                        ),
                        VersionArticle(
                            id="art_2",
                            titre="Art 2",
                            texte="Second article.",
                            validite=IntervalleValidite(valid_from=date(2020, 1, 1)),
                        ),
                    ],
                )
            ],
        )
        ingester = Ingester.__new__(Ingester)
        chunks = ingester.chunk_document(doc)
        # Un chunk minimum par article
        assert len(chunks) >= 2
        assert any(c.article_id == "art_1" for c in chunks)
        assert any(c.article_id == "art_2" for c in chunks)
        assert all(len(c.texte_chunk) > 0 for c in chunks)


class TestIngestionReelle:
    """
    Tests d'Orchestrateur.ingerer() en mode "real" — l'endpoint /ingest ne
    doit plus retourner un succès factice (bug corrigé) : il chunk, embed
    et upsert réellement dans Qdrant, et refuse honnêtement ce qu'il ne
    sait pas faire (URL non fournie, document déjà indexé sans
    forcer_reindexation).
    """

    @staticmethod
    def _ingester_en_memoire(collection: str = "test_ingest"):
        from qdrant_client import QdrantClient
        from qdrant_client.http.models import Distance, VectorParams
        from scripts.ingest import Ingester

        client = QdrantClient(location=":memory:")
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=4, distance=Distance.COSINE),
        )

        class FauxModeleEmbedding:
            def encode(self, texte):
                return [0.1, 0.2, 0.3, 0.4]

        ingester = Ingester.__new__(Ingester)
        ingester.client = client
        ingester.collection_name = collection
        ingester.embedding_model = FauxModeleEmbedding()
        return ingester

    def test_ingestion_reelle_nouveau_document(self, doc_rgpd_json):
        from src.models import RequeteIngestion
        from src.orchestrator import Orchestrateur

        orchestrateur = Orchestrateur(mode="real")
        ingester = self._ingester_en_memoire()
        orchestrateur._obtenir_ingester = lambda: ingester

        requete = RequeteIngestion(
            source=SourceReglementaire.EUR_LEX, contenu_json=doc_rgpd_json
        )

        reponse = asyncio.run(orchestrateur.ingerer(requete))

        assert reponse.document_id == "RGPD_2016_679"
        assert reponse.chunks_indexes >= 2
        assert reponse.nouvelle_version is False
        assert len(reponse.hash_document) == 64

        info = ingester.client.get_collection(ingester.collection_name)
        assert (info.points_count or 0) == reponse.chunks_indexes

    def test_ingestion_sans_contenu_json_leve_valueerror(self):
        from src.models import RequeteIngestion
        from src.orchestrator import Orchestrateur

        orchestrateur = Orchestrateur(mode="real")
        requete = RequeteIngestion(
            source=SourceReglementaire.EUR_LEX, contenu_json=None
        )

        with pytest.raises(ValueError):
            asyncio.run(orchestrateur.ingerer(requete))

    def test_ingestion_contenu_invalide_leve_valueerror(self):
        from src.models import RequeteIngestion
        from src.orchestrator import Orchestrateur

        orchestrateur = Orchestrateur(mode="real")
        orchestrateur._obtenir_ingester = lambda: self._ingester_en_memoire()
        requete = RequeteIngestion(
            source=SourceReglementaire.EUR_LEX,
            contenu_json={"id": "INCOMPLET"},  # champs requis manquants
        )

        with pytest.raises(ValueError):
            asyncio.run(orchestrateur.ingerer(requete))

    def test_ingestion_document_deja_indexe_sans_force(self, doc_rgpd_json):
        from src.models import RequeteIngestion
        from src.orchestrator import DocumentDejaIndexeError, Orchestrateur

        orchestrateur = Orchestrateur(mode="real")
        ingester = self._ingester_en_memoire()
        orchestrateur._obtenir_ingester = lambda: ingester
        requete = RequeteIngestion(
            source=SourceReglementaire.EUR_LEX, contenu_json=doc_rgpd_json
        )

        async def _run():
            await orchestrateur.ingerer(requete)  # première ingestion, OK
            await orchestrateur.ingerer(requete)  # deuxième, doit échouer

        with pytest.raises(DocumentDejaIndexeError):
            asyncio.run(_run())

    def test_ingestion_document_deja_indexe_avec_force_remplace(self, doc_rgpd_json):
        from src.models import RequeteIngestion
        from src.orchestrator import Orchestrateur

        orchestrateur = Orchestrateur(mode="real")
        ingester = self._ingester_en_memoire()
        orchestrateur._obtenir_ingester = lambda: ingester

        requete = RequeteIngestion(
            source=SourceReglementaire.EUR_LEX, contenu_json=doc_rgpd_json
        )
        requete_force = RequeteIngestion(
            source=SourceReglementaire.EUR_LEX,
            contenu_json=doc_rgpd_json,
            forcer_reindexation=True,
        )

        async def _run():
            r1 = await orchestrateur.ingerer(requete)
            r2 = await orchestrateur.ingerer(requete_force)
            return r1, r2

        r1, r2 = asyncio.run(_run())
        assert r2.nouvelle_version is True
        # Les chunks existants ont été remplacés, pas dupliqués.
        info = ingester.client.get_collection(ingester.collection_name)
        assert (info.points_count or 0) == r2.chunks_indexes == r1.chunks_indexes

    def test_api_ingest_409_si_deja_indexe(self, doc_rgpd_json):
        """L'API /ingest doit retourner 409 quand le document existe déjà sans forcer_reindexation."""  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
        from config import cfg
        from fastapi.testclient import TestClient
        from src import api as api_module
        from src.orchestrator import Orchestrateur

        orchestrateur = Orchestrateur(mode="real")
        ingester = self._ingester_en_memoire()
        orchestrateur._obtenir_ingester = lambda: ingester

        original = api_module._orchestrateur
        api_module._orchestrateur = orchestrateur
        try:
            client = TestClient(api_module.app)
            payload = {"source": "EUR-Lex", "contenu_json": doc_rgpd_json}
            headers = {"X-API-Key": cfg.api_key}
            r1 = client.post("/ingest", json=payload, headers=headers)
            assert r1.status_code == 202
            r2 = client.post("/ingest", json=payload, headers=headers)
            assert r2.status_code == 409
        finally:
            api_module._orchestrateur = original

    def test_api_ingest_400_si_contenu_json_absent(self):
        """L'API /ingest doit retourner 400 (pas un faux succès) sans contenu_json."""
        from config import cfg
        from fastapi.testclient import TestClient
        from src import api as api_module
        from src.orchestrator import Orchestrateur

        original = api_module._orchestrateur
        api_module._orchestrateur = Orchestrateur(mode="real")
        try:
            client = TestClient(api_module.app)
            rep = client.post(
                "/ingest",
                json={"source": "EUR-Lex", "url": "https://example.org/doc"},
                headers={"X-API-Key": cfg.api_key},
            )
            assert rep.status_code == 400
        finally:
            api_module._orchestrateur = original


# ---------------------------------------------------------------------------
# Test pipeline Retriever → Temporal → Explainer
# ---------------------------------------------------------------------------


class TestPipelineAgents:
    def test_pipeline_deterministe_sans_qdrant(self):
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

    def test_pipeline_citation_verification(self):
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
    def test_mode_mock_sans_infrastructure(self):
        """L'orchestrateur en mode mock ne doit nécessiter aucune infra."""
        from src.orchestrator import Orchestrateur

        orch = Orchestrateur(mode="mock")

        async def _run():
            req = RequeteQuestion(question="Test question RGPD")
            rep = await orch.traiter(req)
            assert rep.request_id is not None
            assert "mock" in rep.reponse.lower() or len(rep.reponse) > 0
            assert rep.niveau_confiance is not None

        asyncio.run(_run())

    def test_classification_requetes(self):
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
    def test_retrieval_bloquant_ne_gele_pas_la_boucle_asyncio(self, monkeypatch):
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
            def retrieve(self, **kwargs):
                time.sleep(0.3)
                return []

        monkeypatch.setattr(
            orchestrateur, "_obtenir_retriever", lambda: RetrieverLent()
        )

        async def etape_explainer_rapide(
            question, evidences, type_pipeline, date_ref=None
        ):
            return (
                "réponse simulée",
                NiveauConfiance.ELEVE,
                SortieAgent(nom_agent="Explainer", machine="Mac_B", contenu={}),
            )

        monkeypatch.setattr(orchestrateur, "_etape_explainer", etape_explainer_rapide)

        async def _run():
            t0 = time.monotonic()
            marqueurs: list[float] = []

            async def tache_legere():
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
        ecarts = [b - a for a, b in zip(marqueurs, marqueurs[1:])]
        assert all(ecart < 0.2 for ecart in ecarts), (
            f"tache_legere() n'a pas progressé régulièrement : {ecarts}"
        )


# ---------------------------------------------------------------------------
# Test audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail:
    def test_persistance_locale(self, tmp_path):
        """Vérifie que l'audit JSONL est bien écrit localement."""
        import src.audit as audit_module
        from src.models import EnregistrementAudit

        # Patch du chemin de fichier
        chemin_test = tmp_path / "audit_test.jsonl"
        original = audit_module.CHEMIN_AUDIT_LOCAL
        audit_module.CHEMIN_AUDIT_LOCAL = chemin_test

        async def _run():
            gestionnaire = audit_module.GestionnaireAudit(postgres_dsn=None)
            await gestionnaire.initialiser()

            audit = EnregistrementAudit(
                user_query="Question de test",
                reponse_finale="Réponse de test",
                niveau_confiance=NiveauConfiance.ELEVE,
            )
            hash_retourne = await gestionnaire.persister(audit)
            assert len(hash_retourne) == 64

            # Vérifier le fichier
            assert chemin_test.exists()
            contenu = chemin_test.read_text()
            assert "Question de test" in contenu

        try:
            asyncio.run(_run())
        finally:
            audit_module.CHEMIN_AUDIT_LOCAL = original

    def test_chainaage_hashes(self, tmp_path):
        """Deux audits successifs doivent avoir des hashes chaînés."""
        import src.audit as audit_module
        from src.models import EnregistrementAudit

        chemin_test = tmp_path / "audit_chain.jsonl"
        original = audit_module.CHEMIN_AUDIT_LOCAL
        audit_module.CHEMIN_AUDIT_LOCAL = chemin_test
        audit_module._hash_precedent = None  # reset

        async def _run():
            gestionnaire = audit_module.GestionnaireAudit(postgres_dsn=None)
            await gestionnaire.initialiser()

            a1 = EnregistrementAudit(user_query="Q1", reponse_finale="R1")
            h1 = await gestionnaire.persister(a1)

            a2 = EnregistrementAudit(user_query="Q2", reponse_finale="R2")
            h2 = await gestionnaire.persister(a2)

            assert h1 != h2
            # Le hash précédent de a2 doit être h1
            assert a2.hash_precedent == h1

        try:
            asyncio.run(_run())
        finally:
            audit_module.CHEMIN_AUDIT_LOCAL = original
            audit_module._hash_precedent = None

    def test_verifier_integrite_chaine_intacte(self, tmp_path):
        """Une chaîne d'audit intacte doit être entièrement valide."""
        import src.audit as audit_module
        from src.models import EnregistrementAudit

        chemin_test = tmp_path / "audit_ok.jsonl"
        original = audit_module.CHEMIN_AUDIT_LOCAL
        audit_module.CHEMIN_AUDIT_LOCAL = chemin_test
        audit_module._hash_precedent = None

        async def _run():
            gestionnaire = audit_module.GestionnaireAudit(postgres_dsn=None)
            await gestionnaire.initialiser()

            for i in range(3):
                await gestionnaire.persister(
                    EnregistrementAudit(user_query=f"Q{i}", reponse_finale=f"R{i}")
                )

            resultat = await gestionnaire.verifier_integrite()
            assert resultat["total"] == 3
            assert resultat["valides"] == 3
            assert resultat["invalides"] == 0
            assert resultat["erreurs"] == []

        try:
            asyncio.run(_run())
        finally:
            audit_module.CHEMIN_AUDIT_LOCAL = original
            audit_module._hash_precedent = None

    def test_verifier_integrite_detecte_enregistrement_supprime(self, tmp_path):
        """
        Un enregistrement retiré du milieu du fichier JSONL doit être détecté :
        les deux enregistrements restants sont chacun auto-cohérents (leur
        propre hash_courant reste correct), mais le hash_precedent du
        troisième ne correspond plus au hash_courant du premier une fois
        le deuxième supprimé — la liaison de chaîne est rompue.
        """
        import src.audit as audit_module
        from src.models import EnregistrementAudit

        chemin_test = tmp_path / "audit_trafique.jsonl"
        original = audit_module.CHEMIN_AUDIT_LOCAL
        audit_module.CHEMIN_AUDIT_LOCAL = chemin_test
        audit_module._hash_precedent = None

        async def _run():
            gestionnaire = audit_module.GestionnaireAudit(postgres_dsn=None)
            await gestionnaire.initialiser()

            for i in range(3):
                await gestionnaire.persister(
                    EnregistrementAudit(user_query=f"Q{i}", reponse_finale=f"R{i}")
                )

            # Suppression du deuxième enregistrement (falsification simulée).
            lignes = chemin_test.read_text(encoding="utf-8").strip().splitlines()
            assert len(lignes) == 3
            lignes_trafiquees = [lignes[0], lignes[2]]
            chemin_test.write_text(
                "\n".join(lignes_trafiquees) + "\n", encoding="utf-8"
            )

            resultat = await gestionnaire.verifier_integrite()
            assert resultat["total"] == 2
            assert resultat["invalides"] == 1
            assert resultat["erreurs"][0]["type"] == "chaine_rompue"

        try:
            asyncio.run(_run())
        finally:
            audit_module.CHEMIN_AUDIT_LOCAL = original

    def test_desynchronisation_postgres_comptabilisee(self, tmp_path):
        """
        Si PostgreSQL est censé être actif mais que l'INSERT échoue, la
        persistance locale (source de vérité) doit rester intacte, et
        l'échec doit être comptabilisé et exposé via statut() — plus
        jamais silencieusement ignoré comme auparavant.
        """
        import src.audit as audit_module
        from src.models import EnregistrementAudit

        chemin_test = tmp_path / "audit_desync.jsonl"
        original = audit_module.CHEMIN_AUDIT_LOCAL
        audit_module.CHEMIN_AUDIT_LOCAL = chemin_test
        audit_module._hash_precedent = None

        class FausseAcquisition:
            async def __aenter__(self):
                raise RuntimeError("connexion PostgreSQL indisponible")  # noqa: TRY003

            async def __aexit__(self, *args):
                return False

        class FauxPool:
            def acquire(self):
                return FausseAcquisition()

        async def _run():
            gestionnaire = audit_module.GestionnaireAudit(postgres_dsn=None)
            await gestionnaire.initialiser()
            # Simule un PostgreSQL déclaré actif dont l'INSERT échoue.
            gestionnaire._postgres_ok = True
            gestionnaire._pool = FauxPool()

            audit = EnregistrementAudit(user_query="Q", reponse_finale="R")
            hash_retourne = await gestionnaire.persister(audit)

            # La persistance locale a réussi malgré l'échec PostgreSQL.
            assert chemin_test.exists()
            assert "Q" in chemin_test.read_text()
            assert len(hash_retourne) == 64

            statut = gestionnaire.statut()
            assert statut["postgres_actif"] is True
            assert statut["desynchronisations"] == 1

        try:
            asyncio.run(_run())
        finally:
            audit_module.CHEMIN_AUDIT_LOCAL = original
            audit_module._hash_precedent = None

    def test_health_expose_statut_audit(self):
        """L'endpoint /health doit exposer le statut de synchronisation de l'audit."""
        from fastapi.testclient import TestClient
        from src import api as api_module

        client = TestClient(api_module.app)
        rep = client.get("/health")
        assert rep.status_code == 200
        donnees = rep.json()
        assert "audit" in donnees
        assert "postgres_actif" in donnees["audit"]
        assert "desynchronisations" in donnees["audit"]
