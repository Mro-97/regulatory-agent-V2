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
DELAI_ATTENTE_SERVICE = 15  # secondes max pour qu'un service devienne prêt


def _parametres() -> tuple[int, int, int]:
    """`(port_api, port_qdrant, port_redis)` lus depuis la config.

    Les ports des services étaient codés en dur (6333/6379) alors que celui
    de l'API venait de `cfg.api_port` : avec `QDRANT_PORT=6444` dans `.env`,
    le launcher sondait 6333, ne trouvait rien et démarrait un SECOND Qdrant
    sur le port par défaut pendant que l'API se connectait à 6444.
    """
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from config import cfg

        return int(cfg.api_port), int(cfg.qdrant_port), int(cfg.redis_port)
    except Exception:  # noqa: BLE001 — repli si config illisible
        return 8000, 6333, 6379


API_PORT, QDRANT_PORT, REDIS_PORT = _parametres()

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
    """Démarre `commande` détaché, redirige stdout/stderr vers `log_file`.

    Le descripteur du fichier de log est fermé côté PARENT juste après le
    `Popen` : l'enfant garde sa propre copie (dup) et écrit normalement,
    mais le launcher ne fuit plus un descripteur par service démarré — ni
    ne le transmet au processus API qu'il lance ensuite.
    """
    LOG_DIR.mkdir(exist_ok=True)
    with log_file.open("ab") as log_handle:
        return subprocess.Popen(  # noqa: S603 - commandes internes maîtrisées
            commande, stdout=log_handle, stderr=subprocess.STDOUT, cwd=REPO_ROOT
        )


def demarrer_qdrant_si_necessaire() -> None:
    """Lance le binaire `./qdrant` si `QDRANT_PORT` est libre, sinon skip."""
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
        logger.error(
            "Qdrant n'a pas répondu sur %d en %ds.", QDRANT_PORT, DELAI_ATTENTE_SERVICE
        )
        sys.exit(1)
    logger.info("Qdrant prêt.")


def demarrer_redis_si_necessaire() -> None:
    """Lance `redis-server` si `REDIS_PORT` est libre, sinon skip."""
    if _port_ouvert("127.0.0.1", REDIS_PORT):
        logger.info("Redis déjà actif sur %d — skip.", REDIS_PORT)
        return
    redis_bin = (
        shutil.which("redis-server") or "/opt/homebrew/opt/redis/bin/redis-server"
    )
    if not os.access(redis_bin, os.X_OK):
        logger.error(
            "redis-server introuvable ou non exécutable — brew install redis ?"
        )
        sys.exit(1)
    logger.info("Démarrage Redis en arrière-plan…")
    _lancer_arriere_plan(
        [redis_bin, "--port", str(REDIS_PORT), "--daemonize", "no"],
        LOG_DIR / "redis.log",
    )
    if not _attendre_port(REDIS_PORT):
        logger.error(
            "Redis n'a pas répondu sur %d en %ds.", REDIS_PORT, DELAI_ATTENTE_SERVICE
        )
        sys.exit(1)
    logger.info("Redis prêt.")


def prechauffer_modeles_en_arriere_plan() -> None:
    """Charge bge-m3 en tâche de fond pour raccourcir le premier /ask.

    Le `Popen` est conservé puis surveillé sans être attendu : le premier
    /ask profite du cache OS déjà chaud même si l'import Python n'est pas
    terminé. Un échec immédiat du pré-chauffage (import cassé, modèle
    absent) est signalé au lieu de disparaître — il n'était auparavant
    rattaché à aucune référence.
    """
    logger.info("Pré-chauffage modèle d'embedding en arrière-plan…")
    # `cfg.modele_embedding` est passe EXPLICITEMENT : le defaut de
    # `get_embedding()` etait `"BAAI/bge-m3"`, un depot HuggingFace sans
    # safetensors, donc inutilisable. Le prechauffage chargeait ce modele casse
    # au lieu de celui de la configuration.
    script = (
        "from config import cfg; "
        "from src.mlx_embedding import get_embedding; "
        "get_embedding(cfg.modele_embedding).load(); "
        "print('preload OK', flush=True)"
    )
    processus = _lancer_arriere_plan(
        [sys.executable, "-c", script], LOG_DIR / "preload.log"
    )
    time.sleep(1.0)
    if processus.poll() not in (None, 0):
        logger.warning(
            "Pré-chauffage terminé en erreur (code %s) — voir %s.",
            processus.returncode,
            LOG_DIR / "preload.log",
        )


def _verifier_configuration() -> None:
    """Refuse le démarrage si la configuration est invalide.

    Délègue à `main.valider_configuration_demarrage()` — exactement le
    contrôle que rejoue le lifespan de l'API (magasin de clés RBAC vide /
    sans admin, DEBUG+DOCS, ENVIRONNEMENT=prod incohérent, dimension
    embedding…). Échoue tôt et lisiblement.
    """
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        logger.error(".env introuvable — copier .env.example puis configurer.")
        sys.exit(1)
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from main import valider_configuration_demarrage
    except Exception:
        logger.exception("Impossible de charger la configuration")
        sys.exit(1)
    erreurs = valider_configuration_demarrage()
    for err in erreurs:
        logger.error("Configuration invalide : %s", err)
    if erreurs:
        logger.error(
            "Générer une clé : venv/bin/python scripts/gerer_cles.py "
            "generer --role admin --label operateur"
        )
        sys.exit(2)


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
    _verifier_configuration()
    demarrer_qdrant_si_necessaire()
    demarrer_redis_si_necessaire()
    # Le port de l'API est contrôlé AVANT le pré-chauffage : sinon un
    # lancement sur un port déjà occupé payait le chargement du modèle
    # (plusieurs centaines de Mo) pour échouer juste après.
    if _port_ouvert("127.0.0.1", API_PORT):
        logger.error("Port %d déjà occupé — arrêter l'instance existante.", API_PORT)
        sys.exit(1)
    if not args.skip_warmup:
        prechauffer_modeles_en_arriere_plan()
    lancer_api()


if __name__ == "__main__":
    main()
