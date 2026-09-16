"""tests/test_ingest_lots.py — ingestion par lots (mémoire et payload bornés).

Un document volumineux était traité d'un seul bloc : tous les points, leurs
vecteurs et le JSON d'upsert restaient en mémoire simultanément, et le process
se faisait tuer sans trace. L'upsert unique dépassait en outre la limite de
32 Mo de Qdrant (« JSON payload is larger than allowed ») : REACH (12 992
chunks) était silencieusement perdu à chaque ingestion.

Ce test verrouille le découpage : plusieurs upserts, chacun sous la taille de
lot, et un total identique au nombre de chunks.
"""

from __future__ import annotations

import sys
import types

# Stubs MLX : l'ingestion instancie le modèle d'embedding au constructeur.
for _nom in ("mlx", "mlx.core", "mlx_lm", "mlx_lm.sample_utils", "mlx_embeddings"):
    if _nom not in sys.modules:
        sys.modules[_nom] = types.ModuleType(_nom)
sys.modules["mlx.core"].eval = lambda *a, **k: None  # noqa: ARG005 — stub

from datetime import date  # noqa: E402

from scripts.ingest import TAILLE_LOT_UPSERT, Ingester  # noqa: E402
from src.models import (  # noqa: E402
    Chapitre,
    DocumentReglementaire,
    IntervalleValidite,
    SourceReglementaire,
    VersionArticle,
)


class _FauxClient:
    """Client Qdrant simulé : enregistre la taille de chaque upsert."""

    def __init__(self) -> None:
        self.lots: list[int] = []
        self.supprimes = 0

    def upsert(self, *, collection_name: str, points: list) -> None:  # noqa: ARG002
        self.lots.append(len(points))

    def count(self, **_kwargs: object) -> object:
        return types.SimpleNamespace(count=0)

    def delete(self, **_kwargs: object) -> None:
        self.supprimes += 1

    def collection_exists(self, _nom: str) -> bool:
        return True


class _IngesterTest(Ingester):
    """Ingester dont le client et l'embedding sont simulés (aucun réseau)."""

    def __init__(self) -> None:
        self.client = _FauxClient()
        self.collection_name = "test"
        self.embedding_model = types.SimpleNamespace(
            encode=lambda _t: [0.1] * 8,
            encode_batch=lambda textes: [[0.1] * 8 for _ in textes],
        )


def _document(nb_articles: int) -> DocumentReglementaire:
    articles = [
        VersionArticle(
            id=f"art_{i}",
            titre=f"Article {i}",
            texte="Texte réglementaire. " * 20,
            validite=IntervalleValidite(valid_from=date(2024, 1, 1)),
        )
        for i in range(nb_articles)
    ]
    return DocumentReglementaire(
        id="DOC_TEST",
        titre="Document de test",
        source=SourceReglementaire.EUR_LEX,
        publication_date=date(2024, 1, 1),
        entry_into_force=date(2024, 1, 1),
        version="2024-01-01",
        chapitres=[Chapitre(id="chap1", titre="Chapitre 1", articles=articles)],
    )


class TestIngestionParLots:
    def test_upsert_decoupe_en_lots(self) -> None:
        """Un document volumineux produit plusieurs upserts bornés."""
        ingester = _IngesterTest()
        nb = ingester.ingest_document(_document(TAILLE_LOT_UPSERT + 10))
        assert nb > TAILLE_LOT_UPSERT, "le document doit dépasser un lot"
        assert len(ingester.client.lots) >= 2, "l'ingestion doit être découpée"
        assert all(taille <= TAILLE_LOT_UPSERT for taille in ingester.client.lots)
        assert sum(ingester.client.lots) == nb

    def test_document_court_un_seul_upsert(self) -> None:
        """Un petit document reste traité en un seul lot."""
        ingester = _IngesterTest()
        nb = ingester.ingest_document(_document(3))
        assert ingester.client.lots == [nb]

    def test_document_sans_chunk_ne_upsert_rien(self) -> None:
        """Aucun chunk exploitable : aucun upsert, aucun point annoncé."""
        ingester = _IngesterTest()
        doc = _document(0)
        assert ingester.ingest_document(doc) == 0
        assert ingester.client.lots == []
