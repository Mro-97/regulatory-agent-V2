"""src/agents/temporal_llm.py — Annotation LLM optionnelle pour l'Agent Temporel.

Extraite de src/agents/temporal.py (§12 étape 6). Regroupe la logique de
chargement paresseux du modèle Qwen 2.5 7B et la construction du prompt
qui produit l'explication temporelle en langage naturel.

Principe fondamental (skill temporal-reasoning) : le LLM n'est jamais
autorité sur la validité temporelle. Il ne modifie pas le résultat du
filtre déterministe — il l'annote.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import cfg

if TYPE_CHECKING:
    from datetime import date

    from src.agents.temporal import EvidenceTemporelle
    from src.mlx_utils import MLXInference
    from src.models import EvidenceRecuperee

logger = logging.getLogger(__name__)


def charger_modele_temporel(modele: MLXInference | None) -> MLXInference:
    """Charge Qwen 2.5 7B via le registre MLX (lazy)."""
    if modele is None:
        from src.mlx_utils import get_model

        modele = get_model(
            model_name=cfg.modele_temporal,
            temperature=0.0,  # déterministe pour le raisonnement temporel
        )
        logger.info("Modèle temporel chargé : %s", cfg.modele_temporal)
    return modele


def annoter_avec_llm(
    modele: MLXInference,
    question: str,
    date_ref: date,
    applicables: list[EvidenceRecuperee],
    exclues: list[EvidenceTemporelle],
    chevauchements: list[str],
    lacunes: list[str],
) -> str:
    """Qwen 2.5 7B produit une explication temporelle (métadonnées uniquement)."""
    messages = _preparer_messages_temporal(
        question,
        date_ref,
        applicables,
        exclues,
        chevauchements,
        lacunes,
    )
    try:
        resultat = modele.generate_avec_messages(messages=messages, max_tokens=256)
        return resultat.texte.strip()
    except Exception:
        logger.exception("Annotation LLM échouée")
        return (
            f"Analyse temporelle déterministe — "
            f"{len(applicables)} version(s) applicable(s) à {date_ref}."
        )


def _preparer_messages_temporal(
    question: str,
    date_ref: date,
    applicables: list[EvidenceRecuperee],
    exclues: list[EvidenceTemporelle],
    chevauchements: list[str],
    lacunes: list[str],
) -> list[dict[str, str]]:
    """Formatte les 3 contextes (applicables/exclues/anomalies) puis rend le gabarit."""
    from src.prompts_loader import charger_prompt

    ctx_applicables = _formatter_applicables(applicables)
    ctx_exclues = _formatter_exclues(exclues)
    ctx_anomalies = _formatter_anomalies(chevauchements, lacunes)
    return charger_prompt("temporal/annoter", 1).rendre(
        question=question,
        date_ref=date_ref,
        nb_applicables=len(applicables),
        ctx_applicables=ctx_applicables or "Aucune",
        nb_exclues=len(exclues),
        ctx_exclues=ctx_exclues or "Aucune",
        ctx_anomalies=ctx_anomalies,
    )


def _formatter_applicables(applicables: list[EvidenceRecuperee]) -> str:
    """Liste puces `- doc/art : valide du <from> au <to>` (max 10)."""
    return "\n".join(
        f"- {e.document_id}/{e.article_id} : "
        f"valide du {e.valid_from} au {e.valid_to or 'indéfiniment'}"
        for e in applicables[:10]
    )


def _formatter_exclues(exclues: list[EvidenceTemporelle]) -> str:
    """Liste puces `- doc/art : raison` pour les 5 premières exclusions."""
    return "\n".join(
        f"- {et.evidence.document_id}/{et.evidence.article_id} : {et.raison_exclusion}"
        for et in exclues[:5]
    )


def _formatter_anomalies(chevauchements: list[str], lacunes: list[str]) -> str:
    """Bloc `Anomalies détectées : ...` ou chaîne vide si aucune."""
    if not chevauchements and not lacunes:
        return ""
    return "\nAnomalies détectées :\n" + "\n".join(chevauchements + lacunes)
