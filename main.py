"""main.py — Point d'entrée de Regulatory Agent V2
================================================

Lance :
  - API FastAPI via uvicorn
  - Watcher en arrière-plan (boucle asyncio)
  - Gestionnaire d'audit (initialisation pool PostgreSQL si configuré)

Usage :
    python3 main.py
"""  # noqa: D205, D415

import asyncio
import contextlib
import logging
import sys

import uvicorn
from config import cfg

# Ré-export pour `uvicorn main:app` / `gunicorn main:app` — l'app FastAPI
# vit dans src.api ; ce module est le point d'entrée « python main.py ».
from src.api import app as app

logging.basicConfig(
    level=logging.DEBUG if cfg.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


async def demarrer_watcher() -> None:
    """Lance le Watcher en tâche de fond."""
    try:
        from src.watcher import Watcher

        watcher = Watcher()
        logger.info("Watcher démarré en arrière-plan.")
        await watcher.demarrer_boucle()
    except Exception:
        logger.exception("Watcher échoué")


async def initialiser_audit() -> None:
    """Initialise le gestionnaire d'audit au démarrage."""
    try:
        from src.audit import obtenir_gestionnaire

        await obtenir_gestionnaire()
        logger.info("Gestionnaire d'audit initialisé.")
    except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
        logger.warning("Audit non initialisé (non bloquant) : %s", exc)


def valider_configuration_demarrage() -> list[str]:
    """Vérifie au démarrage les invariants critiques (retourne les erreurs)."""
    erreurs: list[str] = []
    _erreur_api_key_manquante(erreurs)
    _erreur_rate_limiter_multi_worker(erreurs)
    return erreurs


_API_KEY_PLACEHOLDER = "remplacez-par-une-cle-longue-et-aleatoire"
_API_KEY_LONGUEUR_MIN = 32
_COMMANDE_GEN_CLE = (
    "python3 -c \"import secrets; print('API_KEY=' + secrets.token_urlsafe(32))\""
)


def _erreur_api_key_manquante(erreurs: list[str]) -> None:
    """Refuse le boot si `cfg.api_key` est vide, placeholder, ou trop courte.

    Trois cas fail-closed :
    - clé vide → aucun endpoint métier ne pourra répondre (503 systématique) ;
    - valeur placeholder de `.env.example` → déploiement non configuré ;
    - clé < 32 caractères → bruteforce triviale.
    """
    cle = (cfg.api_key or "").strip()
    if not cle:
        erreurs.append(
            "API_KEY vide — définir la variable API_KEY dans .env avant démarrage. "
            f"Générer une clé sûre : {_COMMANDE_GEN_CLE}"
        )
        return
    if cle == _API_KEY_PLACEHOLDER:
        erreurs.append(
            "API_KEY = valeur placeholder de .env.example — remplacer par une clé "
            f"réelle avant déploiement. Générer : {_COMMANDE_GEN_CLE}"
        )
        return
    if len(cle) < _API_KEY_LONGUEUR_MIN:
        erreurs.append(
            f"API_KEY trop courte ({len(cle)} < {_API_KEY_LONGUEUR_MIN} caractères) — "
            f"risque de bruteforce. Générer : {_COMMANDE_GEN_CLE}"
        )


def _erreur_rate_limiter_multi_worker(erreurs: list[str]) -> None:
    """Signale que le rate limiter en mémoire n'est pas partagé entre workers."""
    if cfg.api_workers > 1 and cfg.rate_limit_max_requetes > 0:
        erreurs.append(
            f"api_workers={cfg.api_workers} > 1 avec rate_limit_max_requetes>0 : "
            "le rate limiter en mémoire n'est pas partagé entre workers, "
            "la limite effective est multipliée par le nombre de workers. "
            "Utiliser un backend Redis partagé ou fixer api_workers=1."
        )


if __name__ == "__main__":
    logger.info(
        "Démarrage %s v%s sur %s:%d",
        cfg.app_nom,
        cfg.app_version,
        cfg.api_host,
        cfg.api_port,
    )

    erreurs_conf = valider_configuration_demarrage()
    if erreurs_conf:
        for _err in erreurs_conf:
            logger.critical("Configuration invalide : %s", _err)
        sys.exit(2)

    # -----------------------------------------------------------
    # Vérifications de compatibilité avec uvicorn.Server programmatique
    # -----------------------------------------------------------
    # `reload=True` ne fonctionne qu'avec `uvicorn.run(...)` en CLI :
    # il fork un process superviseur qui watche les fichiers, ce qui
    # casse la boucle asyncio du Watcher démarré ici. On journalise et
    # on tourne sans reload.
    if cfg.debug:
        logger.warning(
            "DEBUG=true — le rechargement à chaud (reload) est incompatible "
            "avec le mode programmatique (uvicorn.Server + asyncio). "
            "L'API tourne SANS reload. Pour bénéficier du reload, arrêter "
            "ce process et lancer : "
            "'uvicorn main:app --reload --host %s --port %d' "
            "(le Watcher devra alors être démarré séparément).",
            cfg.api_host,
            cfg.api_port,
        )

    # `workers=N` est ignoré par uvicorn.Server programmatique : seul
    # `uvicorn.run(...)` ou gunicorn savent forker plusieurs workers.
    # On force à 1 et on journalise si l'utilisateur en a demandé plus.
    if cfg.api_workers > 1:
        logger.warning(
            "API_WORKERS=%d ignoré en mode programmatique — un seul worker tourne. "
            "Pour du multi-process en production, utiliser gunicorn : "
            "'gunicorn main:app -k uvicorn.workers.UvicornWorker -w %d "
            "--bind %s:%d' (le Watcher devra alors tourner dans un process séparé).",
            cfg.api_workers,
            cfg.api_workers,
            cfg.api_host,
            cfg.api_port,
        )

    async def run() -> None:
        """Boucle d'entrée : audit + Watcher en tâche de fond + uvicorn."""
        # Initialisation audit
        await initialiser_audit()

        # Watcher en arrière-plan (ne bloque pas l'API)
        watcher_task = asyncio.create_task(demarrer_watcher())

        # Serveur uvicorn — mode programmatique, 1 worker, sans reload.
        # Le multi-worker et le reload nécessitent uvicorn CLI ou gunicorn
        # (voir warnings au-dessus).
        config = uvicorn.Config(
            app="main:app",
            host=cfg.api_host,
            port=cfg.api_port,
            log_level="debug" if cfg.debug else "info",
        )
        server = uvicorn.Server(config)
        try:
            await server.serve()
        finally:
            watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher_task
            logger.info("Arrêt propre.")

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())
