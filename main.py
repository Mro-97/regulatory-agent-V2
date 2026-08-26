"""
main.py — Point d'entrée de Regulatory Agent V2
================================================

Lance :
  - API FastAPI via uvicorn
  - Watcher en arrière-plan (boucle asyncio)
  - Gestionnaire d'audit (initialisation pool PostgreSQL si configuré)

Usage :
    python3 main.py
"""

import asyncio
import logging
import sys

import uvicorn

from config import cfg

logging.basicConfig(
    level=logging.DEBUG if cfg.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)

from src.api import app  # noqa: E402


async def demarrer_watcher() -> None:
    """Lance le Watcher en tâche de fond."""
    try:
        from src.watcher import Watcher
        watcher = Watcher()
        logger.info("Watcher démarré en arrière-plan.")
        await watcher.demarrer_boucle()
    except Exception as exc:
        logger.error("Watcher échoué : %s", exc)


async def initialiser_audit() -> None:
    """Initialise le gestionnaire d'audit au démarrage."""
    try:
        from src.audit import obtenir_gestionnaire
        gestionnaire = await obtenir_gestionnaire()
        logger.info("Gestionnaire d'audit initialisé.")
    except Exception as exc:
        logger.warning("Audit non initialisé (non bloquant) : %s", exc)


def valider_configuration_demarrage() -> list[str]:
    """
    Vérifie au démarrage les invariants critiques de configuration.

    L'ancien comportement fail-closed s'appliquait à CHAQUE requête (503 sur
    tous les endpoints protégés) sans jamais l'annoncer au démarrage. Un
    déploiement avec `.env` incomplet passait donc en apparence sans erreur.
    Cette fonction rend le défaut détectable dès le boot : la liste des
    erreurs retournée est utilisée par `main` pour refuser le démarrage.

    Returns:
        Liste des messages d'erreur. Vide si la configuration est valide.
    """
    erreurs: list[str] = []
    if not cfg.api_key or not cfg.api_key.strip():
        erreurs.append(
            "API_KEY vide — définir la variable API_KEY dans .env avant démarrage. "
            "Sans clé, l'API applique un fail-closed (503) sur chaque requête."
        )
    if cfg.api_workers > 1 and cfg.rate_limit_max_requetes > 0:
        # M2 : le rate limiter est mono-process (LimiteurDebit en mémoire) —
        # en multi-worker chaque worker a son propre compteur, la limite
        # effective est multipliée par le nombre de workers.
        erreurs.append(
            f"api_workers={cfg.api_workers} > 1 avec rate_limit_max_requetes>0 : "
            "le rate limiter en mémoire n'est pas partagé entre workers, "
            "la limite effective est multipliée par le nombre de workers. "
            "Utiliser un backend Redis partagé ou fixer api_workers=1."
        )
    return erreurs


if __name__ == "__main__":
    logger.info(
        "Démarrage %s v%s sur %s:%d",
        cfg.app_nom, cfg.app_version, cfg.api_host, cfg.api_port,
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
            cfg.api_host, cfg.api_port,
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
            cfg.api_workers, cfg.api_workers, cfg.api_host, cfg.api_port,
        )

    async def run():
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
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
            logger.info("Arrêt propre.")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
