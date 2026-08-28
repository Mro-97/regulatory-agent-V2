"""src/agents/retriever_helpers.py — Filtres Qdrant et conversion payload.

Extraits de src/agents/retriever.py (§12 étape 6). Regroupe les
constructions de FieldCondition/IsNullCondition, la conversion d'un
ScoredPoint en EvidenceRecuperee et le parsing des dates de payload.
Le Retriever ne conserve que l'orchestration (embedding, deux passes,
fusion, tri).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING

from qdrant_client.http.models import (
    DatetimeRange,
    FieldCondition,
    IsNullCondition,
    MatchAny,
    PayloadField,
)
from src.models import EvidenceRecuperee

if TYPE_CHECKING:
    from qdrant_client.http.models import ScoredPoint
    from src.models import SourceReglementaire

logger = logging.getLogger(__name__)


def filtre_valid_from(date_ref: date) -> FieldCondition:
    """Condition Qdrant : valid_from <= date_ref."""
    return FieldCondition(
        key="valid_from",
        range=DatetimeRange(lte=datetime.combine(date_ref, datetime.min.time())),
    )


def filtre_valid_to_present(date_ref: date) -> FieldCondition:
    """Condition Qdrant : valid_to >= date_ref (champ renseigné)."""
    return FieldCondition(
        key="valid_to",
        range=DatetimeRange(gte=datetime.combine(date_ref, datetime.min.time())),
    )


def filtre_valid_to_null() -> IsNullCondition:
    """Condition Qdrant : valid_to est nul (en vigueur indéfiniment)."""
    return IsNullCondition(is_null=PayloadField(key="valid_to"))


def filtre_themes(themes: list[str]) -> FieldCondition | None:
    """Condition : le payload 'themes' contient au moins un des thèmes demandés."""
    themes_valides = [t for t in themes if t]
    if not themes_valides:
        return None
    return FieldCondition(key="themes", match=MatchAny(any=themes_valides))


def filtre_sources(sources: list[SourceReglementaire]) -> FieldCondition | None:
    """Condition : le payload 'source' correspond à l'une des sources demandées."""
    valeurs = [s.value for s in sources if s is not None]
    if not valeurs:
        return None
    return FieldCondition(key="source", match=MatchAny(any=valeurs))


def parser_date(valeur: object) -> date:
    """Parse une valeur de date depuis un payload Qdrant.

    Accepte : str ISO 8601, datetime, date.

    Raises:
        ValueError: Si la valeur ne peut pas être parsée.
    """
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    if isinstance(valeur, str):
        return date.fromisoformat(valeur[:10])
    raise ValueError(f"Impossible de parser la date : {valeur!r}")  # noqa: TRY003 — message ponctuel, taxonomie d'erreurs dédiée à traiter en §8 skill


def point_vers_evidence(point: ScoredPoint) -> EvidenceRecuperee | None:
    """Convertit un ScoredPoint Qdrant en EvidenceRecuperee.

    Retourne None si le payload est incomplet, avec log d'avertissement.
    """
    payload = point.payload or {}

    champs_requis = [
        "chunk_id",
        "document_id",
        "article_id",
        "texte_chunk",
        "valid_from",
    ]
    for champ in champs_requis:
        if champ not in payload:
            logger.warning(
                "Chunk ignoré — champ manquant '%s' dans point.id=%s",
                champ,
                point.id,
            )
            return None

    try:
        valid_from = parser_date(payload["valid_from"])
        valid_to = parser_date(payload["valid_to"]) if payload.get("valid_to") else None

        return EvidenceRecuperee(
            chunk_id=str(payload["chunk_id"]),
            document_id=str(payload["document_id"]),
            article_id=str(payload["article_id"]),
            texte_extrait=str(payload["texte_chunk"]),
            score_similarite=round(float(point.score), 4),
            valid_from=valid_from,
            valid_to=valid_to,
        )

    except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
        logger.warning("Conversion échouée pour point.id=%s : %s", point.id, exc)
        return None
