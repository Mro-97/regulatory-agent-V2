"""tests/test_ingest_reindexation_zero_chunk.py — M11 : pas de purge sans chunks.

`Ingester.ingest_document` (scripts/ingest.py) purge les points du document
AVANT de chunker. Avec `forcer_reindexation=true` et un découpage qui ne
produit aucun chunk survivant (seuil `ingest_taille_min_chunk`, sanitizer en
mode `bloquer`), le document disparaissait du corpus alors que l'API
répondait 200 `chunks_indexes=0`. `orchestrator_ingest` construit désormais
les chunks (chunker + sanitizer) avant toute purge et refuse la
réindexation si le résultat est vide.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.errors import DocumentAlreadyIndexedError, InvalidDocumentError
from src.models import RequeteIngestion, SourceReglementaire
from src.orchestrator_ingest import _resoudre_conflit_reindexation


class FauxIngester:
    """Ingester minimal : chunker + sanitizer simulés, sans Qdrant ni MLX."""

    def __init__(
        self,
        *,
        existants: int,
        chunks: list[object],
        sanitizer_bloque: bool = False,
    ) -> None:
        self.existants = existants
        self.chunks = chunks
        self.sanitizer_bloque = sanitizer_bloque
        self.purges = 0
        self.ingestions = 0

    def compter_chunks_existants(self, _document_id: str) -> int:
        """Nombre de chunks déjà indexés (simulé)."""
        return self.existants

    def chunk_document(self, _doc: object) -> list[object]:
        """Chunks produits par le chunker (simulés)."""
        return list(self.chunks)

    def _appliquer_sanitizer(self, chunks: list[object]) -> list[object]:
        """Sanitizer : peut vider la liste (mode `bloquer`)."""
        return [] if self.sanitizer_bloque else list(chunks)

    def supprimer_chunks_document(self, _document_id: str) -> int:
        """Purge simulée."""
        self.purges += 1
        return self.existants

    def ingest_document(self, _doc: object) -> int:
        """Ingestion simulée."""
        self.ingestions += 1
        return len(self.chunks)


def _doc() -> SimpleNamespace:
    return SimpleNamespace(id="DOC_TEST_2024_1")


def _requete(*, forcer: bool) -> RequeteIngestion:
    return RequeteIngestion(
        source=SourceReglementaire.EUR_LEX,
        forcer_reindexation=forcer,
    )


class TestReindexationSansChunk:
    def test_aucun_chunk_refuse_et_ne_purge_pas(self) -> None:
        ingester = FauxIngester(existants=3, chunks=[])
        with pytest.raises(InvalidDocumentError):
            _resoudre_conflit_reindexation(ingester, _doc(), _requete(forcer=True))
        assert ingester.purges == 0

    def test_sanitizer_qui_vide_tout_refuse_et_ne_purge_pas(self) -> None:
        ingester = FauxIngester(existants=2, chunks=[object()], sanitizer_bloque=True)
        with pytest.raises(InvalidDocumentError):
            _resoudre_conflit_reindexation(ingester, _doc(), _requete(forcer=True))
        assert ingester.purges == 0

    def test_chunks_presents_purge_puis_continue(self) -> None:
        ingester = FauxIngester(existants=2, chunks=[object()])
        nb = _resoudre_conflit_reindexation(ingester, _doc(), _requete(forcer=True))
        assert nb == 2
        assert ingester.purges == 1

    def test_sans_forcer_leve_deja_indexe(self) -> None:
        ingester = FauxIngester(existants=3, chunks=[object()])
        with pytest.raises(DocumentAlreadyIndexedError):
            _resoudre_conflit_reindexation(ingester, _doc(), _requete(forcer=False))
        assert ingester.purges == 0
