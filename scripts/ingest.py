#!/usr/bin/env python3
"""Ingestion d'un JSON réglementaire dans Qdrant avec chunking (600, overlap 50)."""

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import cfg
from src.models import DocumentReglementaire, MetadonneesChunk

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Paramètres de chunking (modifiables)
CHUNK_SIZE = 600
OVERLAP = 50


def _filtre_selector_document(document_id: str) -> Any:
    """Sélecteur Qdrant ciblant tous les points d'un `document_id` donné."""
    from qdrant_client.models import (
        FieldCondition,
        Filter,
        FilterSelector,
        MatchValue,
    )

    return FilterSelector(
        filter=Filter(
            must=[
                FieldCondition(key="document_id", match=MatchValue(value=document_id))
            ]
        )
    )


class Ingester:  # noqa: D101
    def __init__(  # noqa: D107
        self,
        collection_name: str = "regulatory_chunks",
        recreate: bool = False,
    ) -> None:
        # F1 : respecter cfg.qdrant_https + cfg.qdrant_api_key. `url=` seul
        # hardcodait http:// et ignorait la clé Qdrant même si présente.
        scheme = "https" if cfg.qdrant_https else "http"
        self.client = QdrantClient(
            url=f"{scheme}://{cfg.qdrant_host}:{cfg.qdrant_port}",
            api_key=cfg.qdrant_api_key or None,
        )
        self.collection_name = collection_name
        self.embedding_model = self._load_embedding_model()

        if recreate:
            self._recreate_collection()

    def _load_embedding_model(self) -> Any:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]  # noqa: I001 — ancrage single-ligne du type: ignore

        model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Modèle d'embedding : all-MiniLM-L6-v2 (dim=384)")
        return model

    def _recreate_collection(self) -> None:
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)
            logger.info("Collection '%s' supprimée", self.collection_name)
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=384, distance=Distance.COSINE),
        )
        logger.info("Collection '%s' créée (dim=384)", self.collection_name)

    def embed_chunk(self, text: str) -> list[float]:  # noqa: D102
        vec = self.embedding_model.encode(text)
        # SentenceTransformer renvoie un ndarray ; les mocks de tests renvoient
        # directement une liste. Accepter les deux sans conversion agressive.
        return vec.tolist() if hasattr(vec, "tolist") else list(vec)

    def chunk_text(self, text: str) -> list[str]:
        """Découpe un texte en chunks de CHUNK_SIZE caractères avec chevauchement OVERLAP."""  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + CHUNK_SIZE, len(text))
            chunks.append(text[start:end])
            if end >= len(text):
                break
            start += CHUNK_SIZE - OVERLAP
        return chunks

    def chunk_document(self, doc: DocumentReglementaire) -> list[MetadonneesChunk]:  # noqa: D102
        chunks = []
        for chapitre in doc.chapitres:
            for article in chapitre.articles:
                # Découper l'article en chunks
                text_chunks = self.chunk_text(article.texte)
                for idx, chunk_text in enumerate(text_chunks):
                    chunk = MetadonneesChunk(
                        chunk_id=f"{doc.id}_{article.id}_part{idx + 1}",
                        document_id=doc.id,
                        chapitre_id=chapitre.id,
                        article_id=article.id,
                        source=doc.source,
                        themes=doc.themes,
                        valid_from=article.validite.valid_from,
                        valid_to=article.validite.valid_to,
                        texte_chunk=chunk_text,
                        position_dans_article=idx,
                    )
                    chunks.append(chunk)
        return chunks

    def ingest_document(self, doc: DocumentReglementaire) -> int:
        """Chunke `doc`, embed chaque chunk, upsert les points dans Qdrant."""
        chunks = self.chunk_document(doc)
        logger.info("%d chunks générés pour %s", len(chunks), doc.id)
        points = [self._chunk_vers_point(chunk) for chunk in chunks]
        if not points:
            logger.warning("Aucun point à indexer pour %s", doc.id)
            return 0
        self.client.upsert(collection_name=self.collection_name, points=points)
        logger.info("%d points indexés dans Qdrant", len(points))
        return len(points)

    def _chunk_vers_point(self, chunk: Any) -> PointStruct:
        """Convertit un chunk en PointStruct Qdrant (vecteur + payload)."""
        return PointStruct(
            id=str(uuid.uuid4()),
            vector=self.embed_chunk(chunk.texte_chunk),
            payload={
                **chunk.model_dump(mode="json"),
                "original_id": chunk.chunk_id,
            },
        )

    def compter_chunks_existants(self, document_id: str) -> int:
        """Compte le nombre de chunks déjà présents pour un document_id donné.

        Utilisé par l'orchestrateur pour décider si une nouvelle ingestion
        doit renvoyer 409 (déjà indexé) ou remplacer les points existants.
        """
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        filtre = Filter(
            must=[
                FieldCondition(key="document_id", match=MatchValue(value=document_id))
            ]
        )
        try:
            resultat = self.client.count(
                collection_name=self.collection_name,
                count_filter=filtre,
                exact=True,
            )
            return int(resultat.count)
        except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
            # Collection inexistante = 0 chunks. On journalise pour tout autre cas.
            logger.debug("compter_chunks_existants(%s) : %s", document_id, exc)
            return 0

    def supprimer_chunks_document(self, document_id: str) -> int:
        """Supprime tous les points Qdrant d'un `document_id` (retourne le nb)."""
        avant = self.compter_chunks_existants(document_id)
        if avant == 0:
            return 0
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=_filtre_selector_document(document_id),
        )
        logger.info("Supprimé %d chunk(s) pour document_id=%s", avant, document_id)
        return avant

    def ingest_json(self, json_path: Path) -> None:
        """Chemin d'entrée CLI : charge un JSON puis appelle `ingest_document`."""
        with json_path.open(encoding="utf-8") as f:
            data = json.load(f)
        doc = DocumentReglementaire(**data)
        logger.info("Document chargé : %s - %s", doc.id, doc.titre)
        self.ingest_document(doc)


def main() -> None:  # noqa: D103
    parser = argparse.ArgumentParser(
        description="Ingérer un JSON réglementaire dans Qdrant"
    )
    parser.add_argument("--json", required=True, help="Chemin vers le fichier JSON")
    parser.add_argument(
        "--collection", default="regulatory_chunks", help="Nom de la collection Qdrant"
    )
    parser.add_argument("--recreate", action="store_true", help="Recréer la collection")
    args = parser.parse_args()

    ingester = Ingester(collection_name=args.collection, recreate=args.recreate)
    ingester.ingest_json(Path(args.json))


if __name__ == "__main__":
    main()
