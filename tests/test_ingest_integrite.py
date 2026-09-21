"""tests/test_ingest_integrite.py — l'ingestion n'indexe pas de texte inversé.

Les pages PDF pivotées de REACH ressortaient lues à l'envers et étaient
indexées telles quelles : leurs vecteurs attiraient des requêtes sans
rapport. `Ingester._preparer_chunks` les écarte avant l'embedding ; la
réparation, elle, se fait à la source (`scripts/pdf_parsing.py`).
"""

from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams

from scripts.ingest import Ingester
from src.corpus_integrite import est_inverse
from src.models import (
    Chapitre,
    DocumentReglementaire,
    IntervalleValidite,
    SourceReglementaire,
    VersionArticle,
)

TEXTE_NORMAL = (
    "L'article 33 du reglement prevoit que le responsable du traitement "
    "notifie la violation de donnees a l'autorite de controle dans les "
    "soixante-douze heures. Cette notification est accompagnee des "
    "informations que la commission exige pour la securite des personnes."
)
TEXTE_INVERSE = TEXTE_NORMAL[::-1]

COLLECTION = "test_integrite"


class _FauxEmbedding:
    """Embedding déterministe : l'ingestion n'a besoin d'aucun modèle."""

    def encode(self, _texte: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]

    def encode_batch(self, textes: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in textes]


def _ingester() -> Ingester:
    """Ingester branché sur un Qdrant en mémoire (aucun réseau, aucun MLX)."""
    client = QdrantClient(location=":memory:")
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    ing = Ingester.__new__(Ingester)
    ing.client = client
    ing.collection_name = COLLECTION
    ing.embedding_model = _FauxEmbedding()
    return ing


def _doc(*textes: str) -> DocumentReglementaire:
    articles = [
        VersionArticle(
            id=f"art_{i}",
            titre=f"Article {i}",
            texte=texte,
            validite=IntervalleValidite(valid_from="2018-05-25"),
        )
        for i, texte in enumerate(textes, 1)
    ]
    return DocumentReglementaire(
        id="DOC_TEST_2024_1",
        titre="Doc test",
        source=SourceReglementaire.EUR_LEX,
        publication_date="2024-01-01",
        entry_into_force="2024-01-01",
        version="2024-01-01",
        chapitres=[Chapitre(id="c1", titre="C1", articles=articles)],
    )


class TestEcarterInverses:
    def test_les_deux_textes_sont_bien_opposes(self) -> None:
        """Prémisse du test : le texte inversé est détecté, l'autre non."""
        assert est_inverse(TEXTE_INVERSE)
        assert not est_inverse(TEXTE_NORMAL)

    def test_preparer_chunks_ecarte_l_inverse(self) -> None:
        ing = _ingester()
        chunks = ing.chunk_document(_doc(TEXTE_INVERSE, TEXTE_NORMAL))
        assert len(chunks) == 2, "les deux articles doivent produire un chunk"
        gardes = ing._preparer_chunks(chunks)
        assert len(gardes) == 1
        assert gardes[0].article_id == "art_2"

    def test_ingest_document_n_indexe_pas_l_inverse(self) -> None:
        ing = _ingester()
        nb = ing.ingest_document(_doc(TEXTE_INVERSE, TEXTE_NORMAL))
        assert nb == 1
        info = ing.client.get_collection(COLLECTION)
        assert info.points_count == 1

    def test_document_entierement_inverse_indexe_zero(self) -> None:
        ing = _ingester()
        assert ing.ingest_document(_doc(TEXTE_INVERSE)) == 0
        info = ing.client.get_collection(COLLECTION)
        assert info.points_count == 0
