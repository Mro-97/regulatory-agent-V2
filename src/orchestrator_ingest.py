"""src/orchestrator_ingest.py — Ingestion synchrone d'un document.

Extraite de src/orchestrator.py (§12 étape 6). Validation du
contenu_json, vérification d'existence, chunking, embedding MLX et
upsert Qdrant. L'`Orchestrateur` déporte l'appel en thread via
`asyncio.to_thread`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from src.errors import (
    DocumentAlreadyIndexedError,
    InvalidDocumentError,
    MissingMetadataError,
)
from src.models import ReponseIngestion, RequeteIngestion

if TYPE_CHECKING:
    from scripts.ingest import Ingester

logger = logging.getLogger(__name__)

# Alias descendant : le nom historique est encore ré-exporté depuis
# src.orchestrator (importé par api.py, tests). La classe unique vit
# désormais dans src.errors (§12 étape 8).
DocumentDejaIndexeError = DocumentAlreadyIndexedError


def ingerer_sync(
    ingester_factory: Callable[[], Ingester],
    requete: RequeteIngestion,
) -> ReponseIngestion:
    """Ingestion synchrone d'un document réglementaire.

    Validation → vérification d'existence → chunking → embedding → upsert.
    `ingester_factory` fournit l'Ingester lazily : la validation du
    `contenu_json` doit s'exécuter avant toute instanciation Qdrant, sinon
    un test qui vérifie le refus de contenu_json vide ne peut plus tourner
    hors environnement Qdrant/MLX.
    """
    from src.models import DocumentReglementaire

    if not requete.contenu_json:
        raise MissingMetadataError(
            field="contenu_json",
            detail=(
                "l'ingestion depuis une URL n'est pas implémentée ; fournir "
                "le document au format DocumentReglementaire canonique "
                "(voir scripts/pdf_to_json.py)."
            ),
        )

    try:
        doc = DocumentReglementaire(**requete.contenu_json)
    except Exception as exc:
        raise InvalidDocumentError(reason=str(exc)) from exc

    if not doc.hash_document:
        doc.hash_document = doc.calculer_hash()

    ingester = ingester_factory()
    nb_existants = ingester.compter_chunks_existants(doc.id)

    if nb_existants > 0 and not requete.forcer_reindexation:
        raise DocumentAlreadyIndexedError(doc.id, nb_existants)

    if nb_existants > 0:
        ingester.supprimer_chunks_document(doc.id)

    nb_chunks = ingester.ingest_document(doc)

    return ReponseIngestion(
        document_id=doc.id,
        chunks_indexes=nb_chunks,
        hash_document=doc.hash_document,
        nouvelle_version=nb_existants > 0,
    )
