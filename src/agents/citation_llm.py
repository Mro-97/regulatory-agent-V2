"""src/agents/citation_llm.py — Extraction LLM des chunks cités.

Extraite de src/agents/citation.py (§12 étape 6). Utilise Mistral 7B pour
identifier quels chunk_id des preuves ont été utilisés dans la réponse
de l'Explainer. La vérification déterministe reste dans citation.py et
ne dépend pas de ce module.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from config import cfg
from src.agents.citation import CitationReglementaire, _normaliser_pour_comparaison
from src.prompts_loader import charger_prompt

if TYPE_CHECKING:
    from src.mlx_utils import MLXInference
    from src.models import EvidenceRecuperee

logger = logging.getLogger(__name__)

# Longueur minimale (caractères normalisés) d'une phrase de la réponse pour
# qu'elle vaille comme ancrage : écarte « Oui. » ou « 1) » qui matcheraient
# n'importe quel chunk par hasard.
_LONGUEUR_MIN_ANCRAGE = 20

# Découpe la réponse de l'Explainer en phrases : ponctuation forte, mais
# aussi `:` / `;` (le prompt v2 impose « 1) Réponse directe : … ») et saut de
# ligne — base de la recherche d'ancrage par phrase.
_RE_PHRASES = re.compile(r"(?<=[.!?:;])\s+|\n+")


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
    """Identifie via Mistral 7B les chunks cités dans la réponse Explainer.

    Retourne les CitationReglementaire (statut NON_VERIFIEE, vérifiées
    ensuite via `AgentCitation.verify()`), [] si le LLM répond AUCUN,
    ou None en cas d'échec — l'appelant retombe alors sur le déterministe
    (échec d'appel LLM ou format de sortie inattendu, cf. L6).
    """
    messages = _preparer_messages_citation(reponse_explainer, evidences)
    try:
        texte = _appeler_llm_citation(modele, messages)
    except Exception:
        logger.exception("Extraction LLM échouée, bascule déterministe")
        return None
    preuves_citees = _parser_citations_llm(texte, evidences)
    if preuves_citees is None:
        return None
    return [
        _citation_depuis_evidence_llm(ev, reponse_explainer) for ev in preuves_citees
    ]


def _preparer_messages_citation(
    reponse_explainer: str, evidences: list[EvidenceRecuperee]
) -> list[dict[str, str]]:
    """Formate le contexte des 10 premiers chunks puis rend le gabarit LLM."""
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


def _parser_citations_llm(
    texte: str, evidences: list[EvidenceRecuperee]
) -> list[EvidenceRecuperee] | None:
    """Liste de chunk_id → preuves correspondantes, None si format inattendu.

    L6 : la sortie attendue est un CSV ``chunk_id`` séparé par ``,``, `;` ou
    un saut de ligne. Si aucun token ne correspond à un `chunk_id` connu ET
    que la réponse n'est pas explicitement « AUCUN », on retourne None pour
    déclencher le repli déterministe chez l'appelant (au lieu de renvoyer
    silencieusement `[]`).
    """
    normalise = texte.strip().upper()
    if not normalise or normalise.startswith("AUCUN"):
        logger.info("LLM : aucun chunk identifié comme cité.")
        return []
    index_chunks = {ev.chunk_id: ev for ev in evidences}
    retenues = _retenir_chunks_cites(texte, index_chunks)
    if not retenues:
        logger.warning(
            "Sortie Citation LLM sans chunk_id reconnu (ni AUCUN) : %r — "
            "repli déterministe.",
            texte[:120],
        )
        return None
    return retenues


def _retenir_chunks_cites(
    texte: str, index_chunks: dict[str, EvidenceRecuperee]
) -> list[EvidenceRecuperee]:
    """Preuves des `chunk_id` reconnus, sans doublon et dans l'ordre du texte."""
    retenues: list[EvidenceRecuperee] = []
    vus: set[str] = set()
    for token in (c.strip() for c in re.split(r"[,\n;]+", texte)):
        if not token or token in vus:
            continue
        ev = index_chunks.get(token)
        if ev is None:
            logger.warning(
                "LLM a proposé un chunk_id inexistant : '%s' — ignoré.",
                token,
            )
            continue
        vus.add(token)
        retenues.append(ev)
    return retenues


def _citation_depuis_evidence_llm(
    ev: EvidenceRecuperee, reponse_explainer: str
) -> CitationReglementaire:
    """Fabrique une CitationReglementaire ancrée sur la RÉPONSE de l'Explainer.

    M7 : l'extrait était recopié du chunk vérifié (`ev.texte_extrait[:200]`),
    ce qui rendait la vérification `extrait in chunk` tautologique — un
    `chunk_id` proposé au hasard passait pour vérifié. L'extrait est
    désormais une phrase de la réponse réellement présente dans le chunk ;
    à défaut, il reste un fragment de la réponse, absent du chunk, et
    `AgentCitation.verify()` classe alors la citation en DOUTEUSE.
    """
    extrait = _extraire_phrase_ancre(reponse_explainer, ev.texte_extrait)
    if extrait is None:
        extrait = (reponse_explainer or "").strip()[:200]
    return CitationReglementaire(
        document_id=ev.document_id,
        article_id=ev.article_id,
        valid_from=ev.valid_from,
        valid_to=ev.valid_to,
        extrait=extrait,
        chunk_id=ev.chunk_id,
    )


def _extraire_phrase_ancre(reponse_explainer: str, chunk: str) -> str | None:
    """Phrase (≤ 200 car.) de la réponse effectivement présente dans `chunk`.

    Retourne la PLUS LONGUE phrase ancrée (la plus significative), ou None
    si aucune phrase d'au moins `_LONGUEUR_MIN_ANCRAGE` caractères n'est
    retrouvée. La comparaison réutilise `_normaliser_pour_comparaison` —
    exactement ce que vérifiera `AgentCitation.verify()`.
    """
    chunk_norm = _normaliser_pour_comparaison(chunk)
    meilleure: str | None = None
    longueur_meilleure = 0
    for phrase in _RE_PHRASES.split(reponse_explainer or ""):
        candidate = phrase.strip()[:200]
        candidate_norm = _normaliser_pour_comparaison(candidate)
        if len(candidate_norm) < _LONGUEUR_MIN_ANCRAGE:
            continue
        if candidate_norm in chunk_norm and len(candidate_norm) > longueur_meilleure:
            meilleure = candidate
            longueur_meilleure = len(candidate_norm)
    return meilleure
