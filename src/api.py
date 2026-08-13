"""
src/api.py — API FastAPI de Regulatory Agent V2
================================================

Endpoints :
  GET  /          → interface web
  GET  /health    → statut
  POST /ask       → question réglementaire
  POST /ingest    → ingestion document
  GET  /pending   → tâches en attente
  POST /approve   → approuver une tâche
  POST /reject    → rejeter une tâche
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import cfg
from src.models import (
    ReponsQuestion,
    ReponseDecisionValidation,
    ReponseIngestion,
    ReponseTachesPendantes,
    RequeteDecisionValidation,
    RequeteIngestion,
    RequeteQuestion,
    StatutValidation,
)
from src.orchestrator import Orchestrateur

logger = logging.getLogger(__name__)

RACINE            = Path(__file__).parent.parent
DOSSIER_STATIC    = RACINE / "web" / "static"
DOSSIER_TEMPLATES = RACINE / "web" / "templates"

app = FastAPI(
    title=cfg.app_nom,
    version=cfg.app_version,
    description="API locale de veille réglementaire. Inférence 100 % locale via MLX.",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(DOSSIER_STATIC)), name="static")
templates = Jinja2Templates(directory=str(DOSSIER_TEMPLATES))

# ---------------------------------------------------------------------------
# Orchestrateur
# ---------------------------------------------------------------------------

_orchestrateur: Orchestrateur | None = None

def obtenir_orchestrateur() -> Orchestrateur:
    global _orchestrateur
    if _orchestrateur is None:
        _orchestrateur = Orchestrateur()
    return _orchestrateur

OrchestrateurDep = Annotated[Orchestrateur, Depends(obtenir_orchestrateur)]

# ---------------------------------------------------------------------------
# Interface web
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def interface(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Système"])
async def health() -> dict:
    return {
        "statut": "ok",
        "app": cfg.app_nom,
        "version": cfg.app_version,
        "horodatage": datetime.now(timezone.utc).isoformat(),
    }

@app.post("/ask", response_model=ReponsQuestion, tags=["Requêtes"])
async def poser_question(requete: RequeteQuestion, orchestrateur: OrchestrateurDep):
    logger.info("POST /ask — %r", requete.question[:80])
    try:
        return await orchestrateur.traiter(requete)
    except Exception as exc:
        logger.error("Erreur /ask : %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/ingest", response_model=ReponseIngestion, status_code=202, tags=["Ingestion"])
async def ingerer(requete: RequeteIngestion, orchestrateur: OrchestrateurDep):
    try:
        return await orchestrateur.ingerer(requete)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.get("/pending", response_model=ReponseTachesPendantes, tags=["Validation"])
async def pending(orchestrateur: OrchestrateurDep):
    try:
        return await orchestrateur.lister_taches_pendantes()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/approve", response_model=ReponseDecisionValidation, tags=["Validation"])
async def approuver(requete: RequeteDecisionValidation, orchestrateur: OrchestrateurDep):
    try:
        return await orchestrateur.valider_tache(
            tache_id=requete.tache_id,
            decision=StatutValidation.APPROUVE,
            commentaire=requete.commentaire,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/reject", response_model=ReponseDecisionValidation, tags=["Validation"])
async def rejeter(requete: RequeteDecisionValidation, orchestrateur: OrchestrateurDep):
    try:
        return await orchestrateur.valider_tache(
            tache_id=requete.tache_id,
            decision=StatutValidation.REJETE,
            commentaire=requete.commentaire,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
