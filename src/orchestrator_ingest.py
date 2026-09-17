"""src/orchestrator_ingest.py — Ingestion synchrone d'un document.

Extraite de src/orchestrator.py (§12 étape 6). Validation du
contenu_json, vérification d'existence, chunking, embedding MLX et
upsert Qdrant. L'`Orchestrateur` déporte l'appel en thread via
`asyncio.to_thread`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.errors import (
    DocumentAlreadyIndexedError,
    InvalidDocumentError,
    MissingMetadataError,
)
from src.models import DocumentReglementaire
from src.schemas import ReponseIngestion, RequeteIngestion

if TYPE_CHECKING:
    from scripts.ingest import Ingester

logger = logging.getLogger(__name__)

# Alias descendant : le nom historique est encore ré-exporté depuis
# src.orchestrator (importé par api.py, tests). La classe unique vit
# désormais dans src.errors (§12 étape 8).
DocumentDejaIndexeError = DocumentAlreadyIndexedError


def _valider_et_construire_document(requete: RequeteIngestion) -> Any:
    """Valide `contenu_json` puis construit un DocumentReglementaire (hash injecté)."""
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
    return doc


def _resoudre_conflit_reindexation(
    ingester: Ingester,
    doc: Any,
    requete: RequeteIngestion,
) -> int:
    """Vérifie l'existence du document et purge si `forcer_reindexation=True`.

    M11 : les chunks de la nouvelle version sont construits (chunker +
    sanitizer) AVANT toute purge. `Ingester.ingest_document` purge d'abord
    puis chunk : si le découpage ne produit aucun chunk survivant (filtre
    `ingest_taille_min_chunk`, sanitizer en mode `bloquer`), le document
    disparaissait du corpus et l'API répondait 200 `chunks_indexes=0`. On
    refuse donc la réindexation dans ce cas, en conservant l'ancienne
    version. (Le correctif équivalent dans `scripts/ingest.py`
    `ingest_document` est hors de ce module.)

    Raises:
        DocumentAlreadyIndexedError: document déjà indexé sans
            `forcer_reindexation`.
        InvalidDocumentError: `forcer_reindexation` demandé mais le nouveau
            découpage ne produit aucun chunk (rien n'a été purgé).
    """
    nb_existants = ingester.compter_chunks_existants(doc.id)
    if nb_existants > 0 and not requete.forcer_reindexation:
        raise DocumentAlreadyIndexedError(doc.id, nb_existants)
    if nb_existants > 0 and not _produit_des_chunks(ingester, doc):
        raise InvalidDocumentError(
            reason=(
                "réindexation refusée : le nouveau découpage ne produit aucun "
                "chunk (seuil ingest_taille_min_chunk ou sanitizer) — "
                "l'ancienne version reste indexée"
            ),
            document_id=doc.id,
        )
    if nb_existants > 0:
        ingester.supprimer_chunks_document(doc.id)
    return nb_existants


def _produit_des_chunks(ingester: Ingester, doc: Any) -> bool:
    """True si chunker + sanitizer produisent au moins un chunk pour `doc`.

    Pré-vol de `_resoudre_conflit_reindexation`. Le sanitizer privé de
    l'`Ingester` est appelé faute d'API publique « préparer les chunks » et
    parce que `scripts/ingest.py` est hors périmètre de ce correctif.
    """
    chunks = ingester.chunk_document(doc)
    return bool(ingester._appliquer_sanitizer(chunks))


def ingerer_sync(
    ingester_factory: Callable[[], Ingester],
    requete: RequeteIngestion,
) -> ReponseIngestion:
    """Ingestion synchrone : validation → check doublon → chunking → embedding."""
    doc = _valider_et_construire_document(requete)
    ingester = ingester_factory()
    nb_existants = _resoudre_conflit_reindexation(ingester, doc, requete)
    nb_chunks = ingester.ingest_document(doc)
    return ReponseIngestion(
        document_id=doc.id,
        chunks_indexes=nb_chunks,
        hash_document=doc.hash_document,
        nouvelle_version=nb_existants > 0,
    )
