#!/usr/bin/env python3
"""Ingestion d'un JSON réglementaire dans Qdrant.

Usage:
    python scripts/ingest.py --json data/raw/test_rgpd.json --collection regulatory_chunks
"""

import argparse
import gc
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import List

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

# Ajouter le chemin du projet au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import cfg
from src.models import DocumentReglementaire, MetadonneesChunk

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class Ingester:
    def __init__(self, collection_name: str = "regulatory_chunks", recreate: bool = False):
        self.client = QdrantClient(
            host=cfg.qdrant_host,
            port=cfg.qdrant_port,
            https=cfg.qdrant_https,
            api_key=cfg.qdrant_api_key or None,
        )
        self.collection_name = collection_name
        self.embedding_model = self._load_embedding_model()

        if recreate:
            self._recreate_collection()

    def _load_embedding_model(self):
        """Charge le modèle d'embedding MLX (bge-m3) via mlx_utils."""
        from src.mlx_utils import get_embedding
        model = get_embedding(cfg.modele_embedding)
        logger.info("Modèle d'embedding : %s", cfg.modele_embedding)
        return model

    def _recreate_collection(self):
        """Supprime et recrée la collection Qdrant."""
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)
            logger.info("Collection '%s' supprimée", self.collection_name)
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
        )
        logger.info("Collection '%s' créée (dim=1024)", self.collection_name)

    def embed_chunk(self, text: str) -> List[float]:
        """Génère un embedding pour un chunk de texte via MLXEmbedding (bge-m3)."""
        vecteur = self.embedding_model.encode(text)
        if isinstance(vecteur, list):
            return vecteur
        if hasattr(vecteur, "tolist"):
            return vecteur.tolist()
        return list(vecteur)

    def chunk_document(self, doc: DocumentReglementaire) -> List[MetadonneesChunk]:
        """Découpe un document en chunks de 700 caractères avec chevauchement de 50."""
        chunks = []
        TAILLE = 700
        CHEVAUCHEMENT = 50

        for chapitre in doc.chapitres:
            for article in chapitre.articles:
                texte = article.texte or ""
                if not texte.strip():
                    continue

                # Découpage en chunks avec chevauchement
                debut = 0
                position = 0
                while debut < len(texte):
                    fin = min(debut + TAILLE, len(texte))
                    morceau = texte[debut:fin].strip()
                    if morceau:
                        chunk_id = f"{doc.id}_{article.id}_{position}"
                        chunks.append(MetadonneesChunk(
                            chunk_id=str(uuid.uuid4()),
                            document_id=doc.id,
                            chapitre_id=chapitre.id,
                            article_id=article.id,
                            source=doc.source,
                            themes=doc.themes,
                            valid_from=article.validite.valid_from,
                            valid_to=article.validite.valid_to,
                            texte_chunk=morceau,
                            position_dans_article=position,
                        ))
                        position += 1
                    debut += TAILLE - CHEVAUCHEMENT

        return chunks
    
    def compter_chunks_existants(self, document_id: str) -> int:
        """Compte les chunks déjà indexés pour un document_id donné."""
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        resultat = self.client.count(
            collection_name=self.collection_name,
            count_filter=Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
            ),
            exact=True,
        )
        return resultat.count

    def supprimer_chunks_document(self, document_id: str) -> None:
        """Supprime tous les chunks déjà indexés pour un document_id donné."""
        from qdrant_client.http.models import FieldCondition, Filter, FilterSelector, MatchValue

        self.client.delete(
            collection_name=self.collection_name,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
                )
            ),
        )
        logger.info("Chunks existants supprimés pour document_id=%s", document_id)

    def ingest_document(self, doc: DocumentReglementaire, batch_size: int = 50) -> int:
        """
        Chunk, embed et upsert un DocumentReglementaire déjà validé.

        Args:
            doc:        Document à ingérer.
            batch_size: Taille des lots d'upsert.

        Returns:
            Nombre de chunks effectivement indexés.
        """
        chunks = self.chunk_document(doc)
        if not chunks:
            return 0

        logger.info("Document : %s — %d chunks", doc.id, len(chunks))
        total_ingeres = 0

        for i in range(0, len(chunks), batch_size):
            lot = chunks[i:i + batch_size]
            points = []
            for chunk in lot:
                try:
                    vecteur = self.embed_chunk(chunk.texte_chunk)
                    points.append(PointStruct(
                        id=str(uuid.uuid4()),
                        vector=vecteur,
                        payload={
                            "chunk_id": chunk.chunk_id,
                            "document_id": chunk.document_id,
                            "chapitre_id": chunk.chapitre_id,
                            "article_id": chunk.article_id,
                            "source": chunk.source.value,
                            "themes": chunk.themes,
                            "valid_from": chunk.valid_from.isoformat(),
                            "valid_to": chunk.valid_to.isoformat() if chunk.valid_to else None,
                            "texte_chunk": chunk.texte_chunk,
                            "position_dans_article": chunk.position_dans_article,
                        }
                    ))
                except Exception as e:
                    logger.error("Erreur embedding : %s", e)
                    continue

            if points:
                try:
                    self.client.upsert(collection_name=self.collection_name, points=points, wait=True)
                    total_ingeres += len(points)
                    logger.info(
                        "Lot %d : %d chunks (%d total)",
                        i // batch_size + 1, len(points), total_ingeres,
                    )
                except Exception as e:
                    logger.error("Erreur upsert : %s", e)

            del points, lot
            gc.collect()

        return total_ingeres

    def ingest_json(self, json_path: Path, batch_size: int = 50) -> None:
        """Ingere un fichier JSON dans Qdrant par lots."""
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        documents = data if isinstance(data, list) else [data]
        total_ingeres = 0

        for data_item in documents:
            try:
                doc = DocumentReglementaire(**data_item)
            except Exception as e:
                logger.error("Erreur validation : %s", e)
                continue

            total_ingeres += self.ingest_document(doc, batch_size=batch_size)

        logger.info("Ingestion terminee : %d chunks", total_ingeres)


def main():
    parser = argparse.ArgumentParser(description="Ingérer un JSON réglementaire dans Qdrant")
    parser.add_argument("--json", required=True, help="Chemin vers le fichier JSON")
    parser.add_argument("--collection", default="regulatory_chunks", help="Nom de la collection Qdrant")
    parser.add_argument("--recreate", action="store_true", help="Recréer la collection")
    parser.add_argument("--batch-size", type=int, default=50, help="Taille des lots")
    args = parser.parse_args()

    ingester = Ingester(collection_name=args.collection, recreate=args.recreate)
    ingester.ingest_json(Path(args.json), batch_size=args.batch_size)


if __name__ == "__main__":
    main()
