"""src/agents/reponse_fondee.py — Détection des réponses de repli prescrites.

Module FEUILLE : `explainer` (niveau de confiance) ET `citation`
(`sources_referencees`) doivent reconnaître les phrases de refus prescrites par
le prompt. Les faire vivre dans `explainer` obligeait `citation` à l'importer —
un sens de dépendance qui interdisait à `explainer` d'utiliser les sources
déterministes de `src/agents/sources.py` sans refermer un cycle.

Les phrases complètes font foi — la sous-chaîne générique « ne contient pas
d'information » est écartée (M6) : le prompt demande explicitement au modèle de
s'en servir pour signaler une couverture PARTIELLE, donc une réponse fondée
pouvait la contenir et était à tort estampillée INCERTAIN.
"""

from __future__ import annotations

MARQUEURS_REPONSE_NON_FONDEE = (
    "les sources disponibles ne contiennent pas d'information",
    "cette question ne relève pas du droit réglementaire",
    "je ne peux pas répondre à cette question",
)


def reponse_est_non_fondee(reponse: str) -> bool:
    """True si la réponse LLM contient une phrase de repli prescrite.

    La détection porte sur les phrases de repli complètes, pas sur un
    fragment générique : une réponse qui mentionne une lacune partielle
    (« … ne contient pas d'information sur X, mais … ») reste fondée.
    """
    minuscule = (reponse or "").lower()
    return any(marqueur in minuscule for marqueur in MARQUEURS_REPONSE_NON_FONDEE)
