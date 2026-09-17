"""src/agents/conflit_llm.py — Analyse LLM des conflits potentiels.

Extraite de src/agents/conflit.py (§12 étape 6). DeepSeek-R1 14B annote
les conflits potentiels détectés déterministiquement, sans jamais les
créer : la liste d'entrée provient toujours de l'heuristique lexicale.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from config import cfg
from src.agents.conflit import ConflitDetecte, NiveauConflit
from src.agents.conflit_helpers import VERDICTS_VALIDES, normaliser_verdict
from src.agents.temperatures import TEMPERATURE_RAISONNEMENT
from src.prompts_loader import charger_prompt

if TYPE_CHECKING:
    from src.mlx_utils import MLXInference

logger = logging.getLogger(__name__)


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
            temperature=TEMPERATURE_RAISONNEMENT,
        )
        logger.info("Modèle Conflit chargé : %s", cfg.modele_conflit)
    return modele


def extraire_verdicts(analyse: str) -> dict[int, str] | None:
    """Retourne {conflit: verdict_normalisé} extrait de la sortie LLM (None si KO).

    M12 : DeepSeek-R1 recopie souvent l'exemple du prompt (avec son
    `{"verdicts": …}`) dans son raisonnement avant de produire la vraie
    réponse. On ne parse donc que le DERNIER objet JSON équilibré contenant
    `verdicts` — jamais le premier écho — et on ignore les libellés hors
    {CONFIRME, APPARENT, INEXISTANT}.
    """
    for candidat in reversed(_objets_json_equilibres(analyse)):
        if '"verdicts"' not in candidat:
            continue
        mapping = _extraire_mapping_verdicts(candidat)
        if mapping:
            return mapping
        # Dernier objet `verdicts` illisible ou sans verdict valide : ne pas
        # remonter à un écho antérieur de l'exemple du prompt (faux verdict).
        return None
    return None


def _objets_json_equilibres(texte: str) -> list[str]:
    r"""Objets JSON équilibrés de premier niveau présents dans `texte`.

    Scan caractère par caractère en ignorant les accolades situées dans une
    chaîne JSON (et les échappements `\"`) — c'est ce qui permet d'extraire
    un objet noyé dans du texte libre ou dans un bloc ```json.
    """
    objets: list[str] = []
    debut: int | None = None
    profondeur = 0
    dans_chaine = False
    echappe = False
    for i, caractere in enumerate(texte):
        if dans_chaine:
            dans_chaine, echappe = _etat_dans_chaine(caractere, echappe)
            continue
        if caractere == '"':
            dans_chaine = True
        else:
            debut, profondeur, objet = _scanner_accolade(texte, i, debut, profondeur)
            if objet is not None:
                objets.append(objet)
    return objets


def _etat_dans_chaine(caractere: str, echappe: bool) -> tuple[bool, bool]:
    """État (dans_chaine, echappe) après lecture d'un caractère de chaîne JSON."""
    if echappe:
        return True, False
    if caractere == "\\":
        return True, True
    if caractere == '"':
        return False, False
    return True, False


def _scanner_accolade(
    texte: str,
    index: int,
    debut: int | None,
    profondeur: int,
) -> tuple[int | None, int, str | None]:
    """État des accolades après `index` et objet fermé le cas échéant.

    Retourne `(debut, profondeur, objet)` : `objet` n'est non-None que si
    l'accolade fermante de `index` ramène la profondeur à zéro.
    """
    caractere = texte[index]
    if caractere == "{":
        return (index if profondeur == 0 else debut), profondeur + 1, None
    if caractere == "}" and profondeur > 0:
        profondeur -= 1
        if profondeur == 0 and debut is not None:
            return None, profondeur, texte[debut : index + 1]
    return debut, profondeur, None


def _extraire_mapping_verdicts(candidat: str) -> dict[int, str] | None:
    """Parse `candidat` en JSON, extrait `verdicts` en dict[int, str]."""
    try:
        donnees = json.loads(candidat)
    except Exception:  # noqa: BLE001 — parse best-effort
        return None
    liste = donnees.get("verdicts") if isinstance(donnees, dict) else None
    if not isinstance(liste, list):
        return None
    mapping: dict[int, str] = {}
    for entree in liste:
        _peupler_mapping_verdict(entree, mapping)
    return mapping or None


def _peupler_mapping_verdict(entree: Any, mapping: dict[int, str]) -> None:
    """Ajoute une entrée `{conflit: N, verdict: S}` au mapping si bien formée."""
    if not isinstance(entree, dict):
        return
    num = entree.get("conflit")
    verdict = entree.get("verdict")
    if not isinstance(num, int) or not isinstance(verdict, str):
        return
    verdict_normalise = normaliser_verdict(verdict)
    if verdict_normalise not in VERDICTS_VALIDES:
        logger.warning(
            "Verdict Conflit hors vocabulaire ignoré : %r (conflit %d)",
            verdict,
            num,
        )
        return
    mapping[num] = verdict_normalise


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
    analyse = _appeler_llm_conflit(modele, messages)
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
    modele: MLXInference, messages: list[dict[str, str]]
) -> str | None:
    """Appelle le LLM ; retourne le texte stripped, ou None si l'appel a échoué."""
    try:
        resultat = modele.generate_avec_messages(messages=messages, max_tokens=512)
    except Exception:
        logger.exception("Analyse LLM échouée")
        return None
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
