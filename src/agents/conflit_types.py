"""src/agents/conflit_types.py — Types partagés de l'agent Conflit.

Module NEUTRE (aucun import de `conflit` ni `conflit_llm`) : `conflit_llm`
importait `NiveauConflit` et `ConflitDetecte` depuis `conflit`, qui importe
`conflit_llm` dans ses méthodes — un cycle au niveau module. Les deux modules
importent désormais ces types d'ici, et `conflit` les ré-exporte pour ses
appelants historiques.

Même motif que `conflit_helpers.py` (verdicts) et `citation_types.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.models import EvidenceRecuperee


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
