"""scripts/diagnostic_corpus.py — detecte et purge les chunks corrompus.

Contexte : le Journal officiel REACH (1907/2006) contient des pages a texte
pivote. L'extraction PDF les a lues a l'envers, et ces chunks sont indexes
tels quels — ils produisent des vecteurs qui attirent des requetes sans aucun
rapport. Mesure du 2026-09-21 : 91 chunks sur 1856 (4,9 %) de REACH, et aucun
autre document touche.

La signature et le critere de detection vivent dans `src/corpus_integrite.py`,
partages avec l'extraction PDF (reparation) et l'ingestion (garde-fou) : une
seule definition du critere, donc aucun risque de divergence entre les trois.

Deux modes :
    --inventaire   (defaut) rapporte, ne modifie RIEN ;
    --purger       supprime les chunks detectes — DESTRUCTIF.

Prendre une sauvegarde avant toute purge :
    python scripts/sauvegarde.py --sauvegarder
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from config import cfg  # noqa: E402
from src.corpus_integrite import est_inverse, mesurer  # noqa: E402

if TYPE_CHECKING:
    from qdrant_client import QdrantClient

logger = logging.getLogger("diagnostic_corpus")


def _client() -> QdrantClient:
    """Client Qdrant configure depuis `cfg`."""
    from qdrant_client import QdrantClient

    return QdrantClient(host=cfg.qdrant_host, port=cfg.qdrant_port, timeout=120)


def _parcourir(client: QdrantClient) -> list[tuple[str, str, str, int]]:
    """Retourne (point_id, document_id, article_id, inversions) des corrompus."""
    trouves: list[tuple[str, str, str, int]] = []
    lus = 0
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=cfg.qdrant_collection,
            limit=500,
            offset=offset,
            with_payload=["document_id", "article_id", "texte_chunk"],
            with_vectors=False,
        )
        for point in points:
            charge = point.payload or {}
            lus += 1
            texte = str(charge.get("texte_chunk") or "")
            if est_inverse(texte):
                trouves.append(
                    (
                        str(point.id),
                        str(charge.get("document_id") or "?"),
                        str(charge.get("article_id") or "?"),
                        mesurer(texte)[0],
                    )
                )
        if offset is None:
            break
    logger.info("%s chunks analyses, %s corrompus", lus, len(trouves))
    return trouves


def inventaire() -> int:
    """Rapporte les chunks corrompus par document, sans rien modifier."""
    trouves = _parcourir(_client())
    if not trouves:
        logger.info("Aucun chunk corrompu detecte.")
        return 0
    par_document: Counter[str] = Counter(doc for _, doc, _, _ in trouves)
    logger.info("Chunks corrompus par document :")
    for document, nombre in par_document.most_common():
        logger.info("  %-34s %5s", document, nombre)
    logger.info(
        "Pour purger : python scripts/diagnostic_corpus.py --purger "
        "(apres scripts/sauvegarde.py --sauvegarder)"
    )
    return 0


def purger() -> int:
    """Supprime les chunks corrompus de la collection — DESTRUCTIF."""
    client = _client()
    trouves = _parcourir(client)
    if not trouves:
        logger.info("Aucun chunk corrompu : rien a purger.")
        return 0
    # Type elargi explicite : `points_selector` attend
    # `list[int | str | UUID | PointId]` et `list` est invariant, donc un
    # `list[str]` ne convient pas.
    identifiants: list[int | str | UUID] = []
    for point_id, _, _, _ in trouves:
        identifiants.append(point_id)
    logger.warning("Suppression de %s chunks corrompus...", len(identifiants))
    client.delete(
        collection_name=cfg.qdrant_collection,
        points_selector=identifiants,
        wait=True,
    )
    info = client.get_collection(cfg.qdrant_collection)
    logger.info("Termine. Collection : %s points.", info.points_count)
    logger.info(
        "Reindexer REACH proprement pour restaurer le contenu legitime "
        "(les chunks purges incluent du texte valide melange)."
    )
    return 0


def _parser() -> argparse.Namespace:
    """Analyse les arguments."""
    parser = argparse.ArgumentParser(
        description="Detecte (et purge) les chunks corrompus du corpus.",
    )
    groupe = parser.add_mutually_exclusive_group()
    groupe.add_argument(
        "--inventaire", action="store_true", help="Rapporter sans modifier (defaut)."
    )
    groupe.add_argument(
        "--purger", action="store_true", help="Supprimer les chunks corrompus."
    )
    return parser.parse_args()


def main() -> int:
    """Point d'entree."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parser()
    return purger() if args.purger else inventaire()


if __name__ == "__main__":
    sys.exit(main())
