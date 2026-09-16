"""src/agents/conflit_helpers.py — Helpers partagés de l'agent Conflit.

Module neutre introduit pour casser un import circulaire : le helper de
normalisation des verdicts vivait dans `conflit_llm.py` et était ré-exporté
par `conflit.py`. Or `conflit_llm.py` importe `conflit.py` au niveau module,
donc `python -c "import src.agents.conflit_llm"` échouait (`conflit.py`
ré-importait un nom pas encore défini dans `conflit_llm` en cours
d'exécution). Les deux modules importent désormais le helper d'ici.
"""

from __future__ import annotations

# Verdicts acceptés du LLM Conflit. Tout autre libellé est rejeté : il peut
# venir de l'écho de l'exemple du prompt dans le raisonnement de
# DeepSeek-R1 plutôt que du verdict réellement rendu.
VERDICTS_VALIDES: frozenset[str] = frozenset({"CONFIRME", "APPARENT", "INEXISTANT"})


def normaliser_verdict(verdict: str) -> str:
    """Normalise un verdict LLM : majuscules, sans accents ni ponctuation."""
    valeur = verdict.strip().upper()
    remplacements = {"É": "E", "È": "E", "Ê": "E", "Ë": "E", "À": "A", "Â": "A"}
    for ancien, nouveau in remplacements.items():
        valeur = valeur.replace(ancien, nouveau)
    return valeur.strip(" .,:;!?\"'")
