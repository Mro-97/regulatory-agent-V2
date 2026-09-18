"""scripts/sauvegarde.py — Sauvegarde et restauration de Qdrant et Redis (O-4).

Sans ce script, la seule sauvegarde existante était celle créée
incidemment par `dedup_qdrant.py` avant une purge — donc ni planifiée, ni
documentée, et jamais pour Redis.

Deux magasins, deux mécanismes, parce que leurs contraintes diffèrent :

- **Qdrant** : l'API de snapshot produit une archive cohérente sans arrêter le
  service (elle inclut l'index et les payloads). C'est la seule méthode sûre :
  copier `qdrant_storage/` à chaud donne une image incohérente.
- **Redis** : `BGSAVE` puis copie du `dump.rdb` produit par Redis lui-même.
  Le volume est minuscule (files HITL + compteurs), donc pas de raison de
  s'en passer.

Usage :
    python scripts/sauvegarde.py --etat            # que contient chaque magasin
    python scripts/sauvegarde.py --sauvegarder     # snapshot Qdrant + dump Redis
    python scripts/sauvegarde.py --purger --garder 2   # ménage (destructif)
    python scripts/sauvegarde.py --restaurer-chemin <chemin.snapshot>
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from config import cfg  # noqa: E402

DOSSIER_SNAPSHOTS = RACINE / "snapshots"

if TYPE_CHECKING:
    from qdrant_client import QdrantClient

logger = logging.getLogger("sauvegarde")


def _client_qdrant() -> QdrantClient:
    """Client Qdrant local, configuré depuis `cfg`."""
    from qdrant_client import QdrantClient

    return QdrantClient(
        host=cfg.qdrant_host,
        port=cfg.qdrant_port,
        https=cfg.qdrant_https,
        api_key=cfg.qdrant_api_key or None,
        timeout=60,
    )


def _dimension(vecteurs: object) -> object:
    """Dimension de la collection, ou '?' si la config ne l'expose pas.

    `vectors` est une union (`VectorParams | dict[str, VectorParams] | None`)
    selon la configuration : une collection mono-vecteur porte `.size`, une
    collection nommee porte un dictionnaire. On lit sans supposer la forme.
    """
    taille = getattr(vecteurs, "size", None)
    if taille is not None:
        return taille
    if isinstance(vecteurs, dict):
        return {nom: getattr(v, "size", "?") for nom, v in vecteurs.items()}
    return "?"


def etat() -> int:
    """Affiche le contenu des deux magasins et le volume des sauvegardes."""
    client = _client_qdrant()
    info = client.get_collection(cfg.qdrant_collection)
    snapshots = client.list_snapshots(collection_name=cfg.qdrant_collection)
    logger.info(
        "Qdrant  : %s points, %s dim",
        info.points_count,
        _dimension(info.config.params.vectors),
    )
    logger.info("          %s snapshot(s) côté serveur", len(snapshots))
    total = sum(f.stat().st_size for f in DOSSIER_SNAPSHOTS.rglob("*") if f.is_file())
    logger.info("          dossier local snapshots/ : %.2f Go", total / 1e9)
    dumps = sorted(DOSSIER_SNAPSHOTS.glob("redis-*.rdb"))
    logger.info("Redis   : %s dump(s) local(aux)", len(dumps))
    if dumps:
        logger.info("          le plus récent : %s", dumps[-1].name)
    return 0


def _sauvegarder_qdrant() -> Path:
    """Crée un snapshot Qdrant, le nom du fichier côté serveur étant rendu."""
    client = _client_qdrant()
    resultat = client.create_snapshot(collection_name=cfg.qdrant_collection, wait=True)
    nom = getattr(resultat, "name", None) or str(resultat)
    logger.info("  Qdrant : snapshot créé — %s", nom)
    return Path(str(nom))


def _sauvegarder_redis() -> Path:
    """Déclenche un `BGSAVE` et copie le `dump.rdb` horodaté dans snapshots/."""
    import asyncio

    from src.redis_client import nouveau_client

    async def _faire() -> Path:
        client = nouveau_client(
            host=cfg.redis_host,
            port=cfg.redis_port,
            password=cfg.redis_password,
            db=cfg.redis_db,
        )
        try:
            await client.sauvegarder()
            source = Path(await client.chemin_du_dump())
        finally:
            await client.aclose()
        if not source.exists():
            raise SystemExit(  # noqa: TRY003 — message opérateur
                f"dump Redis introuvable : {source}"
            )
        horodatage = time.strftime("%Y-%m-%d-%H%M%S")
        destination = DOSSIER_SNAPSHOTS / f"redis-{horodatage}.rdb"
        shutil.copy2(source, destination)
        return destination

    return asyncio.run(_faire())


def sauvegarder() -> int:
    """Sauvegarde les deux magasins dans `snapshots/`."""
    DOSSIER_SNAPSHOTS.mkdir(exist_ok=True)
    _sauvegarder_qdrant()
    dump = _sauvegarder_redis()
    taille_ko = dump.stat().st_size / 1024
    logger.info("  Redis  : dump copié — %s (%.0f Ko)", dump.name, taille_ko)
    logger.info("Snapshots dans %s", DOSSIER_SNAPSHOTS)
    return 0


def purger(garder: int) -> int:
    """Ne conserve que les `garder` snapshots Qdrant les plus récents.

    Les snapshots Qdrant s'accumulaient sans limite (3 Go au 2026-09-17, dont
    des archives de l'ancien index torch devenu inutilisable). Suppression
    explicite : `--purger` est requis, rien n'est effacé par défaut.
    """
    client = _client_qdrant()
    snapshots = sorted(
        client.list_snapshots(collection_name=cfg.qdrant_collection),
        key=lambda s: s.creation_time or "",
    )
    if len(snapshots) <= garder:
        logger.info(
            "%s snapshot(s) — rien à purger (garde %s).", len(snapshots), garder
        )
        return 0
    for snap in snapshots[: len(snapshots) - garder]:
        client.delete_snapshot(
            collection_name=cfg.qdrant_collection, snapshot_name=snap.name
        )
        logger.info("  supprimé : %s (%.0f Mo)", snap.name, snap.size / 1e6)
    logger.info("%s snapshot(s) conservé(s).", garder)
    return 0


def restaurer_chemin(chemin: str) -> int:
    """Restaure une collection depuis un fichier de snapshot.

    Opération destructive : la collection est remplacée. Le snapshot est lu
    depuis le disque et envoyé à Qdrant, qui reconstruit l'index.
    """
    fichier = Path(chemin)
    if not fichier.exists():
        raise SystemExit(  # noqa: TRY003 — message opérateur
            f"snapshot introuvable : {fichier}"
        )
    client = _client_qdrant()
    client.recover_snapshot(
        collection_name=cfg.qdrant_collection,
        location=str(fichier),
        wait=True,
    )
    logger.info(
        "Collection '%s' restaurée depuis %s", cfg.qdrant_collection, fichier.name
    )
    return 0


def _parser() -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Sauvegarde et restauration de Qdrant et Redis.",
    )
    parser.add_argument("--etat", action="store_true", help="Afficher l'état.")
    parser.add_argument(
        "--sauvegarder", action="store_true", help="Snapshot Qdrant + dump Redis."
    )
    parser.add_argument(
        "--purger",
        action="store_true",
        help="Supprimer les snapshots Qdrant les plus anciens (destructif).",
    )
    parser.add_argument(
        "--garder", type=int, default=2, help="Snapshots à conserver avec --purger."
    )
    parser.add_argument(
        "--restaurer-chemin", help="Restaurer la collection depuis un snapshot."
    )
    return parser.parse_args()


def main() -> int:
    """Point d'entrée."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parser()
    if args.etat:
        return etat()
    if args.sauvegarder:
        return sauvegarder()
    if args.purger:
        return purger(args.garder)
    if args.restaurer_chemin:
        return restaurer_chemin(args.restaurer_chemin)
    _parser().print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
