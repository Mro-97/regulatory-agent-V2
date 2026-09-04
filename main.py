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


def valider_configuration_demarrage() -> list[str]:
    """Vérifie au démarrage les invariants critiques (retourne les erreurs).

    Appelée par `main.py` (lancement direct) ET par le lifespan de
    `src/api.py` — donc effective aussi sous `gunicorn main:app` /
    `uvicorn main:app`, où `__main__` ne s'exécute pas.
    """
    erreurs: list[str] = []
    _erreur_api_key_manquante(erreurs)
    _erreur_rate_limiter_multi_worker(erreurs)
    _erreur_debug_et_docs_exposes(erreurs)
    _erreur_dimension_embedding_incoherente(erreurs)
    _erreurs_mode_production(erreurs)
    return erreurs


def _erreurs_mode_production(erreurs: list[str]) -> None:
    """Invariants supplémentaires quand `ENVIRONNEMENT=prod` (durcissement).

    En prod on refuse tout ce qui, en dev, n'est qu'un avertissement :
    verbosité de debug, docs exposées, et un bind public sans proxy de
    confiance déclaré (sinon rate-limit et journal d'accès sont aveugles
    à l'IP réelle des clients).
    """
    if cfg.environnement != "prod":
        return
    if cfg.debug:
        erreurs.append(
            "ENVIRONNEMENT=prod avec DEBUG=true — interdit (tracebacks/verbosité)."
        )
    if cfg.exposer_docs:
        erreurs.append(
            "ENVIRONNEMENT=prod avec EXPOSER_DOCS=true — interdit (fuite de schémas)."
        )
    if cfg.api_host == "0.0.0.0" and not cfg.trusted_proxies:  # noqa: S104
        erreurs.append(
            "ENVIRONNEMENT=prod, bind 0.0.0.0 et TRUSTED_PROXIES vide — l'IP "
            "client vue serait celle du proxy (rate-limit et logs cassés). "
            "Déclarer l'IP du proxy dans TRUSTED_PROXIES."
        )


def _erreur_dimension_embedding_incoherente(erreurs: list[str]) -> None:
    """Refuse le boot si `embedding_dimension` != `qdrant_vecteur_taille`.

    Un modèle d'embedding qui ne produit pas la dimension de la collection
    Qdrant fait échouer toutes les recherches silencieusement (0 evidence,
    réponses non ancrées) — observé quand `.env` pointait `BAAI/bge-m3`
    (KO) au lieu de `sentence-transformers/BAAI/bge-m3`.
    """
    if cfg.embedding_dimension != cfg.qdrant_vecteur_taille:
        erreurs.append(
            f"EMBEDDING_DIMENSION ({cfg.embedding_dimension}) != "
            f"QDRANT_VECTEUR_TAILLE ({cfg.qdrant_vecteur_taille}) — le modèle "
            f"'{cfg.modele_embedding}' et la collection Qdrant sont "
            "incompatibles, toute recherche renverra 0 résultat."
        )


def _erreur_debug_et_docs_exposes(erreurs: list[str]) -> None:
    """Refuse la combinaison `DEBUG=true` + `EXPOSER_DOCS=true` (fuite prod).

    Debug tolère les logs verbeux en dev, docs exposées tolère Swagger.
    Les deux ensemble = fuite d'info assurée en prod (schémas + verbosité
    des tracebacks). Un déploiement doit passer au moins l'un des deux
    à false.
    """
    if cfg.debug and cfg.exposer_docs:
        erreurs.append(
            "DEBUG=true et EXPOSER_DOCS=true simultanément — combinaison "
            "interdite en dehors du poste de développement (fuite de schémas "
            "et de tracebacks). Passer au moins l'un des deux à false."
        )


_API_KEY_PLACEHOLDER = "remplacez-par-une-cle-longue-et-aleatoire"
_API_KEY_LONGUEUR_MIN = 32
_COMMANDE_GEN_CLE = (
    "python3 -c \"import secrets; print('API_KEY=' + secrets.token_urlsafe(32))\""
)


def _erreur_api_key_manquante(erreurs: list[str]) -> None:
    """Refuse le boot si aucune clé API valide n'est configurée.

    Examine `API_KEY` et chaque entrée de `API_KEYS`. Cas fail-closed :
    - aucune clé → aucun endpoint métier ne répondra (503 systématique) ;
    - valeur placeholder de `.env.example` → déploiement non configuré ;
    - clé < 32 caractères → bruteforce trop accessible.
    """
    cles = cfg.cles_api_valides
    if not cles:
        erreurs.append(
            "Aucune clé API — définir API_KEY (ou API_KEYS) dans .env avant "
            f"démarrage. Générer une clé sûre : {_COMMANDE_GEN_CLE}"
        )
        return
    if any(c == _API_KEY_PLACEHOLDER for c in cles):
        erreurs.append(
            "API_KEY = valeur placeholder de .env.example — remplacer par une clé "
            f"réelle avant déploiement. Générer : {_COMMANDE_GEN_CLE}"
        )
        return
    courtes = [c for c in cles if len(c) < _API_KEY_LONGUEUR_MIN]
    if courtes:
        erreurs.append(
            f"{len(courtes)} clé(s) API trop courte(s) (< {_API_KEY_LONGUEUR_MIN} "
            f"caractères) — risque de bruteforce. Générer : {_COMMANDE_GEN_CLE}"
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
