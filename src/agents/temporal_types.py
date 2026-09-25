"""src/agents/temporal_types.py — Type partagé de l'agent Temporel.

Module NEUTRE : `temporal_llm` a besoin d'`EvidenceTemporelle` pour typer ses
entrées, et l'importer depuis `temporal` créait un cycle (ce dernier importe
`temporal_llm` dans ses méthodes). `temporal` ré-exporte le type pour ses
appelants historiques.

Même motif que `citation_types.py` et `conflit_types.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.models import EvidenceRecuperee


@dataclass
class EvidenceTemporelle:
    """Evidence enrichie d'une annotation temporelle.
    Wrappée autour d'EvidenceRecuperee — on ne modifie pas le modèle source.
    """  # noqa: D205

    evidence: EvidenceRecuperee
    applicable: bool
    raison_exclusion: str | None = None
    explication: str | None = None
