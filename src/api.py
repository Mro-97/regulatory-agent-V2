"""src/api.py — API FastAPI de Regulatory Agent V2
================================================

Endpoints :
  GET  /          → interface web
  GET  /health    → statut
  POST /ask       → question réglementaire
  POST /ingest    → ingestion document
  GET  /pending   → tâches en attente
  POST /approve   → approuver une tâche
  POST /reject    → rejeter une tâche

Sécurité :
  - Tous les endpoints métier exigent l'en-tête X-API-Key (config API_KEY).
  - CORS restreint à cors_origins (config).
  - Vérification de l'en-tête Origin sur les mutations.
  - Rate limiting sur /ask et /ingest.
  - Limite de taille de requête (taille_max_requete_octets).
  - En-têtes de sécurité appliqués à toutes les réponses.
  - Les erreurs internes sont journalisées, jamais renvoyées au client.
"""  # noqa: D205, D415

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from config import cfg
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import RequestResponseEndpoint

# `LimiteurDebit` et `_limiteur` ont été déplacés vers src/api_security.py
# (§12 étape 6). Les alias `X as X` ci-dessous les ré-exportent sous leur
# nom d'origine pour compatibilité descendante — les tests monkey-patchent
# `src.api._limiteur`, il doit rester atteignable comme attribut de module.
from src.api_security import (
    AuthDep,
    OrigineDep,
    installer_middlewares,
)
from src.api_security import (
    DebitDep as DebitDep,  # ré-exporté pour compat descendante (import ext.)
)
from src.api_security import (
    LimiteurDebit as LimiteurDebit,
)
from src.api_security import (
    _limiteur as _limiteur,
)
from src.models import (
    ReponseDecisionValidation,
    ReponseFeedback,
    ReponseIngestion,
    ReponseQuestion,
    ReponseSuiviTache,
    ReponseTachesPendantes,
    RequeteDecisionValidation,
    RequeteFeedback,
    RequeteIngestion,
    RequeteQuestion,
    StatutValidation,
)
from src.orchestrator import Orchestrateur

logger = logging.getLogger(__name__)

RACINE = Path(__file__).parent.parent
DOSSIER_STATIC = RACINE / "web" / "static"
DOSSIER_TEMPLATES = RACINE / "web" / "templates"


# ---------------------------------------------------------------------------
# Cycle de vie — validation de configuration au démarrage
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _cycle_de_vie(_app: FastAPI) -> AsyncIterator[None]:
    """Refuse de servir si la configuration de démarrage est invalide.

    `main.py` fait déjà cette vérif quand on lance `python3 main.py`, mais
    PAS sous `gunicorn main:app` / `uvicorn main:app` (où `__main__` ne
    tourne pas). Ce lifespan la rejoue : un worker gunicorn mal configuré
    (clé placeholder, DEBUG+DOCS, ENVIRONNEMENT=prod incohérent…) ne
    démarre pas.
    """
    from main import valider_configuration_demarrage

    erreurs = valider_configuration_demarrage()
    if erreurs:
        for err in erreurs:
            logger.critical("Configuration invalide : %s", err)
        message = "Démarrage refusé — configuration invalide : " + " | ".join(erreurs)
        raise RuntimeError(message)
    yield


# ---------------------------------------------------------------------------
# Application FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    lifespan=_cycle_de_vie,
    title=cfg.app_nom,
    version=cfg.app_version,
    description="""
## Regulatory Agent V2 — API de veille réglementaire

Système local d'assistance réglementaire basé sur RAG (Retrieval-Augmented Generation).
Inférence 100 % locale via MLX sur Apple Silicon — aucune donnée transmise à l'extérieur.

### Authentification
Tous les endpoints (sauf /health et l'interface web) exigent l'en-tête `X-API-Key`.
""",  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
    openapi_tags=[
        {"name": "Système", "description": "Santé et état du système"},
        {
            "name": "Requêtes",
            "description": "Questions réglementaires avec RAG et filtrage temporel",
        },
        {"name": "Ingestion", "description": "Ajout de documents au corpus Qdrant"},
        {
            "name": "Validation",
            "description": "File de validation humaine (approve/reject)",
        },
    ],
    docs_url="/docs" if cfg.exposer_docs else None,
    redoc_url="/redoc" if cfg.exposer_docs else None,
    openapi_url="/openapi.json" if cfg.exposer_docs else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
    allow_credentials=False,
    max_age=600,
)

app.mount("/static", StaticFiles(directory=str(DOSSIER_STATIC)), name="static")
templates = Jinja2Templates(directory=str(DOSSIER_TEMPLATES))


def _version_assets() -> str:
    """Empreinte des assets front (mtime app.js + style.css) pour casser le cache."""
    try:
        mtimes = sum(
            (DOSSIER_STATIC / f).stat().st_mtime
            for f in ("app.js", "style.css", "theme-init.js")
        )
        return str(int(mtimes))
    except OSError:
        return cfg.app_version


# Middlewares, dépendances de sécurité et limiteur de débit extraits dans
# src/api_security.py (§12 étape 6). Les décorateurs @app.middleware sont
# posés ci-dessous via installer_middlewares().
installer_middlewares(app)

# Rate limit ajouté en dernier pour être exécuté en premier (Starlette LIFO) :
# comptage effectué avant parsing du body → les requêtes malformées
# consomment aussi le quota (auparavant elles court-circuitaient DebitDep).
from src.rate_limit_middleware import installer_rate_limit  # noqa: E402

installer_rate_limit(app)

# Journal d'accès en dernier => middleware le plus externe : il voit le
# statut final, y compris les 429/413/411 posés par les middlewares
# internes.
from src.access_log import installer_journal_acces  # noqa: E402

installer_journal_acces(app)


@app.middleware("http")
async def _rediriger_https(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Redirige http → https (308) si `forcer_https`, sauf /health.

    Le schéma d'origine vient de `X-Forwarded-Proto` uniquement si le pair
    est un `trusted_proxy` (cf. src/net) — sinon un client ne peut pas
    prétendre être déjà en https.
    """
    if cfg.forcer_https and request.url.path != "/health":
        from src.net import schema_origine

        if schema_origine(request) == "http":
            cible = request.url.replace(scheme="https")
            return RedirectResponse(str(cible), status_code=308)
    return await call_next(request)


# Garde de concurrence pour le pipeline /ask.
#
# Un timeout MLX n'interrompt pas le thread de génération (src/mlx_utils y
# recycle l'exécuteur après un timeout pour ne pas rester bloqué). Ce
# plafond côté API borne en plus le nombre de pipelines simultanés : au-delà
# on répond 503 tout de suite plutôt que d'empiler des requêtes qui
# attendraient toutes le verrou MLX.
#
# Compteur simple : l'event loop asyncio est mono-thread et il n'y a aucun
# `await` entre le test et l'incrément — pas besoin de verrou. `entrer()`
# renvoie False si saturé ; l'appelant lève alors le 503.
_MSG_ASK_SATURE = "Service occupé (trop de requêtes simultanées) — réessayez."


class _GardeAsk:
    """Plafond non bloquant du nombre de pipelines /ask simultanés."""

    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.en_cours = 0

    def entrer(self) -> bool:
        """Réserve une place ; False si le plafond est atteint (<=0 = illimité)."""
        if self.maximum <= 0:
            return True
        if self.en_cours >= self.maximum:
            return False
        self.en_cours += 1
        return True

    def sortir(self) -> None:
        """Libère une place (borne à 0)."""
        self.en_cours = max(0, self.en_cours - 1)


_ask_garde = _GardeAsk(cfg.ask_max_concurrent)


# ---------------------------------------------------------------------------
# Orchestrateur
# ---------------------------------------------------------------------------

_orchestrateur: Orchestrateur | None = None


def obtenir_orchestrateur() -> Orchestrateur:
    """Retourne le singleton `Orchestrateur`, instancié au premier appel."""
    global _orchestrateur
    if _orchestrateur is None:
        _orchestrateur = Orchestrateur()
    return _orchestrateur


OrchestrateurDep = Annotated[Orchestrateur, Depends(obtenir_orchestrateur)]


# ---------------------------------------------------------------------------
# Helpers d'erreur HTTP (mutualisation §Clean Code SRP)
# ---------------------------------------------------------------------------


# Messages HTTP 500 — sortis du corps de fonction (règle TRY003 §8) pour
# ne pas polluer les `raise` avec des chaînes littérales longues.
_MSG_ERREUR_INGESTION = "Erreur interne lors de l'ingestion."
_MSG_ERREUR_VALIDATION = "Erreur interne lors de la validation."
_MSG_ERREUR_PENDING = "Erreur interne lors de la récupération des tâches."
_MSG_ERREUR_ASK = "Erreur interne lors du traitement de la question."
_MSG_ERREUR_FEEDBACK = "Erreur interne lors de l'enregistrement du signalement."
_MSG_QUEUE_INDISPONIBLE = "File de validation temporairement indisponible."


def _erreur_500(detail: str) -> HTTPException:
    """Fabrique une HTTPException 500 avec `detail` (pour `raise ... from`)."""
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail
    )


def _erreur_503(detail: str) -> HTTPException:
    """Fabrique une HTTPException 503 (backend externe indisponible)."""
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


async def _appliquer_decision_validation(
    orchestrateur: Orchestrateur,
    requete: RequeteDecisionValidation,
    decision: StatutValidation,
    *,
    endpoint: str,
) -> ReponseDecisionValidation:
    """Applique une décision (APPROUVE/REJETE) avec mapping d'erreurs HTTP.

    Mutualisation des handlers `/approve` et `/reject` qui n'ont
    fonctionnellement qu'un statut cible différent.
    """
    try:
        reponse = await orchestrateur.valider_tache(
            tache_id=requete.tache_id,
            decision=decision,
            commentaire=requete.commentaire,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except Exception:
        logger.exception("Erreur %s", endpoint)
        raise _erreur_500(_MSG_ERREUR_VALIDATION) from None
    # Trace d'audit applicative — Redis conserve le détail complet
    # (commentaire, contenu), on ne journalise ici que l'identifiant
    # et le statut pour rester traçable même si Redis est purgé.
    logger.info(
        "Décision validation — endpoint=%s tache_id=%s statut=%s",
        endpoint,
        reponse.tache_id,
        reponse.nouveau_statut.value,
    )
    return reponse


# ---------------------------------------------------------------------------
# Interface web
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def interface(request: Request) -> HTMLResponse:
    """Rend l'interface web principale (index.html) sans embarquer la clé API."""
    # C1 : la clé API n'est JAMAIS embarquée dans la page (elle serait
    # visible via `view source` pour tout visiteur non authentifié).
    # Le frontend la demande à l'utilisateur au premier chargement de
    # l'onglet et la stocke en sessionStorage.
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"asset_v": _version_assets()},
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    tags=["Système"],
    summary="État du système",
    description="Vérifie que l'API est opérationnelle. Retourne l'horodatage.",
)
async def health() -> dict[str, object]:
    """Endpoint public de santé : uniquement statut + horodatage.

    Rien d'autre n'est exposé sans authentification (ni nom/version
    d'application — fingerprinting — ni état du backend d'audit). Le
    détail d'audit est disponible sur `/health/details` (clé API requise).
    """
    return {"statut": "ok", "horodatage": datetime.now(UTC).isoformat()}


@app.get(
    "/health/details",
    tags=["Système"],
    summary="État détaillé (authentifié)",
    description="Comme /health, plus l'état du backend d'audit. Exige X-API-Key.",
    dependencies=[AuthDep],
    include_in_schema=False,
)
async def health_details() -> dict[str, object]:
    """Santé + état du backend d'audit — réservé aux appelants authentifiés."""
    reponse: dict[str, object] = {
        "statut": "ok",
        "horodatage": datetime.now(UTC).isoformat(),
    }
    try:
        from src.audit import obtenir_gestionnaire

        gestionnaire = await obtenir_gestionnaire()
        reponse["audit"] = gestionnaire.statut()
    except Exception:
        logger.exception("Statut audit indisponible pour /health/details")
    return reponse


@app.post(
    "/ask",
    response_model=ReponseQuestion,
    tags=["Requêtes"],
    summary="Poser une question réglementaire",
    description="Pipeline RAG complet : retrieval vectoriel → filtrage temporel → explication LLM → citations.",  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
    dependencies=[AuthDep, OrigineDep],  # rate limit géré par middleware
)
async def poser_question(
    requete: RequeteQuestion,
    orchestrateur: OrchestrateurDep,
    request: Request,
) -> ReponseQuestion:
    """Traite une question réglementaire via le pipeline multi-agent."""
    from src.access_log import journaliser_acces_requete

    debut = time.perf_counter()
    statut = 200
    if not _ask_garde.entrer():
        journaliser_acces_requete(request, 503, 0, requete.question)
        raise _erreur_503(_MSG_ASK_SATURE)
    try:
        return await orchestrateur.traiter(requete)
    except Exception:
        statut = 500
        logger.exception("Erreur /ask")
        raise _erreur_500(_MSG_ERREUR_ASK) from None
    finally:
        _ask_garde.sortir()
        journaliser_acces_requete(
            request, statut, int((time.perf_counter() - debut) * 1000), requete.question
        )


async def _sse_ask(
    requete: RequeteQuestion, orchestrateur: Orchestrateur
) -> AsyncIterator[str]:
    """Formatte les événements de `traiter_stream` en trames Server-Sent Events.

    La place réservée dans `_ask_garde` (prise par la route) est libérée
    ici, quand le flux se termine ou est interrompu.
    """
    try:
        async for evenement, charge in orchestrateur.traiter_stream(requete):
            corps = json.dumps(charge, ensure_ascii=False)
            yield f"event: {evenement}\ndata: {corps}\n\n"
    except Exception:
        logger.exception("Flux /ask/stream interrompu")
        yield 'event: erreur\ndata: {"detail": "Flux interrompu."}\n\n'
    finally:
        _ask_garde.sortir()


@app.post(
    "/ask/stream",
    tags=["Requêtes"],
    summary="Poser une question — réponse diffusée (SSE)",
    description="Comme /ask, mais diffuse la synthèse token par token (text/event-stream) : événements `etape`, `token`, `fin`, `erreur`.",  # noqa: E501
    dependencies=[AuthDep, OrigineDep],
)
async def poser_question_stream(
    requete: RequeteQuestion,
    orchestrateur: OrchestrateurDep,
    request: Request,
) -> StreamingResponse:
    """Diffuse la réponse en Server-Sent Events."""
    from src.access_log import journaliser_acces_requete

    if not _ask_garde.entrer():
        journaliser_acces_requete(request, 503, 0, requete.question)
        raise _erreur_503(_MSG_ASK_SATURE)
    journaliser_acces_requete(request, 200, 0, requete.question)
    return StreamingResponse(
        _sse_ask(requete, orchestrateur),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post(
    "/ingest",
    response_model=ReponseIngestion,
    status_code=202,
    tags=["Ingestion"],
    summary="Ingérer un document",
    description="Ajoute un document JSON canonique (format DocumentReglementaire) au corpus Qdrant.",  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
    dependencies=[AuthDep, OrigineDep],  # rate limit géré par middleware
)
async def ingerer(
    requete: RequeteIngestion,
    orchestrateur: OrchestrateurDep,
) -> ReponseIngestion:
    """Ingère un document JSON canonique (chunking + embedding + upsert Qdrant)."""
    from src.orchestrator import DocumentDejaIndexeError

    try:
        return await orchestrateur.ingerer(requete)
    except DocumentDejaIndexeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception:
        logger.exception("Erreur /ingest")
        raise _erreur_500(_MSG_ERREUR_INGESTION) from None


@app.get(
    "/pending",
    response_model=ReponseTachesPendantes,
    tags=["Validation"],
    summary="Tâches en attente",
    description="Retourne toutes les tâches en attente de validation humaine.",
    dependencies=[AuthDep],
)
async def pending(orchestrateur: OrchestrateurDep) -> ReponseTachesPendantes:
    """Liste les tâches Redis en attente de validation humaine."""
    from src.errors import QueueBackendError

    try:
        return await orchestrateur.lister_taches_pendantes()
    except QueueBackendError as exc:
        # Backend HS : 503 explicite plutôt qu'une liste vide qui laisse
        # croire à un opérateur que rien n'est en attente.
        logger.exception("Redis indisponible pour /pending")
        raise _erreur_503(_MSG_QUEUE_INDISPONIBLE) from exc
    except Exception:
        logger.exception("Erreur /pending")
        raise _erreur_500(_MSG_ERREUR_PENDING) from None


@app.post(
    "/approve",
    response_model=ReponseDecisionValidation,
    tags=["Validation"],
    summary="Approuver une tâche",
    description="Approuve une tâche de validation identifiée par son tache_id.",
    dependencies=[AuthDep, OrigineDep],
)
async def approuver(
    requete: RequeteDecisionValidation,
    orchestrateur: OrchestrateurDep,
) -> ReponseDecisionValidation:
    """Approuve la tâche identifiée par `tache_id` (statut → APPROUVE)."""
    return await _appliquer_decision_validation(
        orchestrateur, requete, StatutValidation.APPROUVE, endpoint="/approve"
    )


@app.post(
    "/reject",
    response_model=ReponseDecisionValidation,
    tags=["Validation"],
    summary="Rejeter une tâche",
    description="Rejette une tâche de validation identifiée par son tache_id.",
    dependencies=[AuthDep, OrigineDep],
)
async def rejeter(
    requete: RequeteDecisionValidation,
    orchestrateur: OrchestrateurDep,
) -> ReponseDecisionValidation:
    """Rejette la tâche identifiée par `tache_id` (statut → REJETE)."""
    return await _appliquer_decision_validation(
        orchestrateur, requete, StatutValidation.REJETE, endpoint="/reject"
    )


@app.get(
    "/tache/{tache_id}",
    response_model=ReponseSuiviTache,
    tags=["Validation"],
    summary="Suivi d'une tâche de validation",
    description="Statut courant d'une tâche (en attente / approuvée / rejetée) par son id.",  # noqa: E501
    dependencies=[AuthDep],
)
async def suivi_tache(
    tache_id: UUID,
    orchestrateur: OrchestrateurDep,
) -> ReponseSuiviTache:
    """Retourne le statut courant d'une tâche pour le demandeur qui la suit."""
    from src.errors import QueueBackendError

    try:
        tache = await orchestrateur.obtenir_tache(tache_id)
    except QueueBackendError as exc:
        logger.exception("Redis indisponible pour /tache")
        raise _erreur_503(_MSG_QUEUE_INDISPONIBLE) from exc
    if tache is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tâche introuvable.")
    return ReponseSuiviTache(
        tache_id=tache.tache_id,
        statut=tache.statut,
        horodatage_creation=tache.horodatage_creation,
        horodatage_traitement=tache.horodatage_traitement,
        commentaire_validateur=tache.commentaire_validateur,
        escaladee=tache.escaladee,
    )


def _enregistrer_signalement(requete: RequeteFeedback) -> ReponseFeedback:
    """Ajoute une ligne JSONL au fichier de signalements (append atomique)."""
    from src.stockage_local import ecrire_ligne_protegee

    horodatage = datetime.now(UTC)
    ligne = {
        "horodatage": horodatage.isoformat(),
        "request_id": str(requete.request_id),
        "motif": requete.motif.value,
        "commentaire": requete.commentaire,
    }
    ecrire_ligne_protegee(
        Path(cfg.feedback_local_path), json.dumps(ligne, ensure_ascii=False) + "\n"
    )
    logger.info(
        "Signalement — request_id=%s motif=%s", requete.request_id, requete.motif.value
    )
    return ReponseFeedback(horodatage=horodatage)


@app.post(
    "/feedback",
    response_model=ReponseFeedback,
    status_code=202,
    tags=["Qualité"],
    summary="Signaler une réponse",
    description="Enregistre un signalement utilisateur sur une réponse (revue qualité, calibration).",  # noqa: E501
    dependencies=[AuthDep, OrigineDep],
)
async def signaler(requete: RequeteFeedback) -> ReponseFeedback:
    """Journalise un signalement (`request_id` + motif + commentaire) en JSONL."""
    try:
        return _enregistrer_signalement(requete)
    except OSError:
        logger.exception("Écriture du signalement impossible")
        raise _erreur_500(_MSG_ERREUR_FEEDBACK) from None
