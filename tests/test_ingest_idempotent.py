"""tests/test_ingest_idempotent.py — ré-ingestion sans doublons.

Avant : `_chunk_vers_point` tirait un `uuid4()` par point → ré-ingérer
un document empilait des copies (cause des ~14 k doublons exacts dans
la collection). Désormais l'`id` dérive du `chunk_id` (uuid5) et
`ingest_document` purge le document avant de réinsérer.
"""

from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams
from scripts.ingest import Ingester
from src.models import DocumentReglementaire


class _FauxEmbedding:
    def encode(self, _texte: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]


def _ingester(collection: str = "test_idem") -> Ingester:
    client = QdrantClient(location=":memory:")
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    ing = Ingester.__new__(Ingester)
    ing.client = client
    ing.collection_name = collection
    ing.embedding_model = _FauxEmbedding()
    return ing


def _doc(doc_rgpd_json: dict) -> DocumentReglementaire:  # type: ignore[type-arg]
    return DocumentReglementaire.model_validate(doc_rgpd_json)


def test_reingestion_ne_double_pas_les_points(doc_rgpd_json) -> None:  # noqa: ANN001
    ing = _ingester()
    doc = _doc(doc_rgpd_json)

    n1 = ing.ingest_document(doc)
    c1 = ing.client.get_collection(ing.collection_name).points_count
    n2 = ing.ingest_document(doc)
    c2 = ing.client.get_collection(ing.collection_name).points_count

    assert n1 == n2
    assert c1 == c2 == n1


def test_ids_points_stables_entre_deux_ingestions(doc_rgpd_json) -> None:  # noqa: ANN001
    ing = _ingester()
    doc = _doc(doc_rgpd_json)

    ing.ingest_document(doc)
    ids1 = {p.id for p in ing.client.scroll(ing.collection_name, limit=100)[0]}
    ing.ingest_document(doc)
    ids2 = {p.id for p in ing.client.scroll(ing.collection_name, limit=100)[0]}

    assert ids1 == ids2
