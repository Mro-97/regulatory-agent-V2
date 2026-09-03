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
    python scripts/dedup_qdrant.py                 # dry-run doublons exacts
    python scripts/dedup_qdrant.py --apply         # snapshot puis suppression
    python scripts/dedup_qdrant.py --rapport       # inventaire + paires _FULL
    python scripts/dedup_qdrant.py --purger-bases  # dry-run Phase 2
    python scripts/dedup_qdrant.py --purger-bases --apply   # exécute la Phase 2
    python scripts/dedup_qdrant.py --purger-micro           # dry-run micro-chunks
    python scripts/dedup_qdrant.py --purger-micro --apply   # supprime les micro-chunks

Phase 2 (`--purger-bases`) : quand `X` et `X_FULL` coexistent, `X_FULL`
est la version complète — on supprime tous les points de `X` (ingestion
partielle/ancienne du même texte).

`--purger-micro` : supprime les points dont `texte_chunk` fait moins de
`cfg.ingest_taille_min_chunk` caractères (fragments « paragraphe 3 »,
« — Article 33 » : bruit de retrieval sans valeur).

`--purger-docs prefixe [prefixe ...]` : supprime tous les points dont le
`document_id` commence par l'un des préfixes (données synthétiques, PDF
mal parsés, doublons à retirer). Toujours en dry-run sans `--apply`.
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


def _parcourir(client: QdrantClient, collection: str) -> list[Any]:
    """Retourne tous les points de la collection (payload, sans vecteurs)."""
    tous: list[Any] = []
    offset: Any = None
    while True:
        points, offset = client.scroll(
            collection,
            limit=1000,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        tous.extend(points)
        if offset is None:
            return tous


def _grouper(points: list[Any]) -> tuple[int, dict[str, list[Any]]]:
    """Regroupe les ids par empreinte exacte (document_id | article_id | hash)."""
    groupes: dict[str, list[Any]] = defaultdict(list)
    for point in points:
        groupes[_empreinte(point.payload or {})].append(point.id)
    return len(points), groupes


def _rapport(points: list[Any]) -> None:
    """Inventaire par document_id, met en évidence les paires `X` / `X_FULL`."""
    par_doc: dict[str, int] = defaultdict(int)
    for point in points:
        par_doc[str((point.payload or {}).get("document_id"))] += 1
    logger.info("documents distincts : %d", len(par_doc))
    paires = sorted(d for d in par_doc if f"{d}_FULL" in par_doc)
    if not paires:
        logger.info("aucune paire base / _FULL — Phase 2 sans objet.")
    for base in paires:
        logger.info(
            "  %-32s %6d pts   |   %s_FULL %6d pts",
            base,
            par_doc[base],
            base,
            par_doc[f"{base}_FULL"],
        )


def _ids_des_bases_avec_full(points: list[Any]) -> tuple[list[str], list[Any]]:
    """Retourne (document_ids des bases, ids de leurs points) quand `X_FULL` existe."""
    par_doc: dict[str, list[Any]] = defaultdict(list)
    for point in points:
        par_doc[str((point.payload or {}).get("document_id"))].append(point.id)
    bases = sorted(d for d in par_doc if f"{d}_FULL" in par_doc)
    ids = [i for base in bases for i in par_doc[base]]
    return bases, ids


def _ids_par_prefixe_doc(points: list[Any], prefixes: list[str]) -> list[Any]:
    """Ids des points dont `document_id` commence par l'un des `prefixes`."""
    return [
        p.id
        for p in points
        if any(
            str((p.payload or {}).get("document_id", "")).startswith(pref)
            for pref in prefixes
        )
    ]


def _ids_micro_chunks(points: list[Any]) -> list[Any]:
    """Ids des points dont `texte_chunk` < `cfg.ingest_taille_min_chunk` caractères."""
    mini = cfg.ingest_taille_min_chunk
    return [
        p.id
        for p in points
        if len(str((p.payload or {}).get("texte_chunk", "")).strip()) < mini
    ]


def _ids_redondants(groupes: dict[str, list[Any]]) -> list[Any]:
    """Pour chaque groupe de taille > 1, garde le plus petit id, rend les autres."""
    surplus: list[Any] = []
    for ids in groupes.values():
        if len(ids) > 1:
            surplus.extend(sorted(ids, key=str)[1:])
    return surplus


def _executer_purge(
    client: QdrantClient, collection: str, ids: list[Any], appliquer: bool
) -> None:
    """Affiche le compte puis supprime (si `appliquer`), sinon dry-run."""
    logger.info("points concernés : %d", len(ids))
    if not appliquer:
        logger.info("\nDRY-RUN — relancer avec --apply pour exécuter.")
        return
    if not ids:
        logger.info("\nRien à supprimer.")
        return
    _supprimer(client, collection, ids)
    logger.info(
        "\npoints_count final : %s", client.get_collection(collection).points_count
    )


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
    parser.add_argument(
        "--rapport", action="store_true", help="inventaire document_id + paires _FULL"
    )
    parser.add_argument(
        "--purger-bases",
        action="store_true",
        help="Phase 2 : supprime les document_id X quand X_FULL existe",
    )
    parser.add_argument(
        "--purger-micro",
        action="store_true",
        help="supprime les chunks < cfg.ingest_taille_min_chunk caractères",
    )
    parser.add_argument(
        "--purger-docs",
        nargs="+",
        metavar="PREFIXE",
        help="supprime les points dont le document_id commence par PREFIXE",
    )
    args = parser.parse_args()

    client = _client()
    collection = cfg.qdrant_collection
    points = _parcourir(client, collection)

    if args.rapport:
        _rapport(points)
        return

    if args.purger_bases:
        bases, ids = _ids_des_bases_avec_full(points)
        logger.info("bases à supprimer : %s", bases or "(aucune)")
        _executer_purge(client, collection, ids, args.apply)
        return

    if args.purger_micro:
        ids = _ids_micro_chunks(points)
        logger.info("seuil : %d caractères", cfg.ingest_taille_min_chunk)
        _executer_purge(client, collection, ids, args.apply)
        return

    if args.purger_docs:
        ids = _ids_par_prefixe_doc(points, args.purger_docs)
        logger.info("préfixes : %s", args.purger_docs)
        _executer_purge(client, collection, ids, args.apply)
        return

    total, groupes = _grouper(points)
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
