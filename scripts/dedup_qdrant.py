#!/usr/bin/env python3
"""scripts/dedup_qdrant.py — purge des doublons EXACTS de la collection Qdrant.

Contexte : une ré-ingestion CLI répétée (avant le fix des ids déterministes
dans scripts/ingest.py) a empilé le même chunk plusieurs fois. Deux points
sont des doublons EXACTS quand ils partagent (document_id, article_id,
texte_chunk) — texte identique donc vecteur identique, aucune perte
d'information à n'en garder qu'un.

Ne touche PAS aux paires `X` / `X_FULL` (document_id distincts) : c'est un
choix éditorial (Phase 2), pas un doublon mécanique.

Usage :
    python scripts/dedup_qdrant.py            # dry-run, lecture seule
    python scripts/dedup_qdrant.py --apply    # snapshot puis suppression
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import cfg
from qdrant_client import QdrantClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("dedup_qdrant")

_LOT = 2000


def _client() -> QdrantClient:
    """Fabrique un client Qdrant depuis cfg (host/port/https/api_key)."""
    scheme = "https" if cfg.qdrant_https else "http"
    return QdrantClient(
        url=f"{scheme}://{cfg.qdrant_host}:{cfg.qdrant_port}",
        api_key=cfg.qdrant_api_key or None,
        timeout=120,
    )


def _empreinte(payload: dict[str, Any]) -> str:
    """Clé d'unicité d'un chunk : document_id | article_id | hash(texte)."""
    h = hashlib.sha256(str(payload.get("texte_chunk", "")).encode("utf-8")).hexdigest()
    return f"{payload.get('document_id')}|{payload.get('article_id')}|{h}"


def _grouper(client: QdrantClient, collection: str) -> tuple[int, dict[str, list[Any]]]:
    """Parcourt la collection et regroupe les ids par empreinte exacte."""
    groupes: dict[str, list[Any]] = defaultdict(list)
    total = 0
    offset: Any = None
    while True:
        points, offset = client.scroll(
            collection,
            limit=1000,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            total += 1
            groupes[_empreinte(point.payload or {})].append(point.id)
        if offset is None:
            return total, groupes


def _ids_redondants(groupes: dict[str, list[Any]]) -> list[Any]:
    """Pour chaque groupe de taille > 1, garde le plus petit id, rend les autres."""
    surplus: list[Any] = []
    for ids in groupes.values():
        if len(ids) > 1:
            surplus.extend(sorted(ids, key=str)[1:])
    return surplus


def _supprimer(client: QdrantClient, collection: str, ids: list[Any]) -> None:
    """Snapshot de sécurité puis suppression des `ids` par lots."""
    snap = client.create_snapshot(collection_name=collection, wait=True)
    logger.info("snapshot : %s", getattr(snap, "name", snap))
    for debut in range(0, len(ids), _LOT):
        lot = ids[debut : debut + _LOT]
        client.delete(collection_name=collection, points_selector=lot, wait=True)
        logger.info("  supprimé %d/%d", debut + len(lot), len(ids))


def main() -> None:
    """Point d'entrée CLI : dry-run par défaut, `--apply` pour exécuter."""
    parser = argparse.ArgumentParser(description="Purge des doublons exacts Qdrant.")
    parser.add_argument("--apply", action="store_true", help="exécute les suppressions")
    args = parser.parse_args()

    client = _client()
    collection = cfg.qdrant_collection
    total, groupes = _grouper(client, collection)
    a_supprimer = _ids_redondants(groupes)

    logger.info("total points        : %d", total)
    logger.info("groupes distincts   : %d", len(groupes))
    logger.info("points à supprimer  : %d", len(a_supprimer))
    logger.info("points restants     : %d", total - len(a_supprimer))

    if not args.apply:
        logger.info("\nDRY-RUN — rien supprimé. Relancer avec --apply pour exécuter.")
        return
    if not a_supprimer:
        logger.info("\nRien à supprimer.")
        return
    _supprimer(client, collection, a_supprimer)
    final = client.get_collection(collection).points_count
    logger.info("\npoints_count final : %s", final)


if __name__ == "__main__":
    main()
