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
    """Utilise Qwen 2.5 7B pour produire une explication temporelle
    en langage naturel.

    Le LLM reçoit uniquement les métadonnées temporelles (pas le texte
    complet des articles) pour limiter la taille du contexte.

    Args:
        modele:         Instance MLXInference déjà chargée.
        question:       Question originale de l'utilisateur.
        date_ref:       Date de référence.
        applicables:    Preuves retenues.
        exclues:        Preuves écartées avec raison.
        chevauchements: Anomalies détectées.
        lacunes:        Lacunes détectées.

    Returns:
        Explication en langage naturel (str).
    """  # noqa: D205
    from src.prompts_loader import charger_prompt

    # Construction du contexte temporel (sans texte complet)
    ctx_applicables = "\n".join(
        f"- {e.document_id}/{e.article_id} : "
        f"valide du {e.valid_from} au {e.valid_to or 'indéfiniment'}"
        for e in applicables[:10]
    )
    ctx_exclues = "\n".join(
        f"- {et.evidence.document_id}/{et.evidence.article_id} : {et.raison_exclusion}"
        for et in exclues[:5]
    )
    ctx_anomalies = ""
    if chevauchements or lacunes:
        ctx_anomalies = "\nAnomalies détectées :\n" + "\n".join(
            chevauchements + lacunes
        )

    prompt_messages = charger_prompt("temporal/annoter", 1).rendre(
        question=question,
        date_ref=date_ref,
        nb_applicables=len(applicables),
        ctx_applicables=ctx_applicables or "Aucune",
        nb_exclues=len(exclues),
        ctx_exclues=ctx_exclues or "Aucune",
        ctx_anomalies=ctx_anomalies,
    )

    try:
        resultat = modele.generate_avec_messages(
            messages=prompt_messages,
            max_tokens=256,
        )
        return resultat.texte.strip()
    except Exception:
        logger.exception("Annotation LLM échouée")
        return f"Analyse temporelle déterministe — {len(applicables)} version(s) applicable(s) à {date_ref}."  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
