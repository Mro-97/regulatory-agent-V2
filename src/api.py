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

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from config import cfg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# `LimiteurDebit` et `_limiteur` ont été déplacés vers src/api_security.py
# (§12 étape 6). Les alias `X as X` ci-dessous les ré-exportent sous leur
# nom d'origine pour compatibilité descendante — les tests monkey-patchent
# `src.api._limiteur`, il doit rester atteignable comme attribut de module.
from src.api_security import (
    AuthDep,
    DebitDep,
    OrigineDep,
    installer_middlewares,
)
from src.api_security import (
    LimiteurDebit as LimiteurDebit,
)
from src.api_security import (
    _limiteur as _limiteur,
)
from src.models import (
    ReponseDecisionValidation,
    ReponseIngestion,
    ReponseQuestion,
    ReponseTachesPendantes,
    RequeteDecisionValidation,
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
# Application FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
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


# Middlewares, dépendances de sécurité et limiteur de débit extraits dans
# src/api_security.py (§12 étape 6). Les décorateurs @app.middleware sont
# posés ci-dessous via installer_middlewares().
installer_middlewares(app)


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
# Interface web
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def interface(request: Request) -> HTMLResponse:
    """Rend l'interface web principale (index.html) sans embarquer la clé API."""
    # C1 : la clé API n'est JAMAIS embarquée dans la page (elle serait
    # visible via `view source` pour tout visiteur non authentifié).
    # Le frontend la demande à l'utilisateur au premier chargement de
    # l'onglet et la stocke en sessionStorage.
    return templates.TemplateResponse(request=request, name="index.html")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    tags=["Système"],
    summary="État du système",
    description="Vérifie que l'API est opérationnelle. Retourne l'horodatage et la version.",  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
)
async def health() -> dict[str, object]:
    """Endpoint public de santé : statut + horodatage + statut d'audit."""
    # M4 : /health est public — on n'expose ni le nom d'application ni la
    # version (fingerprinting). Le statut audit reste utile aux sondes
    # d'exploitation locales et ne révèle pas d'info versionnée.
    reponse: dict[str, object] = {
        "statut": "ok",
        "horodatage": datetime.now(UTC).isoformat(),
    }
    try:
        from src.audit import obtenir_gestionnaire

        gestionnaire = await obtenir_gestionnaire()
        reponse["audit"] = gestionnaire.statut()
    except Exception:
        logger.exception("Statut audit indisponible pour /health")
    return reponse


@app.post(
    "/ask",
    response_model=ReponseQuestion,
    tags=["Requêtes"],
    summary="Poser une question réglementaire",
    description="Pipeline RAG complet : retrieval vectoriel → filtrage temporel → explication LLM → citations.",  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
    dependencies=[AuthDep, OrigineDep, DebitDep],
)
async def poser_question(
    requete: RequeteQuestion,
    orchestrateur: OrchestrateurDep,
) -> ReponseQuestion:
    """Traite une question réglementaire via le pipeline multi-agent."""
    logger.info("POST /ask — %r", requete.question[:80])
    try:
        return await orchestrateur.traiter(requete)
    except Exception:
        logger.exception("Erreur /ask")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors du traitement de la question.",
        ) from None


@app.post(
    "/ingest",
    response_model=ReponseIngestion,
    status_code=202,
    tags=["Ingestion"],
    summary="Ingérer un document",
    description="Ajoute un document JSON canonique (format DocumentReglementaire) au corpus Qdrant.",  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
    dependencies=[AuthDep, OrigineDep, DebitDep],
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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Erreur /ingest")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors de l'ingestion.",
        ) from None


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
    try:
        return await orchestrateur.lister_taches_pendantes()
    except Exception:
        logger.exception("Erreur /pending")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors de la récupération des tâches.",
        ) from None


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
    try:
        return await orchestrateur.valider_tache(
            tache_id=requete.tache_id,
            decision=StatutValidation.APPROUVE,
            commentaire=requete.commentaire,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Erreur /approve")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors de la validation.",
        ) from None


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
    try:
        return await orchestrateur.valider_tache(
            tache_id=requete.tache_id,
            decision=StatutValidation.REJETE,
            commentaire=requete.commentaire,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Erreur /reject")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors de la validation.",
        ) from None
