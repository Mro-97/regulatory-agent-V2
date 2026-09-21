"""src/orchestrator_audit.py — Journalisation et construction d'audit.

Extraits de `src/orchestrator.py` (1096 lignes). Ces fonctions ne dépendent
d'aucun état de l'`Orchestrateur` : elles prennent leurs arguments et rendent
un résultat, ce qui les rend testables seules et allège le module qui porte le
pipeline.

Le module reste feuille vis-à-vis de l'orchestrateur : il n'importe que
`config`, `src.models` et la bibliothèque standard. C'est ce qui permet à
`src/orchestrator.py` de l'importer sans créer de cycle.
"""

from __future__ import annotations

import logging
import platform
from uuid import UUID

from config import cfg
from src.models import (
    EnregistrementAudit,
    EvidenceRecuperee,
    NiveauConfiance,
    SortieAgent,
)
from src.schemas import ReponseIngestion, RequeteIngestion, RequeteQuestion

logger = logging.getLogger(__name__)

# Architecture unique m4pro2 : un seul hôte exécute tous les agents.
# `SortieAgent.machine` reste utile pour l'audit (traçabilité multi-hôte
# éventuelle en cas d'évolution) mais retourne le nom réel de la machine
# d'exécution, plus une étiquette « Mac_A/B/C » figée qui renvoyait à
# l'ancienne architecture 3-machines abandonnée.
MACHINE_INCONNUE = "inconnue"
MACHINE = platform.node() or MACHINE_INCONNUE


def resoudre_mode(mode: str | None) -> str:
    """Retourne le mode effectif en repliant les valeurs inconnues sur 'real'."""
    effectif = mode or cfg.orchestrateur_mode
    if effectif not in ("real", "mock"):
        logger.warning("Mode inconnu '%s', bascule sur 'real'.", effectif)
        return "real"
    return effectif


def journaliser_debut_traitement(
    request_id: UUID,
    mode: str,
    type_pipeline: str,
    requete: RequeteQuestion,
) -> None:
    """Trace le début d'un `traiter()` avec identifiants et question tronquée."""
    logger.info(
        "Traitement request_id=%s mode=%s type=%s question=%r",
        request_id,
        mode,
        type_pipeline,
        requete.question[:80],
    )


def construire_audit_mock(
    requete: RequeteQuestion,
    request_id: UUID,
    reponse: str,
) -> EnregistrementAudit:
    """Construit un EnregistrementAudit pour le mode mock (hash SHA-256 injecté)."""
    audit = EnregistrementAudit(
        request_id=request_id,
        user_query=requete.question,
        date_contexte=requete.date_contexte,
        reponse_finale=reponse,
        niveau_confiance=NiveauConfiance.INCERTAIN,
    )
    audit.hash_courant = audit.calculer_hash()
    return audit


def reponse_ingestion_mock(requete: RequeteIngestion) -> ReponseIngestion:
    """Réponse-fake retournée en mode mock (aucune action Qdrant)."""
    document_id = (requete.contenu_json or {}).get("id", "mock")
    return ReponseIngestion(
        document_id=str(document_id),
        chunks_indexes=0,
        hash_document="",
        nouvelle_version=False,
    )


def journaliser_audit_succes(audit: EnregistrementAudit, hash_courant: str) -> None:
    """Trace un audit persisté avec succès (hash tronqué, agents, confiance)."""
    logger.info(
        "AUDIT request_id=%s hash=%s agents=%s confiance=%s",
        audit.request_id,
        hash_courant[:16],
        [a.nom_agent for a in audit.agents_executes],
        audit.niveau_confiance.value,
    )


def journaliser_audit_fallback(audit: EnregistrementAudit) -> None:
    """Trace un audit qui n'a pas pu être persisté (log only, non bloquant)."""
    logger.info(
        "AUDIT (log only) request_id=%s agents=%s confiance=%s",
        audit.request_id,
        [a.nom_agent for a in audit.agents_executes],
        audit.niveau_confiance.value,
    )


def construire_audit_reel(
    requete: RequeteQuestion,
    request_id: UUID,
    evidences: list[EvidenceRecuperee],
    agents_executes: list[SortieAgent],
    reponse_texte: str,
    niveau_confiance: NiveauConfiance,
    soumettre_validation: bool,
) -> EnregistrementAudit:
    """Construit l'EnregistrementAudit final du pipeline réel (hash injecté).

    `soumettre_validation` reflète la soumission EFFECTIVE (H1) : False si la
    tâche de validation Redis n'a pas pu être enregistrée (l'échec est
    journalisé en ERROR par `_soumettre_validation_si_besoin`).
    """
    audit = EnregistrementAudit(
        request_id=request_id,
        user_query=requete.question,
        date_contexte=requete.date_contexte,
        documents_recuperes=list({e.document_id for e in evidences}),
        evidences=evidences,
        agents_executes=agents_executes,
        reponse_finale=reponse_texte,
        niveau_confiance=niveau_confiance,
        necessite_validation_humaine=soumettre_validation,
    )
    audit.hash_courant = audit.calculer_hash()
    return audit
