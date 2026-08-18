"""
scripts/setup_qdrant.py — Initialisation de la collection Qdrant
================================================================

Crée la collection regulatory_chunks avec les bons paramètres
et les index de payload nécessaires au filtrage temporel.

Usage :
    python3 scripts/setup_qdrant.py
    python3 scripts/setup_qdrant.py --memory   # test en mémoire
    python3 scripts/setup_qdrant.py --reset     # supprime et recrée

Index créés sur le payload :
    - document_id   (keyword) — filtrage par document
    - source        (keyword) — filtrage par source
    - valid_from    (datetime) — filtrage temporel borne inférieure
    - valid_to      (datetime) — filtrage temporel borne supérieure
    - themes        (keyword[]) — filtrage thématique
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def setup_collection(client, collection: str, dimension: int, reset: bool = False) -> None:
    """
    Crée (ou recrée) la collection Qdrant avec tous les index nécessaires.

    Args:
        client:     QdrantClient connecté.
        collection: Nom de la collection.
        dimension:  Dimension des vecteurs d'embedding.
        reset:      Si True, supprime la collection existante avant création.
    """
    from qdrant_client.http.models import (
        Distance,
        PayloadSchemaType,
        VectorParams,
    )

    # Suppression si reset
    if reset and client.collection_exists(collection):
        logger.warning("Suppression de la collection existante : %s", collection)
        client.delete_collection(collection)

    # Création si absente
    if not client.collection_exists(collection):
        logger.info(
            "Création collection '%s' (dim=%d, distance=Cosine)…",
            collection, dimension,
        )
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(
                size=dimension,
                distance=Distance.COSINE,
                on_disk=False,
            ),
        )
        logger.info("Collection créée.")
    else:
        logger.info("Collection '%s' existante — index seulement.", collection)

    # Index de payload pour le filtrage performant
    index_a_creer = [
        ("document_id",  PayloadSchemaType.KEYWORD),
        ("article_id",   PayloadSchemaType.KEYWORD),
        ("source",       PayloadSchemaType.KEYWORD),
        ("themes",       PayloadSchemaType.KEYWORD),
        ("valid_from",   PayloadSchemaType.DATETIME),
        ("valid_to",     PayloadSchemaType.DATETIME),
    ]

    for champ, schema in index_a_creer:
        try:
            client.create_payload_index(
                collection_name=collection,
                field_name=champ,
                field_schema=schema,
            )
            logger.info("Index créé : %s (%s)", champ, schema.value)
        except Exception as exc:
            # L'index existe déjà — ignoré
            logger.debug("Index '%s' déjà présent : %s", champ, exc)

    # Vérification finale
    info = client.get_collection(collection)
    logger.info(
        "Collection prête : %s | vecteurs=%d | statut=%s",
        collection,
        info.vectors_count or 0,
        info.status,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialise la collection Qdrant pour Regulatory Agent V2.",
    )
    parser.add_argument("--memory", action="store_true",
        help="Utilise Qdrant en mémoire (test local).")
    parser.add_argument("--reset", action="store_true",
        help="Supprime et recrée la collection existante.")
    args = parser.parse_args()

    from qdrant_client import QdrantClient

    if args.memory:
        logger.info("Mode test : Qdrant en mémoire.")
        client = QdrantClient(location=":memory:")
    else:
        logger.info("Connexion Qdrant : %s:%d", cfg.qdrant_host, cfg.qdrant_port)
        client = QdrantClient(host=cfg.qdrant_host, port=cfg.qdrant_port)

    setup_collection(
        client=client,
        collection=cfg.qdrant_collection,
        dimension=cfg.qdrant_vecteur_taille,
        reset=args.reset,
    )

    if not args.memory:
        logger.info(
            "Prochaine étape : python3 scripts/ingest.py --fichier data/raw/<doc>.json"
        )


if __name__ == "__main__":
    main()
