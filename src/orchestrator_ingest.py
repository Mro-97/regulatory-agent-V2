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

from src.models import ReponseIngestion, RequeteIngestion

if TYPE_CHECKING:
    from scripts.ingest import Ingester

logger = logging.getLogger(__name__)


class DocumentDejaIndexeError(Exception):
    """Levée par ingerer_sync() quand un document_id est déjà présent
    dans Qdrant et que forcer_reindexation=False. Traduite en HTTP 409
    par l'API (voir src/api.py).
    """  # noqa: D205 — TODO §12 étape 4 : compléter docstrings


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
        raise ValueError(  # noqa: TRY003 — message ponctuel, taxonomie d'erreurs dédiée à traiter en §8 skill
            "contenu_json requis — l'ingestion depuis une URL n'est pas "
            "implémentée. Fournir le document au format DocumentReglementaire "
            "canonique (voir scripts/pdf_to_json.py)."
        )

    try:
        doc = DocumentReglementaire(**requete.contenu_json)
    except Exception as exc:
        raise ValueError(f"contenu_json invalide : {exc}") from exc  # noqa: TRY003 — message ponctuel, taxonomie d'erreurs dédiée à traiter en §8 skill

    if not doc.hash_document:
        doc.hash_document = doc.calculer_hash()

    ingester = ingester_factory()
    nb_existants = ingester.compter_chunks_existants(doc.id)

    if nb_existants > 0 and not requete.forcer_reindexation:
        raise DocumentDejaIndexeError(  # noqa: TRY003 — message ponctuel, taxonomie d'erreurs dédiée à traiter en §8 skill
            f"Document '{doc.id}' déjà indexé ({nb_existants} chunks) — "
            f"renvoyer avec forcer_reindexation=true pour le remplacer."
        )

    if nb_existants > 0:
        ingester.supprimer_chunks_document(doc.id)

    nb_chunks = ingester.ingest_document(doc)

    return ReponseIngestion(
        document_id=doc.id,
        chunks_indexes=nb_chunks,
        hash_document=doc.hash_document,
        nouvelle_version=nb_existants > 0,
    )
