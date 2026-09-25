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

# Validation de demarrage extraite dans src/demarrage.py (imports
# circulaires) : re-exportee ici pour les appelants historiques.
from src.demarrage import (
    valider_configuration_demarrage as valider_configuration_demarrage,
)


# Ré-export lazy de `app` pour `uvicorn main:app` / `gunicorn main:app`.
# On passe par __getattr__ (PEP 562) plutôt qu'un import top-level : ça
# évite de charger toute la stack FastAPI (src.api → src.orchestrator →
# src.agents.*) au moindre `import main`, ce qui alourdissait chaque test
# qui référence `valider_configuration_demarrage`. Uvicorn accède à
# l'attribut `app` du module, ce qui déclenche l'import à la demande.
def __getattr__(name: str) -> object:
    if name == "app":
        from src.api import app

        return app
    raise AttributeError(f"module 'main' has no attribute {name!r}")  # noqa: TRY003


logging.basicConfig(
    level=logging.DEBUG if cfg.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Loggers HTTP tiers bruyants : capés à INFO même en DEBUG applicatif.
# `httpcore.http11` en particulier dump tous les headers de chaque
# requête (observé : 200 kB/cycle Watcher sur les sources CNIL). On
# garde les infos utiles (méthode + URL + code) sans le bruit binaire.
for _bruyant in ("httpcore", "httpcore.http11", "httpcore.connection", "httpx"):
    logging.getLogger(_bruyant).setLevel(logging.INFO)

logger = logging.getLogger(__name__)


async def demarrer_watcher() -> None:
    """Lance le Watcher en tâche de fond, après un délai de warm-up.

    Le délai (`watcher_delai_demarrage_secondes`) évite que le premier cycle
    du Watcher entre en concurrence I/O avec le startup uvicorn et retarde
    la disponibilité de `/health`. Désactivable via `watcher_actif=false`
    quand le Watcher tourne dans un process séparé.
    """
    if not cfg.watcher_actif:
        logger.info("Watcher désactivé (watcher_actif=false).")
        return
    try:
        from src.watcher import Watcher

        delai = cfg.watcher_delai_demarrage_secondes
        if delai > 0:
            logger.info("Watcher — attente %.1f s avant premier cycle.", delai)
            await asyncio.sleep(delai)
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
            server_header=False,  # pas de fingerprint « server: uvicorn »
            # uvicorn ne doit PAS interpréter X-Forwarded-For/-Proto lui-même :
            # sinon `request.client.host` est réécrit depuis un en-tête
            # falsifiable (défaut : confiance à 127.0.0.1). C'est src/net.py,
            # piloté par TRUSTED_PROXIES, qui décide — un seul endroit.
            proxy_headers=False,
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
