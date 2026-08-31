"""src/agents/conflit.py — Agent Conflit de Regulatory Agent V2
=============================================================

Responsabilité : détecter les contradictions et incohérences entre
les passages réglementaires récupérés.

Appelé sélectivement (~20 % des requêtes) uniquement lorsque la
question suggère une contradiction potentielle (mots-clés détectés
par l'orchestrateur) ou lorsque plusieurs documents couvrent le
même domaine sur la même période.

Deux niveaux :

  1. Détection déterministe (toujours exécutée)
     - Chevauchements temporels sur le même article entre documents
       différents (même périmètre, périodes qui se recoupent).
     - Obligations contradictoires repérées par heuristiques lexicales
       (ex. "doit" vs "ne doit pas", "interdit" vs "autorisé").
     - Rapide, sans modèle.

  2. Analyse LLM via DeepSeek-R1 14B (use_llm=True)
     Utilisée uniquement quand la détection déterministe signale un
     conflit potentiel. DeepSeek-R1 est un modèle de raisonnement :
     il reçoit les passages en tension et produit une analyse argumentée.
     Il ne tranche pas juridiquement — il identifie et explique.

Principe absolu :
  L'agent Conflit ne fait jamais autorité. Il signale et explique.
  La résolution appartient à un juriste humain (human-in-the-loop).
  Toute contradiction non résolue est soumise à pending_links.

Dépendances : src/mlx_utils.py, src/models.py
"""  # noqa: D205, D415

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

from src.models import EvidenceRecuperee

if TYPE_CHECKING:
    from src.mlx_utils import MLXInference

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


class NiveauConflit(StrEnum):
    """Niveau de sévérité d'un conflit détecté."""

    AUCUN = "aucun"
    POTENTIEL = "potentiel"  # heuristique — à confirmer par un juriste
    PROBABLE = "probable"  # LLM confirme une tension réelle
    CRITIQUE = "critique"  # obligations directement contradictoires


@dataclass
class ConflitDetecte:
    """Description d'un conflit entre deux passages réglementaires."""

    evidence_a: EvidenceRecuperee
    evidence_b: EvidenceRecuperee
    niveau: NiveauConflit
    description: str
    necessite_validation_humaine: bool = True


@dataclass
class ResultatConflit:
    """Résultat complet de l'agent Conflit."""

    conflits: list[ConflitDetecte]
    niveau_global: NiveauConflit
    analyse_llm: str | None = None
    mode: str = "deterministe"
    necessite_validation_humaine: bool = False


# ---------------------------------------------------------------------------
# Heuristiques lexicales
# ---------------------------------------------------------------------------

# Paires de termes contradictoires (forme positive / forme négative)
_PAIRES_CONTRADICTOIRES = [
    (r"\bdoit\b", r"\bne doit pas\b"),
    (r"\bobligatoire\b", r"\bfacultatif\b"),
    (r"\binterdit\b", r"\bautorisé\b"),
    (r"\bprohibé\b", r"\bpermis\b"),
    (r"\bexigé\b", r"\boptionnel\b"),
    (r"\bnécessaire\b", r"\bsuffisant\b"),
]


def _contient_terme(texte: str, pattern: str) -> bool:
    """True si `pattern` (regex) apparaît dans `texte` sans distinction de casse."""
    return bool(re.search(pattern, texte, re.IGNORECASE))


def _detecter_tension_lexicale(texte_a: str, texte_b: str) -> str | None:
    """Détecte une tension lexicale entre deux textes.
    Retourne une description si une contradiction est repérée, None sinon.
    """  # noqa: D205
    for pos, neg in _PAIRES_CONTRADICTOIRES:
        a_pos = _contient_terme(texte_a, pos)
        a_neg = _contient_terme(texte_a, neg)
        b_pos = _contient_terme(texte_b, pos)
        b_neg = _contient_terme(texte_b, neg)

        # Tension : l'un affirme, l'autre nie
        if (a_pos and b_neg) or (a_neg and b_pos):
            return f"Tension lexicale : '{pos}' vs '{neg}'"

    return None


# `_normaliser_verdict` a été déplacé vers src/agents/conflit_llm.py
# (§12 étape 6). Ré-exporté sous son nom d'origine pour compatibilité
# descendante (tests inclus).
# fmt: off
from src.agents.conflit_llm import _normaliser_verdict as _normaliser_verdict  # noqa: E402, I001
# fmt: on


def _paires(
    evidences: list[EvidenceRecuperee],
) -> Iterator[tuple[EvidenceRecuperee, EvidenceRecuperee]]:
    """Itère sur toutes les paires (i, j) avec i < j."""
    n = len(evidences)
    for i in range(n):
        for j in range(i + 1, n):
            yield evidences[i], evidences[j]


def _paire_active_a_date(
    ev_a: EvidenceRecuperee, ev_b: EvidenceRecuperee, date_ref: date | None
) -> bool:
    """True si les deux preuves sont valides à `date_ref` (True si date_ref None)."""
    if date_ref is None:
        return True
    return _est_active_a(ev_a, date_ref) and _est_active_a(ev_b, date_ref)


def _est_active_a(ev: EvidenceRecuperee, date_ref: date) -> bool:
    """True si `valid_from <= date_ref <= valid_to` (bornes inclusives)."""
    return ev.valid_from <= date_ref and (
        ev.valid_to is None or ev.valid_to >= date_ref
    )


def _conflit_inter_documents(
    ev_a: EvidenceRecuperee, ev_b: EvidenceRecuperee, tension: str
) -> ConflitDetecte:
    """Construit un ConflitDetecte pour une tension entre documents distincts."""
    return ConflitDetecte(
        evidence_a=ev_a,
        evidence_b=ev_b,
        niveau=NiveauConflit.POTENTIEL,
        description=(
            f"{tension} entre "
            f"{ev_a.document_id}/{ev_a.article_id} et "
            f"{ev_b.document_id}/{ev_b.article_id}"
        ),
        necessite_validation_humaine=True,
    )


def _conflit_intra_document(
    ev_a: EvidenceRecuperee, ev_b: EvidenceRecuperee, tension: str
) -> ConflitDetecte:
    """Construit un ConflitDetecte pour une incohérence interne à un document."""
    return ConflitDetecte(
        evidence_a=ev_a,
        evidence_b=ev_b,
        niveau=NiveauConflit.POTENTIEL,
        description=(
            f"Incohérence interne ({tension}) entre "
            f"{ev_a.article_id} et {ev_b.article_id} "
            f"dans {ev_a.document_id}"
        ),
        necessite_validation_humaine=True,
    )


def _journaliser_conflits_inter(conflits: list[ConflitDetecte]) -> None:
    """Log WARNING si des conflits ont été détectés, INFO sinon."""
    if conflits:
        logger.warning(
            "%d conflit(s) potentiel(s) détecté(s) entre documents distincts.",
            len(conflits),
        )
    else:
        logger.info("Aucun conflit inter-documents détecté.")


def _resultat_conflit_vide() -> ResultatConflit:
    """Retourne un ResultatConflit AUCUN (aucun conflit, mode déterministe)."""
    return ResultatConflit(
        conflits=[],
        niveau_global=NiveauConflit.AUCUN,
        mode="deterministe",
    )


def _calculer_niveau_global(conflits: list[ConflitDetecte]) -> NiveauConflit:
    """Retourne le niveau max (CRITIQUE > PROBABLE > POTENTIEL)."""
    niveaux = [c.niveau for c in conflits]
    if NiveauConflit.CRITIQUE in niveaux:
        return NiveauConflit.CRITIQUE
    if NiveauConflit.PROBABLE in niveaux:
        return NiveauConflit.PROBABLE
    return NiveauConflit.POTENTIEL


def _assembler_resultat_conflit(
    conflits: list[ConflitDetecte], analyse_llm: str | None, mode: str
) -> ResultatConflit:
    """Assemble le ResultatConflit final avec niveau global + validation requise."""
    niveau_global = _calculer_niveau_global(conflits)
    necessite = niveau_global in (NiveauConflit.PROBABLE, NiveauConflit.CRITIQUE)
    logger.info(
        "Résultat conflits — niveau=%s conflits=%d validation_requise=%s",
        niveau_global.value,
        len(conflits),
        necessite,
    )
    return ResultatConflit(
        conflits=conflits,
        niveau_global=niveau_global,
        analyse_llm=analyse_llm,
        mode=mode,
        necessite_validation_humaine=necessite,
    )


# ---------------------------------------------------------------------------
# Agent Conflit
# ---------------------------------------------------------------------------


class AgentConflit:
    """Agent de détection des contradictions réglementaires.

    Paramètres :
        use_llm : Si True, utilise DeepSeek-R1 14B pour analyser les
                  conflits potentiels détectés par l'heuristique.
                  DeepSeek-R1 14B pèse ~9 Go — ne charger que sur Mac C
                  ou sur Mac A/B avec swap explicite.
                  Défaut : False (détection déterministe uniquement).
    """

    def __init__(self, use_llm: bool = False) -> None:  # noqa: D107
        self.use_llm = use_llm
        self._modele: MLXInference | None = None
        logger.info("AgentConflit initialisé — use_llm=%s", use_llm)

    # ------------------------------------------------------------------
    # Détection déterministe
    # ------------------------------------------------------------------

    def _detecter_chevauchements(
        self,
        evidences: list[EvidenceRecuperee],
        date_ref: date | None,
    ) -> list[ConflitDetecte]:
        """Détecte tensions entre preuves de documents distincts actives à date_ref."""
        conflits: list[ConflitDetecte] = []
        for ev_a, ev_b in _paires(evidences):
            if ev_a.document_id == ev_b.document_id:
                continue
            if not _paire_active_a_date(ev_a, ev_b, date_ref):
                continue
            tension = _detecter_tension_lexicale(ev_a.texte_extrait, ev_b.texte_extrait)
            if tension:
                conflits.append(_conflit_inter_documents(ev_a, ev_b, tension))
        _journaliser_conflits_inter(conflits)
        return conflits

    def _detecter_incoherences_internes(
        self,
        evidences: list[EvidenceRecuperee],
    ) -> list[ConflitDetecte]:
        """Détecte les tensions entre articles distincts d'un même document."""
        conflits: list[ConflitDetecte] = []
        for ev_a, ev_b in _paires(evidences):
            if ev_a.document_id != ev_b.document_id:
                continue
            if ev_a.article_id == ev_b.article_id:
                continue
            tension = _detecter_tension_lexicale(ev_a.texte_extrait, ev_b.texte_extrait)
            if tension:
                conflits.append(_conflit_intra_document(ev_a, ev_b, tension))
        return conflits

    # ------------------------------------------------------------------
    # Analyse LLM (DeepSeek-R1 14B)
    # ------------------------------------------------------------------

    def _charger_modele(self) -> None:
        """Charge DeepSeek-R1 14B via le registre MLX (délégué)."""
        from src.agents.conflit_llm import charger_modele_conflit

        self._modele = charger_modele_conflit(self._modele)

    def _analyser_avec_llm(
        self,
        question: str,
        conflits: list[ConflitDetecte],
    ) -> tuple[list[ConflitDetecte], str]:
        """Analyse LLM déléguée à src.agents.conflit_llm."""
        from src.agents.conflit_llm import analyser_avec_llm
        from src.errors import ModelNotLoadedError

        self._charger_modele()
        if self._modele is None:
            raise ModelNotLoadedError("Conflict")
        return analyser_avec_llm(
            modele=self._modele,
            question=question,
            conflits=conflits,
        )

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    def analyser(
        self,
        question: str,
        evidences: list[EvidenceRecuperee],
        date_ref: date | None = None,
    ) -> ResultatConflit:
        """Détection déterministe + analyse LLM (si `use_llm`) des conflits."""
        logger.info(
            "Analyse conflits — evidences=%d date_ref=%s question=%r",
            len(evidences),
            date_ref,
            question[:80],
        )
        if len(evidences) < 2:
            return _resultat_conflit_vide()
        tous_conflits = self._detecter_chevauchements(
            evidences, date_ref
        ) + self._detecter_incoherences_internes(evidences)
        if not tous_conflits:
            return _resultat_conflit_vide()
        tous_conflits, analyse_llm, mode = self._raffiner_via_llm(
            question, tous_conflits
        )
        return _assembler_resultat_conflit(tous_conflits, analyse_llm, mode)

    def _raffiner_via_llm(
        self,
        question: str,
        conflits: list[ConflitDetecte],
    ) -> tuple[list[ConflitDetecte], str | None, str]:
        """Passe (conflits, analyse, mode) après LLM (ou déterministe si off)."""
        if not self.use_llm:
            return conflits, None, "deterministe"
        raffines, analyse = self._analyser_avec_llm(
            question=question,
            conflits=conflits,
        )
        return raffines, analyse, "llm"
