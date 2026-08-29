"""src/schemas.py — Schémas d'API (requêtes / réponses HTTP).

Extraits de src/models.py (§12 étape 6). Ces modèles vivent séparément
des entités de domaine parce qu'ils servent uniquement le contrat HTTP :
un changement d'API ne doit pas provoquer de migration côté persistance.

Ré-exportés depuis `src.models` pour compatibilité descendante.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from uuid import UUID

from config import cfg
from pydantic import BaseModel, Field, model_validator

from src.models import (
    TAILLE_MAX_CONTENU_JSON,
    EvidenceRecuperee,
    NiveauConfiance,
    SourceReglementaire,
    StatutValidation,
    TacheValidation,
)


class RequeteQuestion(BaseModel):
    """Corps de la requête POST /ask."""

    question: str = Field(
        ...,
        min_length=3,
        max_length=cfg.question_max_length,
        description="Question réglementaire.",
    )
    date_contexte: date | None = Field(
        default=None, description="Date de contexte réglementaire."
    )
    filtres_themes: list[str] = Field(default_factory=list)
    filtres_sources: list[SourceReglementaire] = Field(default_factory=list)
    demander_validation_humaine: bool = Field(default=False)


class ReponseQuestion(BaseModel):
    """Corps de la réponse POST /ask."""

    request_id: UUID
    reponse: str
    evidences: list[EvidenceRecuperee] = Field(default_factory=list)
    niveau_confiance: NiveauConfiance
    en_attente_validation: bool = Field(default=False)
    tache_validation_id: UUID | None = Field(default=None)


class RequeteIngestion(BaseModel):
    """Corps de la requête POST /ingest."""

    source: SourceReglementaire
    url: str | None = Field(default=None, max_length=2048)
    contenu_json: dict[str, Any] | None = Field(default=None)
    forcer_reindexation: bool = Field(default=False)

    @model_validator(mode="after")
    def verifier_taille_contenu(self) -> RequeteIngestion:
        """Refuse un contenu JSON trop volumineux (anti-DoS)."""
        if self.contenu_json is not None:
            taille = len(json.dumps(self.contenu_json, ensure_ascii=False))
            if taille > TAILLE_MAX_CONTENU_JSON:
                from src.errors import PayloadTooLargeError

                raise PayloadTooLargeError(taille, TAILLE_MAX_CONTENU_JSON)
        return self


class ReponseIngestion(BaseModel):
    """Corps de la réponse POST /ingest."""

    document_id: str
    chunks_indexes: int
    hash_document: str
    nouvelle_version: bool


class ReponseTachesPendantes(BaseModel):
    """Corps de la réponse GET /pending."""

    total: int
    par_file: dict[str, int] = Field(default_factory=dict)
    taches: list[TacheValidation] = Field(default_factory=list)


class RequeteDecisionValidation(BaseModel):
    """Corps des requêtes POST /approve et POST /reject."""

    tache_id: UUID
    commentaire: str | None = Field(default=None, max_length=2000)


class ReponseDecisionValidation(BaseModel):
    """Corps de la réponse /approve et /reject."""

    tache_id: UUID
    nouveau_statut: StatutValidation
    horodatage_traitement: datetime
