"""src/orchestrator_pipeline.py — Étapes du pipeline Retrieval / Temporal / Explainer.

Extraites de src/orchestrator.py (§12 étape 6). Les trois étapes sont
livrées comme fonctions module-level et reçoivent l'Orchestrateur en
paramètre pour réutiliser `_executer_bloquant`, `_obtenir_retriever`
et `_machine_pour_agent` sans dupliquer leur logique de sérialisation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.models import EvidenceRecuperee, NiveauConfiance, SortieAgent

if TYPE_CHECKING:
    from datetime import date

    from src.models import SourceReglementaire
    from src.orchestrator import Orchestrateur

logger = logging.getLogger(__name__)


async def etape_retrieval(
    orchestrator: Orchestrateur,
    question: str,
    date_contexte: date | None,
    filtres_themes: list[str],
    filtres_sources: list[SourceReglementaire],
) -> tuple[list[EvidenceRecuperee], SortieAgent]:
    """Étape 1 : récupération des passages pertinents via Qdrant."""
    debut = datetime.now(UTC)
    retriever = orchestrator._obtenir_retriever()

    # Déporté en thread (embedding MLX + requête Qdrant, bloquant) —
    # ne touche pas le registre de modèles de génération, donc pas
    # besoin de _verrou_agents ici.
    evidences = await asyncio.to_thread(
        retriever.retrieve,
        question=question,
        date_contexte=date_contexte,
        filtres_themes=filtres_themes,
        filtres_sources=filtres_sources,
    )

    sortie = SortieAgent(
        nom_agent="Retriever",
        machine=orchestrator._machine_pour_agent("Retriever"),
        contenu={
            "chunks_recuperes": len(evidences),
            "date_contexte": date_contexte.isoformat() if date_contexte else None,
            "filtres_themes": filtres_themes,
            "filtres_sources": [s.value for s in filtres_sources],
        },
        duree_ms=int((datetime.now(UTC) - debut).total_seconds() * 1000),
    )
    return evidences, sortie


async def etape_temporal(
    orchestrator: Orchestrateur,
    question: str,
    date_contexte: date | None,
    evidences: list[EvidenceRecuperee],
) -> tuple[list[EvidenceRecuperee], SortieAgent]:
    """Étape 2 : analyse temporelle via AgentTemporel."""
    from src.agents.temporal import AgentTemporel

    # use_llm=True : sous architecture unique m4pro2 (24 Go), le budget
    # RAM tolère l'annotation LLM du raisonnement temporel.
    agent = AgentTemporel(use_llm=True)
    resultat = await orchestrator._executer_bloquant(
        agent.analyser,
        question=question,
        evidences=evidences,
        date_contexte=date_contexte,
    )

    contenu: dict[str, object] = {
        "date_ref": resultat.date_ref.isoformat(),
        "avant_filtrage": len(evidences),
        "apres_filtrage": len(resultat.evidences_applicables),
        "chevauchements": resultat.chevauchements,
        "lacunes": resultat.lacunes,
        "niveau_confiance": resultat.niveau_confiance.value,
    }
    if resultat.explication_llm:
        contenu["explication_llm"] = resultat.explication_llm

    sortie = SortieAgent(
        nom_agent="Temporal",
        machine=orchestrator._machine_pour_agent("Temporal"),
        contenu=contenu,
    )
    return resultat.evidences_applicables, sortie


async def etape_explainer(
    orchestrator: Orchestrateur,
    question: str,
    evidences: list[EvidenceRecuperee],
    type_pipeline: str,
    date_ref: date | None = None,
) -> tuple[str, NiveauConfiance, SortieAgent]:
    """Étape 3 : synthèse via AgentExplainer."""
    from src.agents.explainer import AgentExplainer

    agent = AgentExplainer(use_llm=True)
    resultat = await orchestrator._executer_bloquant(
        agent.expliquer,
        question=question,
        evidences=evidences,
        date_ref=date_ref,
        type_pipeline=type_pipeline,
    )

    sortie = SortieAgent(
        nom_agent="Explainer",
        machine=orchestrator._machine_pour_agent("Explainer"),
        contenu={
            "mode": resultat.mode,
            "evidences_utilisees": len(evidences),
            "sources_citees": len(resultat.sources_citees),
            "niveau_confiance": resultat.niveau_confiance.value,
        },
    )
    return resultat.reponse, resultat.niveau_confiance, sortie


async def etape_conflit(
    orchestrator: Orchestrateur,
    question: str,
    date_contexte: date | None,
    evidences: list[EvidenceRecuperee],
    request_id: object,
) -> SortieAgent | None:
    """Étape 2b : détection de conflit (activée quand type_pipeline == 'conflit').

    Ajoute la SortieAgent à retourner à l'orchestrateur (ou None si l'agent
    a échoué) et soumet une TacheValidation LIENS quand la détection élève
    le niveau à PROBABLE / CRITIQUE. Le paramètre `request_id` transite
    dans la tâche à des fins de traçabilité (rattachée à la requête d'origine).
    """
    from uuid import UUID

    from src.agents.conflit import AgentConflit
    from src.models import TacheValidation, TypeFilePendante

    request_uuid = request_id if isinstance(request_id, UUID) else UUID(str(request_id))

    try:
        # use_llm=True : sous architecture unique m4pro2 (24 Go), le budget
        # RAM tolère l'analyse LLM des conflits potentiels.
        agent_conflit = AgentConflit(use_llm=True)
        resultat_conflit = await orchestrator._executer_bloquant(
            agent_conflit.analyser,
            question=question,
            evidences=evidences,
            date_ref=date_contexte,
        )
        sortie = SortieAgent(
            nom_agent="Conflict",
            machine=orchestrator._machine_pour_agent("Conflict"),
            contenu={
                "niveau_global": resultat_conflit.niveau_global.value,
                "conflits_detectes": len(resultat_conflit.conflits),
                "mode": resultat_conflit.mode,
            },
        )
        if resultat_conflit.necessite_validation_humaine:
            tache_conflit = TacheValidation(
                type_file=TypeFilePendante.LIENS,
                request_id=request_uuid,
                contenu={
                    "question": question,
                    "conflits": [
                        {
                            "doc_a": c.evidence_a.document_id,
                            "art_a": c.evidence_a.article_id,
                            "doc_b": c.evidence_b.document_id,
                            "art_b": c.evidence_b.article_id,
                            "niveau": c.niveau.value,
                            "description": c.description,
                        }
                        for c in resultat_conflit.conflits
                    ],
                },
            )
            await orchestrator._enregistrer_tache_redis(tache_conflit)
            logger.warning(
                "Conflit %s soumis à validation humaine.",
                resultat_conflit.niveau_global.value,
            )
    except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
        logger.warning("Agent Conflict échoué, ignoré : %s", exc)
        return None
    return sortie


async def etape_citation(
    orchestrator: Orchestrateur,
    evidences: list[EvidenceRecuperee],
) -> SortieAgent | None:
    """Étape 4 : génération et vérification des citations."""
    from src.agents.citation import AgentCitation

    try:
        agent_citation = AgentCitation(use_llm=True)
        resultat_citation = await orchestrator._executer_bloquant(
            agent_citation.generate,
            evidences=evidences,
        )
        sortie = SortieAgent(
            nom_agent="Citation",
            machine=orchestrator._machine_pour_agent("Citation"),
            contenu={
                "mode": resultat_citation.mode,
                "verifiees": len(resultat_citation.citations_verifiees),
                "douteuses": len(resultat_citation.citations_douteuses),
            },
        )
        if resultat_citation.avertissement:
            logger.warning("Citation : %s", resultat_citation.avertissement)
    except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
        logger.warning("Agent Citation échoué, ignoré : %s", exc)
        return None
    return sortie
