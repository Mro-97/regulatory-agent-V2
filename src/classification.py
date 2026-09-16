"""src/classification.py — Routage d'une requête vers le bon pipeline RAG.

Extrait de src/orchestrator.py (§12 étape 6 : extraire plutôt que
désactiver le seuil §10). Aucun changement de comportement — importé
tel quel par l'Orchestrateur.
"""

from __future__ import annotations

import re
from datetime import date

# L10 : couvre les formulations manquantes « depuis 2020 », « à partir de
# 2021 », « en vigueur depuis 2019 » et l'année seule. L'année seule est
# bornée par `(?<!/)…(?!/)` pour ne pas confondre une date avec un numéro de
# règlement (« 2016/679 », très fréquent dans le corpus).
_MOTS_CLES_TEMPORELS = re.compile(
    r"\b("
    r"en\s+\d{4}"
    r"|avant\s+\d{4}"
    r"|après\s+\d{4}"
    r"|depuis\s+\d{4}"
    r"|à\s+partir\s+de\s+\d{4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|applicable\s+(?:le|au|en)"
    r"|version\s+(?:de|du|en)\s+\d{4}"
    r"|historique"
    r"|à\s+(?:cette|la)\s+(?:date|époque)"
    r"|(?<!/)\d{4}(?!/)"
    r")\b",
    re.IGNORECASE,
)

_MOTS_CLES_CONFLIT = re.compile(
    r"\b(contradict|conflit|incompatible|contradiction|incohérence|contraire|oppose)\b",
    re.IGNORECASE,
)


def classifier_requete(question: str, date_contexte: date | None) -> str:
    """Retourne le type de pipeline : ``temporelle``, ``conflit`` ou ``courante``."""
    if date_contexte is not None:
        return "temporelle"
    if _MOTS_CLES_TEMPORELS.search(question):
        return "temporelle"
    if _MOTS_CLES_CONFLIT.search(question):
        return "conflit"
    return "courante"
