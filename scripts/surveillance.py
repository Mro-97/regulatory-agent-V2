"""scripts/surveillance.py — Supervision des services avec alerte (O-5).

Avant ce script, `/health/details` existait mais **rien ne l'interrogeait** :
un service arrêté ne se signalait que si quelqu'un ouvrait la page. La
supervision repose ici sur un principe Unix : un contrôle, un code de sortie.
C'est ce qui la rend composable — `cron`/`launchd` pour la répétition,
`--alerte-cmd` pour la notification, sans rien imposer.

Quatre contrôles, chacun indépendant :

- **API** : `/health` répond 200 dans le délai imparti (c'est le seul service
  dont l'arrêt rend l'application inutilisable immédiatement).
- **Qdrant** : l'API de collection répond (sans lui, toute recherche échoue).
- **Redis** : `PING` via le client RESP du projet (sans lui, les files HITL
  sont indisponibles — l'API, elle, continue de fonctionner).
- **PostgreSQL** : socket TCP joignable, si un DSN est configuré (sans lui,
  l'audit bascule sur le fichier JSONL, donc dégradation et non panne).

Usage :
    python scripts/surveillance.py                 # code 0 si tout va bien
    python scripts/surveillance.py --verbeux       # détail de chaque contrôle
    python scripts/surveillance.py --alerte-cmd "osascript -e '...'"
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from config import cfg  # noqa: E402

logger = logging.getLogger("surveillance")

DELAI_SECONDES = 5.0


@dataclass(frozen=True)
class Controle:
    """Résultat d'un contrôle : nom, état, et détail lisible."""

    nom: str
    ok: bool
    detail: str


def _controler_api() -> Controle:
    """L'API répond-elle 200 sur `/health` dans le délai imparti ?"""
    url = f"http://{cfg.api_host}:{cfg.api_port}/health"
    try:
        with urllib.request.urlopen(url, timeout=DELAI_SECONDES) as reponse:
            code = reponse.status
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return Controle("api", False, f"injoignable ({type(exc).__name__})")
    return Controle("api", code == 200, f"HTTP {code}")


def _controler_qdrant() -> Controle:
    """L'API de collection Qdrant répond-elle, et la collection existe-t-elle ?"""
    base = f"http://{cfg.qdrant_host}:{cfg.qdrant_port}"
    url = f"{base}/collections/{cfg.qdrant_collection}"
    try:
        with urllib.request.urlopen(  # noqa: S310 — URL construite ici
            url, timeout=DELAI_SECONDES
        ) as reponse:
            code = reponse.status
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return Controle("qdrant", False, f"injoignable ({type(exc).__name__})")
    return Controle("qdrant", code == 200, f"HTTP {code}")


def _controler_redis() -> Controle:
    """Redis répond-il au `PING` (client RESP du projet, pas de dépendance) ?"""
    import asyncio

    from src.redis_client import nouveau_client

    async def _ping() -> bool:
        client = nouveau_client(
            host=cfg.redis_host,
            port=cfg.redis_port,
            password=cfg.redis_password,
            db=cfg.redis_db,
            timeout_secondes=DELAI_SECONDES,
        )
        try:
            return await client.ping()
        finally:
            await client.aclose()

    try:
        vivant = asyncio.run(_ping())
    except Exception as exc:  # noqa: BLE001 — frontière : tout échec = service KO
        return Controle("redis", False, f"injoignable ({type(exc).__name__})")
    return Controle("redis", vivant, "PING ok" if vivant else "PING sans reponse")


def _controler_postgres() -> Controle:
    """Le port PostgreSQL est-il ouvert (si un DSN est configuré) ?

    Simple test TCP : ouvrir une vraie connexion exigerait `asyncpg` et un
    pool, pour un contrôle dont le seul but est de savoir si le service
    répond. Un port fermé suffit à conclure.
    """
    if not cfg.postgres_dsn:
        return Controle("postgres", True, "non configuré (repli JSONL)")
    analyse = urlparse(cfg.postgres_dsn)
    hote = analyse.hostname or "127.0.0.1"
    port = analyse.port or 5432
    try:
        with socket.create_connection((hote, port), timeout=DELAI_SECONDES):
            return Controle("postgres", True, f"port {port} ouvert")
    except OSError as exc:
        return Controle("postgres", False, f"port {port} fermé ({type(exc).__name__})")


def controler_tout() -> list[Controle]:
    """Exécute les quatre contrôles, sans s'arrêter au premier échec."""
    return [
        _controler_api(),
        _controler_qdrant(),
        _controler_redis(),
        _controler_postgres(),
    ]


def _alerter(commande: str, resume: str) -> None:
    """Exécute la commande d'alerte en lui passant le résumé sur stdin.

    Le résumé passe par stdin et non par la ligne de commande : l'opérateur
    garde la main sur son outil (mail, webhook, notification macOS) sans que
    ce script ait à connaître le format attendu.
    """
    import subprocess

    try:
        subprocess.run(  # noqa: S602 — commande fournie par l'opérateur, assumée
            commande,
            shell=True,
            input=resume,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.exception("Alerte non envoyée : %s", type(exc).__name__)


def _parser() -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(description="Supervision des services.")
    parser.add_argument(
        "--verbeux", action="store_true", help="Détailler chaque contrôle."
    )
    parser.add_argument(
        "--alerte-cmd",
        help="Commande à exécuter en cas d'échec (reçoit le résumé sur stdin).",
    )
    return parser.parse_args()


def main() -> int:
    """Point d'entrée : code 0 si tout va bien, 1 sinon."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parser()
    controles = controler_tout()
    for controle in controles:
        if args.verbeux or not controle.ok:
            etat = "OK  " if controle.ok else "ECHEC"
            logger.info("%s %-9s %s", etat, controle.nom, controle.detail)

    en_echec = [c for c in controles if not c.ok]
    if not en_echec:
        logger.info("Tous les services répondent (%s contrôles).", len(controles))
        return 0

    resume = "\n".join(f"- {c.nom} : {c.detail}" for c in en_echec)
    logger.error("%s service(s) en échec :\n%s", len(en_echec), resume)
    if args.alerte_cmd:
        _alerter(args.alerte_cmd, resume)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
