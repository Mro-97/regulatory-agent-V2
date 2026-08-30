"""src/agents/conflit_llm.py — Analyse LLM des conflits potentiels.

Extraite de src/agents/conflit.py (§12 étape 6). DeepSeek-R1 14B annote
les conflits potentiels détectés déterministiquement, sans jamais les
créer : la liste d'entrée provient toujours de l'heuristique lexicale.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from config import cfg
from src.agents.conflit import ConflitDetecte, NiveauConflit

if TYPE_CHECKING:
    from src.mlx_utils import MLXInference

logger = logging.getLogger(__name__)


def _normaliser_verdict(verdict: str) -> str:
    """Normalise un verdict LLM : majuscules, sans accents ni ponctuation."""
    valeur = verdict.strip().upper()
    remplacements = {"É": "E", "È": "E", "Ê": "E", "Ë": "E", "À": "A", "Â": "A"}
    for ancien, nouveau in remplacements.items():
        valeur = valeur.replace(ancien, nouveau)
    return valeur.strip(" .,:;!?\"'")


def charger_modele_conflit(modele: MLXInference | None) -> MLXInference:
    """Charge DeepSeek-R1 14B via le registre MLX.
    ATTENTION : ~9 Go de RAM — décharger les autres modèles avant.
    """  # noqa: D205
    if modele is None:
        from src.mlx_utils import get_model

        logger.warning(
            "Chargement de DeepSeek-R1 14B (~9 Go) — "
            "les autres modèles seront déchargés."
        )
        modele = get_model(
            model_name=cfg.modele_conflit,
            temperature=0.0,  # raisonnement déterministe
        )
        logger.info("Modèle Conflit chargé : %s", cfg.modele_conflit)
    return modele


def extraire_verdicts(analyse: str) -> dict[int, str] | None:
    """Extrait le mapping {numero_conflit → verdict_normalisé} depuis la sortie LLM.

    Retourne None si la sortie n'est pas parsable.
    """
    match = re.search(r"\{[^{}]*\"verdicts\".*?\}\s*\]?\s*\}", analyse, re.DOTALL)
    candidats = [analyse] if match is None else [match.group(0), analyse]

    for candidat in candidats:
        try:
            donnees = json.loads(candidat)
        except Exception:  # noqa: BLE001, S112
            continue

        liste = donnees.get("verdicts") if isinstance(donnees, dict) else None
        if not isinstance(liste, list):
            continue

        mapping: dict[int, str] = {}
        for entree in liste:
            if not isinstance(entree, dict):
                continue
            num = entree.get("conflit")
            verdict = entree.get("verdict")
            if not isinstance(num, int) or not isinstance(verdict, str):
                continue
            mapping[num] = _normaliser_verdict(verdict)

        if mapping:
            return mapping

    return None


def verdict_vers_niveau(
    verdict: str | None, niveau_initial: NiveauConflit
) -> NiveauConflit:
    """Applique le verdict LLM au niveau d'un conflit.

    - CONFIRMÉ   → PROBABLE
    - APPARENT   → niveau initial (POTENTIEL) conservé
    - INEXISTANT → AUCUN (le conflit sera retiré)
    - autre / None → niveau initial conservé (parsing partiel)
    """
    if verdict == "CONFIRME":
        return NiveauConflit.PROBABLE
    if verdict == "INEXISTANT":
        return NiveauConflit.AUCUN
    return niveau_initial


def analyser_avec_llm(
    modele: MLXInference,
    question: str,
    conflits: list[ConflitDetecte],
) -> tuple[list[ConflitDetecte], str]:
    """DeepSeek-R1 14B annote 5 conflits max et renvoie un JSON structuré."""
    conflits_analyses = conflits[:5]
    messages = _preparer_messages_conflit(question, conflits_analyses)
    analyse = _appeler_llm_conflit(modele, messages, len(conflits))
    if analyse is None:
        return conflits, (
            f"Analyse automatique indisponible. "
            f"{len(conflits)} tension(s) détectée(s) manuellement."
        )
    verdicts = extraire_verdicts(analyse)
    if verdicts is None:
        _journaliser_parsing_echoue(analyse)
        return conflits, analyse
    conflits_retenus = _appliquer_verdicts(conflits_analyses, verdicts)
    conflits_retenus.extend(conflits[len(conflits_analyses) :])
    return conflits_retenus, analyse


def _preparer_messages_conflit(
    question: str, conflits_analyses: list[ConflitDetecte]
) -> list[dict[str, str]]:
    """Construit le contexte formatté puis rend le gabarit `conflit/analyser` v1."""
    from src.prompts_loader import charger_prompt

    contexte = "\n\n".join(
        f"CONFLIT {i + 1} :\n"
        f"Source A : {c.evidence_a.document_id}/{c.evidence_a.article_id}\n"
        f"Texte A : {c.evidence_a.texte_extrait[:400]}\n"
        f"Source B : {c.evidence_b.document_id}/{c.evidence_b.article_id}\n"
        f"Texte B : {c.evidence_b.texte_extrait[:400]}\n"
        f"Tension détectée : {c.description}"
        for i, c in enumerate(conflits_analyses)
    )
    return charger_prompt("conflit/analyser", 1).rendre(
        question=question,
        nb_conflits=len(conflits_analyses),
        contexte=contexte,
    )


def _appeler_llm_conflit(
    modele: MLXInference, messages: list[dict[str, str]], nb_conflits_total: int
) -> str | None:
    """Appelle le LLM ; retourne le texte stripped, ou None si l'appel a échoué."""
    try:
        resultat = modele.generate_avec_messages(messages=messages, max_tokens=512)
    except Exception:
        logger.exception("Analyse LLM échouée")
        return None
    _ = nb_conflits_total  # réservé pour un usage futur (metrics)
    return resultat.texte.strip()


def _journaliser_parsing_echoue(analyse: str) -> None:
    """Trace un WARNING quand le JSON verdict est illisible (niveaux préservés)."""
    logger.warning(
        "Parsing JSON du verdict Conflit échoué — niveaux déterministes conservés. "
        "Sortie brute : %r",
        analyse[:200],
    )


def _appliquer_verdicts(
    conflits_analyses: list[ConflitDetecte], verdicts: dict[int, str]
) -> list[ConflitDetecte]:
    """Applique les verdicts (1-based) aux conflits ; drop les verdicts INEXISTANT."""
    retenus: list[ConflitDetecte] = []
    for i, conflit in enumerate(conflits_analyses):
        verdict = verdicts.get(i + 1)
        conflit.niveau = verdict_vers_niveau(verdict, conflit.niveau)
        if conflit.niveau == NiveauConflit.AUCUN:
            logger.info("Conflit %d écarté par le LLM (verdict INEXISTANT).", i + 1)
        else:
            retenus.append(conflit)
    return retenus
