"""src/agents/citation_llm.py — Extraction LLM des chunks cités.

Extraite de src/agents/citation.py (§12 étape 6). Utilise Mistral 7B pour
identifier quels chunk_id des preuves ont été utilisés dans la réponse
de l'Explainer. La vérification déterministe reste dans citation.py et
ne dépend pas de ce module.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import cfg
from src.agents.citation import CitationReglementaire

if TYPE_CHECKING:
    from src.mlx_utils import MLXInference
    from src.models import EvidenceRecuperee

logger = logging.getLogger(__name__)


def charger_modele_citation(modele: MLXInference | None) -> MLXInference:
    """Charge Mistral 7B via le registre MLX (lazy)."""
    if modele is None:
        from src.mlx_utils import get_model

        modele = get_model(
            model_name=cfg.modele_citation,
            temperature=0.0,
        )
        logger.info("Modèle Citation chargé : %s", cfg.modele_citation)
    return modele


def extraire_avec_llm(
    modele: MLXInference,
    reponse_explainer: str,
    evidences: list[EvidenceRecuperee],
) -> list[CitationReglementaire] | None:
    """Utilise Mistral 7B pour identifier quels passages des preuves
    ont été utilisés dans la réponse de l'Explainer.

    Le LLM reçoit la réponse et les textes des preuves, et retourne
    les chunk_id des preuves effectivement citées. La vérification
    déterministe s'applique ensuite via `AgentCitation.verify()`.

    Args:
        modele:            Instance MLXInference déjà chargée.
        reponse_explainer: Texte généré par l'Explainer.
        evidences:         Preuves disponibles.

    Returns:
        Liste de citations identifiées (statut NON_VERIFIEE avant verify()),
        liste vide si le LLM répond AUCUN, ou None en cas d'échec — dans ce
        dernier cas l'appelant retombe sur la génération déterministe.
    """  # noqa: D205
    messages = _preparer_messages_citation(reponse_explainer, evidences)
    try:
        texte = _appeler_llm_citation(modele, messages)
    except Exception:
        logger.exception("Extraction LLM échouée, bascule déterministe")
        return None
    if not texte or texte.upper() == "AUCUN":
        logger.info("LLM : aucun chunk identifié comme cité.")
        return []
    return _resoudre_chunk_ids_en_citations(texte, evidences)


def _preparer_messages_citation(
    reponse_explainer: str, evidences: list[EvidenceRecuperee]
) -> list[dict[str, str]]:
    """Formate le contexte des 10 premiers chunks puis rend le gabarit LLM."""
    from src.prompts_loader import charger_prompt

    contexte_preuves = "\n\n".join(
        f"CHUNK_ID: {ev.chunk_id}\n"
        f"SOURCE: {ev.document_id}/{ev.article_id}\n"
        f"TEXTE: {ev.texte_extrait[:300]}"
        for ev in evidences[:10]
    )
    return charger_prompt("citation/extraire", 1).rendre(
        reponse_explainer=reponse_explainer[:1000],
        contexte_preuves=contexte_preuves,
    )


def _appeler_llm_citation(modele: MLXInference, messages: list[dict[str, str]]) -> str:
    """Appelle le LLM (max_tokens=128) et retourne le texte stripped."""
    resultat = modele.generate_avec_messages(messages=messages, max_tokens=128)
    return resultat.texte.strip()


def _resoudre_chunk_ids_en_citations(
    texte: str, evidences: list[EvidenceRecuperee]
) -> list[CitationReglementaire]:
    """Convertit CSV chunk_id → citations (chunks inconnus loggés puis droppés)."""
    chunk_ids_bruts = [c.strip() for c in texte.split(",")]
    index_chunks = {ev.chunk_id: ev for ev in evidences}
    citations: list[CitationReglementaire] = []
    for chunk_id in chunk_ids_bruts:
        ev = index_chunks.get(chunk_id)
        if ev is None:
            logger.warning(
                "LLM a proposé un chunk_id inexistant : '%s' — ignoré.",
                chunk_id,
            )
            continue
        citations.append(
            CitationReglementaire(
                document_id=ev.document_id,
                article_id=ev.article_id,
                valid_from=ev.valid_from,
                valid_to=ev.valid_to,
                extrait=ev.texte_extrait[:200],
                chunk_id=ev.chunk_id,
            )
        )
    return citations
