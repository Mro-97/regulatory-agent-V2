"""tests/test_integration_ingestion.py — Tests d'intégration ingestion.

Extraits de tests/test_integration.py (§12 étape 6). Regroupe :
  - TestPipelineIngestion : chunking + upsert Qdrant (mémoire).
  - TestIngestionReelle   : Orchestrateur.ingerer() via l'API.

Les fixtures `doc_rgpd_json` et `client_qdrant_memoire` vivent dans
tests/conftest.py (pytest les partage automatiquement).
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from src.models import DocumentReglementaire, SourceReglementaire

# ---------------------------------------------------------------------------
# Test pipeline ingestion → Qdrant
# ---------------------------------------------------------------------------


class TestPipelineIngestion:
    def test_chunking_et_upsert(self, doc_rgpd_json, client_qdrant_memoire):  # noqa: ANN001, ANN201
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

    def test_chunking_article_court(self):  # noqa: ANN201
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

    def test_chunking_article_long(self):  # noqa: ANN201
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
    def _ingester_en_memoire(collection: str = "test_ingest"):  # noqa: ANN205
        from qdrant_client import QdrantClient
        from qdrant_client.http.models import Distance, VectorParams
        from scripts.ingest import Ingester

        client = QdrantClient(location=":memory:")
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=4, distance=Distance.COSINE),
        )

        class FauxModeleEmbedding:
            def encode(self, texte):  # noqa: ANN001, ANN202, ARG002
                return [0.1, 0.2, 0.3, 0.4]

        ingester = Ingester.__new__(Ingester)
        ingester.client = client
        ingester.collection_name = collection
        ingester.embedding_model = FauxModeleEmbedding()
        return ingester

    def test_ingestion_reelle_nouveau_document(self, doc_rgpd_json):  # noqa: ANN001, ANN201
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

    def test_ingestion_sans_contenu_json_leve_valueerror(self):  # noqa: ANN201
        from src.models import RequeteIngestion
        from src.orchestrator import Orchestrateur

        orchestrateur = Orchestrateur(mode="real")
        requete = RequeteIngestion(
            source=SourceReglementaire.EUR_LEX, contenu_json=None
        )

        with pytest.raises(ValueError):
            asyncio.run(orchestrateur.ingerer(requete))

    def test_ingestion_contenu_invalide_leve_valueerror(self):  # noqa: ANN201
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

    def test_ingestion_document_deja_indexe_sans_force(self, doc_rgpd_json):  # noqa: ANN001, ANN201
        from src.models import RequeteIngestion
        from src.orchestrator import DocumentDejaIndexeError, Orchestrateur

        orchestrateur = Orchestrateur(mode="real")
        ingester = self._ingester_en_memoire()
        orchestrateur._obtenir_ingester = lambda: ingester
        requete = RequeteIngestion(
            source=SourceReglementaire.EUR_LEX, contenu_json=doc_rgpd_json
        )

        async def _run():  # noqa: ANN202
            await orchestrateur.ingerer(requete)  # première ingestion, OK
            await orchestrateur.ingerer(requete)  # deuxième, doit échouer

        with pytest.raises(DocumentDejaIndexeError):
            asyncio.run(_run())

    def test_ingestion_document_deja_indexe_avec_force_remplace(self, doc_rgpd_json):  # noqa: ANN001, ANN201
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

        async def _run():  # noqa: ANN202
            r1 = await orchestrateur.ingerer(requete)
            r2 = await orchestrateur.ingerer(requete_force)
            return r1, r2

        r1, r2 = asyncio.run(_run())
        assert r2.nouvelle_version is True
        # Les chunks existants ont été remplacés, pas dupliqués.
        info = ingester.client.get_collection(ingester.collection_name)
        assert (info.points_count or 0) == r2.chunks_indexes == r1.chunks_indexes

    def test_api_ingest_409_si_deja_indexe(self, doc_rgpd_json):  # noqa: ANN001, ANN201
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

    def test_api_ingest_400_si_contenu_json_absent(self):  # noqa: ANN201
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
