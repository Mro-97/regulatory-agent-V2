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

Sécurité :
  - Tous les endpoints métier exigent l'en-tête X-API-Key (config API_KEY).
  - CORS restreint à cors_origins (config).
  - Vérification de l'en-tête Origin sur les mutations.
  - Rate limiting sur /ask et /ingest.
  - Limite de taille de requête (taille_max_requete_octets).
  - En-têtes de sécurité appliqués à toutes les réponses.
  - Les erreurs internes sont journalisées, jamais renvoyées au client.
"""

from __future__ import annotations

import hmac
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import cfg
from src.models import (
    ReponseQuestion,
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
""",
    openapi_tags=[
        {"name": "Système", "description": "Santé et état du système"},
        {"name": "Requêtes", "description": "Questions réglementaires avec RAG et filtrage temporel"},
        {"name": "Ingestion", "description": "Ajout de documents au corpus Qdrant"},
        {"name": "Validation", "description": "File de validation humaine (approve/reject)"},
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


# ---------------------------------------------------------------------------
# Middleware — sécurité
# ---------------------------------------------------------------------------


@app.middleware("http")
async def en_tetes_securite(request: Request, call_next):
    """Ajoute les en-têtes de sécurité à toutes les réponses."""
    reponse = await call_next(request)
    reponse.headers.setdefault("X-Content-Type-Options", "nosniff")
    reponse.headers.setdefault("X-Frame-Options", "DENY")
    reponse.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    reponse.headers.setdefault(
        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
    )
    return reponse


@app.middleware("http")
async def limite_taille_requete(request: Request, call_next):
    """Rejette les requêtes trop volumineuses.

    Deux vecteurs à couvrir :
      - `Content-Length` déclaré et supérieur à la limite → 413.
      - `Transfer-Encoding: chunked` (ou toute valeur ≠ identity) sur une
        méthode qui accepte un corps : sans `Content-Length`, la limite
        précédente était contournable. Les navigateurs légitimes
        n'émettent jamais de body chunked côté client — on refuse (411).
    """
    longueur = request.headers.get("Content-Length")
    if longueur and longueur.isdigit() and int(longueur) > cfg.taille_max_requete_octets:
        return JSONResponse(
            status_code=413,
            content={"detail": "Requête trop volumineuse."},
        )
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        te = (request.headers.get("Transfer-Encoding") or "").strip().lower()
        if te and te != "identity":
            return JSONResponse(
                status_code=411,
                content={"detail": "Transfer-Encoding non autorisé — Content-Length requis."},
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Sécurité — dépendances
# ---------------------------------------------------------------------------


def verifier_auth(request: Request) -> None:
    """Exige une clé API valide (fail-closed : API_KEY vide = refus)."""
    if not cfg.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentification non configurée.",
        )
    fournie = request.headers.get("X-API-Key", "")
    if not fournie or not hmac.compare_digest(fournie, cfg.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé API invalide.",
        )


def verifier_origine(request: Request) -> None:
    """Rejette les requêtes cross-site sur les mutations (anti-CSRF)."""
    origine = request.headers.get("Origin")
    if origine is None:
        return
    if origine in cfg.cors_origins:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Origine non autorisée.",
    )


class LimiteurDebit:
    """Limiteur de débit en mémoire (fenêtre glissante), clé = adresse IP.

    Le suivi est plafonné à `max_cles` entrées distinctes ; les entrées
    dont tous les horodatages sont hors fenêtre sont purgées à chaque
    appel. Sans ce plafond, un port-scan ou un flood d'IP sources faisait
    croître `_horodatages` indéfiniment (fuite mémoire).

    Le limiteur est un objet mono-process : en multi-worker (gunicorn -w N),
    la limite effective est × N. `main.valider_configuration_demarrage()`
    signale ce cas au boot.
    """

    def __init__(
        self,
        max_requetes: int,
        fenetre_secondes: int,
        max_cles: int = 10_000,
    ) -> None:
        self.max_requetes = max_requetes
        self.fenetre_secondes = fenetre_secondes
        self.max_cles = max_cles
        self._horodatages: defaultdict[str, list[float]] = defaultdict(list)
        self._verrou = Lock()

    def _purger(self, borne: float) -> None:
        """Retire les clés dont tous les horodatages sont hors fenêtre."""
        obsoletes = [
            k for k, ts in self._horodatages.items()
            if not ts or max(ts) <= borne
        ]
        for k in obsoletes:
            del self._horodatages[k]

    def autoriser(self, cle: str) -> bool:
        maintenant = time.monotonic()
        borne = maintenant - self.fenetre_secondes
        with self._verrou:
            # Purge opportuniste quand le dictionnaire dépasse le plafond.
            if len(self._horodatages) >= self.max_cles:
                self._purger(borne)
                if len(self._horodatages) >= self.max_cles and cle not in self._horodatages:
                    # Toujours saturé : on refuse la nouvelle clé plutôt que
                    # de laisser croître à l'infini.
                    return False
            valeurs = [t for t in self._horodatages[cle] if t > borne]
            self._horodatages[cle] = valeurs
            if len(valeurs) >= self.max_requetes:
                return False
            valeurs.append(maintenant)
            return True


_limiteur = LimiteurDebit(
    max_requetes=cfg.rate_limit_max_requetes,
    fenetre_secondes=cfg.rate_limit_fenetre_secondes,
)


def verifier_rate_limit(request: Request) -> None:
    """Limite le débit par IP sur les endpoints coûteux."""
    cle = request.client.host if request.client else "inconnu"
    if not _limiteur.autoriser(cle):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de requêtes, réessayez plus tard.",
        )


AuthDep = Depends(verifier_auth)
OrigineDep = Depends(verifier_origine)
DebitDep = Depends(verifier_rate_limit)


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
    description="Vérifie que l'API est opérationnelle. Retourne l'horodatage et la version.",
)
async def health() -> dict[str, object]:
    reponse: dict[str, object] = {
        "statut": "ok",
        "app": cfg.app_nom,
        "version": cfg.app_version,
        "horodatage": datetime.now(timezone.utc).isoformat(),
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
    description="Pipeline RAG complet : retrieval vectoriel → filtrage temporel → explication LLM → citations.",
    dependencies=[AuthDep, OrigineDep, DebitDep],
)
async def poser_question(requete: RequeteQuestion, orchestrateur: OrchestrateurDep):
    logger.info("POST /ask — %r", requete.question[:80])
    try:
        return await orchestrateur.traiter(requete)
    except Exception:
        logger.exception("Erreur /ask")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors du traitement de la question.",
        )


@app.post(
    "/ingest",
    response_model=ReponseIngestion,
    status_code=202,
    tags=["Ingestion"],
    summary="Ingérer un document",
    description="Ajoute un document JSON canonique (format DocumentReglementaire) au corpus Qdrant.",
    dependencies=[AuthDep, OrigineDep, DebitDep],
)
async def ingerer(requete: RequeteIngestion, orchestrateur: OrchestrateurDep):
    from src.orchestrator import DocumentDejaIndexeError

    try:
        return await orchestrateur.ingerer(requete)
    except DocumentDejaIndexeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception:
        logger.exception("Erreur /ingest")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors de l'ingestion.",
        )


@app.get(
    "/pending",
    response_model=ReponseTachesPendantes,
    tags=["Validation"],
    summary="Tâches en attente",
    description="Retourne toutes les tâches en attente de validation humaine.",
    dependencies=[AuthDep],
)
async def pending(orchestrateur: OrchestrateurDep):
    try:
        return await orchestrateur.lister_taches_pendantes()
    except Exception:
        logger.exception("Erreur /pending")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors de la récupération des tâches.",
        )


@app.post(
    "/approve",
    response_model=ReponseDecisionValidation,
    tags=["Validation"],
    summary="Approuver une tâche",
    description="Approuve une tâche de validation identifiée par son tache_id.",
    dependencies=[AuthDep, OrigineDep],
)
async def approuver(requete: RequeteDecisionValidation, orchestrateur: OrchestrateurDep):
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
        )
    except Exception:
        logger.exception("Erreur /approve")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors de la validation.",
        )


@app.post(
    "/reject",
    response_model=ReponseDecisionValidation,
    tags=["Validation"],
    summary="Rejeter une tâche",
    description="Rejette une tâche de validation identifiée par son tache_id.",
    dependencies=[AuthDep, OrigineDep],
)
async def rejeter(requete: RequeteDecisionValidation, orchestrateur: OrchestrateurDep):
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
        )
    except Exception:
        logger.exception("Erreur /reject")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors de la validation.",
        )
