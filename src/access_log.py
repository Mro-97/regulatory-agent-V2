"""src/access_log.py — journal d'accès HTTP structuré (une ligne par requête).

Les lignes uvicorn brutes ne disent pas *pourquoi* un 401/403 : ce
middleware émet, pour chaque requête, une ligne clé=valeur greppable avec
l'IP, une empreinte de la clé API (jamais la clé), le motif du refus, et
les en-têtes utiles au diagnostic (`Origin`, `User-Agent`, `Referer`).

Derrière un tunnel SSH, `request.client.host` vaut toujours 127.0.0.1 —
`X-Forwarded-For` est loggé pour le jour où un proxy est ajouté.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING

from config import cfg

if TYPE_CHECKING:
    from fastapi import FastAPI, Request, Response
    from starlette.middleware.base import RequestResponseEndpoint

logger = logging.getLogger("acces")

_UA_MAX = 120


def _empreinte_cle(fournie: str | None) -> str:
    """`absente` / `invalide` / hash court — jamais la clé en clair."""
    attendue = (cfg.api_key or "").strip()
    proposee = (fournie or "").strip()
    if not proposee:
        return "absente"
    if attendue and proposee != attendue:
        return "invalide"
    return hashlib.sha256(proposee.encode("utf-8")).hexdigest()[:8]


def _identite(request: Request) -> str:
    """Nom lisible du client : X-User, sinon X-Client-Id, sinon empreinte de clé."""
    entete = request.headers.get("X-User") or request.headers.get("X-Client-Id")
    return entete[:40] if entete else _empreinte_cle(request.headers.get("X-API-Key"))


def _ip_client_reelle(request: Request) -> str:
    """IP du navigateur si un proxy la transmet (XFF / X-Real-IP), sinon '-'.

    Un simple tunnel `ssh -L` ne la transmet PAS : tout arrive en 127.0.0.1.
    """
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.headers.get("X-Real-IP", "-")


def _motif(request: Request, statut: int) -> str:
    """Raison lisible d'un statut >= 400, dérivée des en-têtes de la requête."""
    if statut == 401:
        return "cle_absente" if not request.headers.get("X-API-Key") else "cle_invalide"
    if statut == 403:
        origine = request.headers.get("Origin")
        if origine and origine not in cfg.cors_origins:
            return "origine_refusee"
        return "interdit"
    return {429: "rate_limite", 413: "corps_trop_grand", 411: "encodage_refuse"}.get(
        statut, "erreur" if statut >= 500 else "-"
    )


CHEMINS_LOGUES_PAR_LA_ROUTE = frozenset({"/ask", "/ask/stream"})


def journaliser_acces_requete(
    request: Request, statut: int, duree_ms: int, question: str | None = None
) -> None:
    """Émet la ligne d'accès (WARNING si statut >= 400, INFO sinon).

    Appelée par le middleware pour tout endpoint, et directement par les
    routes `/ask*` (qui passent `question` — non récupérable côté
    middleware à travers `BaseHTTPMiddleware`).
    """
    niveau = logger.warning if statut >= 400 else logger.info
    niveau(
        "acces user=%s cle=%s ip=%s ip_client=%s methode=%s chemin=%s statut=%d "
        "duree_ms=%d motif=%s question=%r origin=%s ua=%r ref=%s",
        _identite(request),
        _empreinte_cle(request.headers.get("X-API-Key")),
        request.client.host if request.client else "?",
        _ip_client_reelle(request),
        request.method,
        request.url.path,
        statut,
        duree_ms,
        _motif(request, statut) if statut >= 400 else "-",
        (question[:200] if question else "-"),
        request.headers.get("Origin", "-"),
        request.headers.get("User-Agent", "-")[:_UA_MAX],
        request.headers.get("Referer", "-"),
    )


def installer_journal_acces(app: FastAPI) -> None:
    """Attache le middleware de journal d'accès — à appeler en dernier (outermost).

    `duree_ms` mesure jusqu'à l'émission des en-têtes ; pour une réponse
    en flux (`/ask/stream`) c'est le time-to-first-byte, pas la durée
    totale du flux.
    """

    @app.middleware("http")
    async def journal_acces(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        debut = time.perf_counter()
        reponse = await call_next(request)
        duree_ms = int((time.perf_counter() - debut) * 1000)
        # Pour /ask*, la route logue elle-même (avec la question). Le
        # middleware ne complète que les rejets avant-route (401/403/429…).
        deja_logue = (
            request.url.path in CHEMINS_LOGUES_PAR_LA_ROUTE
            and reponse.status_code < 400
        )
        if not deja_logue:
            journaliser_acces_requete(request, reponse.status_code, duree_ms)
        return reponse
