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

# Namespace pour dériver un id de point Qdrant stable depuis un chunk_id.
_NS_CHUNK = uuid.uuid5(uuid.NAMESPACE_URL, "regulatory-agent/chunk")


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
        """Backend d'embedding — le MÊME que le retriever (source unique).

        `cfg.modele_embedding` (bge-m3, dim `cfg.embedding_dimension`) au
        lieu d'un `all-MiniLM-L6-v2` local 384-dim : ingérer avec un modèle
        différent de la recherche produisait des vecteurs incompatibles
        avec la collection.
        """
        from src.mlx_utils import get_embedding

        return get_embedding(cfg.modele_embedding)

    def _recreate_collection(self) -> None:
        dim = cfg.embedding_dimension
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)
            logger.info("Collection '%s' supprimée", self.collection_name)
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        logger.info("Collection '%s' créée (dim=%d)", self.collection_name, dim)

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
        mini = cfg.ingest_taille_min_chunk
        for chapitre in doc.chapitres:
            for article in chapitre.articles:
                # Découper l'article en chunks
                text_chunks = self.chunk_text(article.texte)
                for idx, chunk_text in enumerate(text_chunks):
                    if len(chunk_text.strip()) < mini:
                        logger.debug(
                            "Chunk écarté (%d < %d car.) : %s_%s_part%d",
                            len(chunk_text.strip()),
                            mini,
                            doc.id,
                            article.id,
                            idx + 1,
                        )
                        continue
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
        """Ré-indexe `doc` : purge ses points existants, puis chunk + embed + upsert.

        Idempotent : deux appels successifs sur le même document laissent
        la collection identique (les `id` de points sont dérivés du
        `chunk_id` — cf. `_chunk_vers_point` — et la purge en tête retire
        les chunks devenus orphelins si le découpage a changé).
        """
        self.supprimer_chunks_document(doc.id)
        chunks = self.chunk_document(doc)
        logger.info("%d chunks générés pour %s", len(chunks), doc.id)
        chunks_traites = self._appliquer_sanitizer(chunks)
        points = [self._chunk_vers_point(c) for c in chunks_traites]
        if not points:
            logger.warning("Aucun point à indexer pour %s", doc.id)
            return 0
        self.client.upsert(collection_name=self.collection_name, points=points)
        logger.info("%d points indexés dans Qdrant", len(points))
        return len(points)

    def _appliquer_sanitizer(
        self, chunks: list[MetadonneesChunk]
    ) -> list[MetadonneesChunk]:
        """Filtre + annote les chunks selon `cfg.ingest_mode_sanitizer`."""
        from src.ingest_sanitizer import ModeSanitizer, appliquer_politique

        try:
            mode = ModeSanitizer(cfg.ingest_mode_sanitizer)
        except ValueError:
            logger.warning(
                "ingest_mode_sanitizer='%s' inconnu, fallback 'annoter'.",
                cfg.ingest_mode_sanitizer,
            )
            mode = ModeSanitizer.ANNOTER
        conserves: list[MetadonneesChunk] = []
        for chunk in chunks:
            traite = appliquer_politique(chunk.texte_chunk, mode, chunk.chunk_id)
            if traite is None:
                continue
            if traite != chunk.texte_chunk:
                chunk = chunk.model_copy(update={"texte_chunk": traite})
            conserves.append(chunk)
        return conserves

    def _chunk_vers_point(self, chunk: Any) -> PointStruct:
        """Convertit un chunk en PointStruct Qdrant (id déterministe = uuid5(chunk_id)).

        L'`id` dérive du `chunk_id` stable (`{doc}_{article}_part{n}`) :
        ré-ingérer un chunk inchangé écrase le point au lieu d'en créer
        un doublon (cause des ~14 k doublons exacts dans la collection).
        """
        return PointStruct(
            id=str(uuid.uuid5(_NS_CHUNK, chunk.chunk_id)),
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
