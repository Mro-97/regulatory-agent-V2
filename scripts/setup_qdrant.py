"""scripts/setup_qdrant.py — Initialisation de la collection Qdrant
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
"""  # noqa: D205, D415

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import cfg
from qdrant_client import QdrantClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def setup_collection(
    client: QdrantClient,
    collection: str,
    dimension: int,
    reset: bool = False,
) -> None:
    """Idempotent : crée/recrée `collection` (VectorParams Cosine) + indexes payload."""
    _reinitialiser_si_demande(client, collection, reset)
    _creer_collection_si_absente(client, collection, dimension)
    _creer_indexes_payload(client, collection)
    _journaliser_etat_collection(client, collection)


def _reinitialiser_si_demande(
    client: QdrantClient, collection: str, reset: bool
) -> None:
    """Supprime la collection existante quand `reset=True`."""
    if reset and client.collection_exists(collection):
        logger.warning("Suppression de la collection existante : %s", collection)
        client.delete_collection(collection)


def _creer_collection_si_absente(
    client: QdrantClient, collection: str, dimension: int
) -> None:
    """Crée la collection avec VectorParams Cosine si elle n'existe pas."""
    from qdrant_client.http.models import Distance, VectorParams

    if client.collection_exists(collection):
        logger.info("Collection '%s' existante — index seulement.", collection)
        return
    logger.info(
        "Création collection '%s' (dim=%d, distance=Cosine)…",
        collection,
        dimension,
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


def _creer_indexes_payload(client: QdrantClient, collection: str) -> None:
    """Crée les 6 indexes de payload nécessaires au filtrage temporel/thématique."""
    from qdrant_client.http.models import PayloadSchemaType

    index_a_creer = [
        ("document_id", PayloadSchemaType.KEYWORD),
        ("article_id", PayloadSchemaType.KEYWORD),
        ("source", PayloadSchemaType.KEYWORD),
        ("themes", PayloadSchemaType.KEYWORD),
        ("valid_from", PayloadSchemaType.DATETIME),
        ("valid_to", PayloadSchemaType.DATETIME),
    ]
    for champ, schema in index_a_creer:
        try:
            client.create_payload_index(
                collection_name=collection,
                field_name=champ,
                field_schema=schema,
            )
            logger.info("Index créé : %s (%s)", champ, schema.value)
        except Exception as exc:  # noqa: BLE001 — index déjà présent, tolérable, cf. skill §8
            logger.debug("Index '%s' déjà présent : %s", champ, exc)


def _journaliser_etat_collection(client: QdrantClient, collection: str) -> None:
    """Trace vecteurs/statut de la collection après setup."""
    info = client.get_collection(collection)
    logger.info(
        "Collection prête : %s | vecteurs=%d | statut=%s",
        collection,
        info.points_count or 0,
        info.status,
    )


def main() -> None:
    """Entrée CLI : parse `--memory`/`--reset`, se connecte et appelle setup."""
    args = _parser_arguments().parse_args()
    client = _nouveau_client(memory=args.memory)
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


def _parser_arguments() -> argparse.ArgumentParser:
    """ArgumentParser CLI de setup_qdrant."""
    parser = argparse.ArgumentParser(
        description="Initialise la collection Qdrant pour Regulatory Agent V2.",
    )
    parser.add_argument(
        "--memory", action="store_true", help="Utilise Qdrant en mémoire (test local)."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Supprime et recrée la collection existante.",
    )
    return parser


def _nouveau_client(memory: bool) -> QdrantClient:
    """Instancie un client Qdrant (mémoire pour tests, sinon `cfg.qdrant_*`)."""
    from qdrant_client import QdrantClient

    if memory:
        logger.info("Mode test : Qdrant en mémoire.")
        return QdrantClient(location=":memory:")
    logger.info("Connexion Qdrant : %s:%d", cfg.qdrant_host, cfg.qdrant_port)
    return QdrantClient(
        host=cfg.qdrant_host,
        port=cfg.qdrant_port,
        https=cfg.qdrant_https,
        api_key=cfg.qdrant_api_key or None,
    )


if __name__ == "__main__":
    main()
