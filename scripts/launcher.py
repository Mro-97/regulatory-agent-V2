#!/usr/bin/env python3
"""scripts/launcher.py — Orchestrateur de démarrage local.

Vérifie et démarre les dépendances (Qdrant + Redis) puis lance l'API,
avec pré-chauffage optionnel des modèles MLX. Une seule commande pour
un poste de développement.

Usage :
    python3 scripts/launcher.py                # démarrage standard
    python3 scripts/launcher.py --skip-warmup  # sans préchargement modèles
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "logs"
QDRANT_PORT = 6333
REDIS_PORT = 6379
API_PORT = 8000
DELAI_ATTENTE_SERVICE = 15  # secondes max pour qu'un service devienne prêt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] launcher — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _port_ouvert(host: str, port: int, timeout: float = 0.5) -> bool:
    """True si `host:port` accepte une connexion TCP."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
        except OSError:
            return False
    return True


def _attendre_port(port: int, timeout: int = DELAI_ATTENTE_SERVICE) -> bool:
    """Poll `127.0.0.1:port` jusqu'à ce qu'il réponde, ou timeout."""
    debut = time.monotonic()
    while time.monotonic() - debut < timeout:
        if _port_ouvert("127.0.0.1", port):
            return True
        time.sleep(0.5)
    return False


def _lancer_arriere_plan(
    commande: list[str], log_file: Path
) -> subprocess.Popen[bytes]:
    """Démarre `commande` détaché, redirige stdout/stderr vers `log_file`."""
    LOG_DIR.mkdir(exist_ok=True)
    log_handle = log_file.open("ab")
    return subprocess.Popen(  # noqa: S603 - commandes internes maîtrisées
        commande, stdout=log_handle, stderr=subprocess.STDOUT, cwd=REPO_ROOT
    )


def demarrer_qdrant_si_necessaire() -> None:
    """Lance le binaire `./qdrant` si le port 6333 est libre, sinon skip."""
    if _port_ouvert("127.0.0.1", QDRANT_PORT):
        logger.info("Qdrant déjà actif sur %d — skip.", QDRANT_PORT)
        return
    binaire = REPO_ROOT / "qdrant"
    if not binaire.exists():
        logger.error("Binaire Qdrant introuvable : %s", binaire)
        sys.exit(1)
    logger.info("Démarrage Qdrant en arrière-plan…")
    _lancer_arriere_plan([str(binaire)], LOG_DIR / "qdrant.log")
    if not _attendre_port(QDRANT_PORT):
        logger.error("Qdrant n'a pas répondu sur %d en %ds.", QDRANT_PORT, 15)
        sys.exit(1)
    logger.info("Qdrant prêt.")


def demarrer_redis_si_necessaire() -> None:
    """Lance `redis-server` si le port 6379 est libre, sinon skip."""
    if _port_ouvert("127.0.0.1", REDIS_PORT):
        logger.info("Redis déjà actif sur %d — skip.", REDIS_PORT)
        return
    redis_bin = (
        shutil.which("redis-server") or "/opt/homebrew/opt/redis/bin/redis-server"
    )
    if not Path(redis_bin).exists():
        logger.error("redis-server introuvable — brew install redis ?")
        sys.exit(1)
    logger.info("Démarrage Redis en arrière-plan…")
    _lancer_arriere_plan(
        [redis_bin, "--port", str(REDIS_PORT), "--daemonize", "no"],
        LOG_DIR / "redis.log",
    )
    if not _attendre_port(REDIS_PORT):
        logger.error("Redis n'a pas répondu sur %d en %ds.", REDIS_PORT, 15)
        sys.exit(1)
    logger.info("Redis prêt.")


def prechauffer_modeles_en_arriere_plan() -> subprocess.Popen[bytes]:
    """Charge bge-m3 en tâche de fond pour raccourcir le premier /ask.

    Retourne le Popen — le launcher ne l'attend pas ; le premier /ask
    profitera du cache OS déjà chaud même si l'import Python n'a pas fini.
    """
    logger.info("Pré-chauffage modèle d'embedding en arrière-plan…")
    script = (
        "from src.mlx_embedding import get_embedding; "
        "get_embedding().load(); print('preload OK', flush=True)"
    )
    return _lancer_arriere_plan([sys.executable, "-c", script], LOG_DIR / "preload.log")


def _verifier_api_key() -> None:
    """Refuse le démarrage si `API_KEY` n'est pas configurée dans `.env`."""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        logger.error(".env introuvable — copier .env.example puis configurer API_KEY.")
        sys.exit(1)
    contenu = env_file.read_text(encoding="utf-8")
    if "API_KEY=" not in contenu or all(
        not ligne.startswith("API_KEY=") or not ligne.split("=", 1)[1].strip()
        for ligne in contenu.splitlines()
    ):
        logger.error(
            "API_KEY vide ou absente de .env — voir main.py pour la génération."
        )
        sys.exit(1)


def lancer_api() -> None:
    """Démarre l'API FastAPI en avant-plan (bloquant jusqu'à Ctrl+C)."""
    if _port_ouvert("127.0.0.1", API_PORT):
        logger.error("Port %d déjà occupé — arrêter l'instance existante.", API_PORT)
        sys.exit(1)
    logger.info("Démarrage de l'API sur %d…", API_PORT)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    subprocess.run(  # noqa: S603 - commande interne maîtrisée
        [sys.executable, str(REPO_ROOT / "main.py")],
        env=env,
        cwd=REPO_ROOT,
        check=False,
    )


def _parser_arguments() -> argparse.Namespace:
    """Configure et retourne les arguments CLI du launcher."""
    parser = argparse.ArgumentParser(
        description="Démarre Qdrant, Redis puis l'API Regulatory Agent.",
    )
    parser.add_argument(
        "--skip-warmup",
        action="store_true",
        help="Ne pas pré-charger le modèle d'embedding.",
    )
    return parser.parse_args()


def main() -> None:
    """Point d'entrée : vérifs → services → warmup → API."""
    args = _parser_arguments()
    _verifier_api_key()
    demarrer_qdrant_si_necessaire()
    demarrer_redis_si_necessaire()
    if not args.skip_warmup:
        prechauffer_modeles_en_arriere_plan()
    lancer_api()


if __name__ == "__main__":
    main()
