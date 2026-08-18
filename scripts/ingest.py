#!/usr/bin/env python3
"""Ingestion d'un JSON réglementaire dans Qdrant.

Usage:
    python scripts/ingest.py --json data/raw/test_rgpd.json --collection regulatory_chunks
"""

import argparse
import json
import logging
import sys
from pathlib import Path
import uuid
from datetime import date
from typing import List, Optional
import mlx_embedding_models  # ou mlx_embeddings selon l'installation
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct

# Ajouter le chemin du projet au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import cfg
from src.models import DocumentReglementaire, MetadonneesChunk

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class Ingester:
    def __init__(self, collection_name: str = "regulatory_chunks", recreate: bool = False):
        self.client = QdrantClient(host=cfg.qdrant_host, port=cfg.qdrant_port)
        self.collection_name = collection_name
        self.embedding_model = self._load_embedding_model()

        if recreate:
            self._recreate_collection()

    def _load_embedding_model(self):
        """Charge le modèle d'embedding MLX (bge-m3) via mlx_utils."""
        from src.mlx_utils import get_embedding
        model = get_embedding(cfg.modele_embedding)
        logger.info(f"Modèle d'embedding : {cfg.modele_embedding}")
        return model

    def _recreate_collection(self):
        """Supprime et recrée la collection Qdrant."""
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)
            logger.info(f"🗑️ Collection '{self.collection_name}' supprimée")
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
        )
        logger.info(f"✅ Collection '{self.collection_name}' créée (dim=1024)")

    def embed_chunk(self, text: str) -> List[float]:
        """Génère un embedding pour un chunk de texte via MLXEmbedding (bge-m3)."""
        vecteur = self.embedding_model.encode(text)
        if isinstance(vecteur, list):
            return vecteur
        if hasattr(vecteur, "tolist"):
            return vecteur.tolist()
        return list(vecteur)

    def chunk_document(self, doc: DocumentReglementaire) -> List[MetadonneesChunk]:
        """Découpe un document en chunks (un par article)."""
        chunks = []
        for chapitre in doc.chapitres:
            for article in chapitre.articles:
                # On peut aussi découper l'article en plusieurs chunks si trop long
                # Ici on prend tout le texte comme un chunk
                chunk = MetadonneesChunk(
                    chunk_id=str(uuid.uuid4()),
                    document_id=doc.id,
                    chapitre_id=chapitre.id,
                    article_id=article.id,
                    source=doc.source,
                    themes=doc.themes,
                    valid_from=article.validite.valid_from,
                    valid_to=article.validite.valid_to,
                    texte_chunk=article.texte,
                    position_dans_article=0,
                )
                chunks.append(chunk)
        return chunks

    def ingest_json(self, json_path: Path) -> None:
        """Ingère un fichier JSON dans Qdrant."""
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Valider avec Pydantic
        # Gerer les fichiers contenant une liste de documents
        if isinstance(data, list):
            for item in data:
                doc = DocumentReglementaire(**item)
                chunks = self.chunk_document(doc)
                if not chunks:
                    continue
                vectors = [self.embed_chunk(c.texte_chunk) for c in chunks]
                points = [PointStruct(id=str(uuid.uuid4()), vector=v, payload={
                    "chunk_id": c.chunk_id, "document_id": c.document_id,
                    "chapitre_id": c.chapitre_id, "article_id": c.article_id,
                    "source": c.source.value, "themes": c.themes,
                    "valid_from": c.valid_from.isoformat(),
                    "valid_to": c.valid_to.isoformat() if c.valid_to else None,
                    "texte_chunk": c.texte_chunk, "position_dans_article": c.position_dans_article,
                }) for c, v in zip(chunks, vectors)]
                self.client.upsert(collection_name=self.collection_name, points=points, wait=True)
                logger.info(f"✅ {len(chunks)} chunks indexés pour {doc.id}")
            return
        doc = DocumentReglementaire(**data)
        logger.info(f"📄 Document chargé : {doc.id} - {doc.titre}")

        # Chunker
        chunks = self.chunk_document(doc)
        logger.info(f"🧩 {len(chunks)} chunks générés")

        # Embedder et indexer
        points = []
        for chunk in chunks:
            vector = self.embed_chunk(chunk.texte_chunk)
            point = PointStruct(
                id=chunk.chunk_id,
                vector=vector,
                payload=chunk.model_dump(mode="json")
            )
            points.append(point)

        if not points:
            logger.warning("Aucun point à indexer")
            return

        # Upsert dans Qdrant
        self.client.upsert(
            collection_name=self.collection_name,
            points=points,
        )
        logger.info(f"✅ {len(points)} chunks indexés dans Qdrant")


def main():
    parser = argparse.ArgumentParser(description="Ingérer un JSON réglementaire dans Qdrant")
    parser.add_argument("--json", required=True, help="Chemin vers le fichier JSON")
    parser.add_argument("--collection", default="regulatory_chunks", help="Nom de la collection Qdrant")
    parser.add_argument("--recreate", action="store_true", help="Recréer la collection")
    args = parser.parse_args()

    ingester = Ingester(collection_name=args.collection, recreate=args.recreate)
    ingester.ingest_json(Path(args.json))


if __name__ == "__main__":
    main()
