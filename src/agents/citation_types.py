"""src/agents/citation_types.py — Types et normalisation partagés des citations.

Module NEUTRE (aucun import de `citation` ni `citation_llm`) : `citation_llm`
importait `CitationReglementaire` et la normalisation depuis `citation`, qui
importe `citation_llm` dans ses méthodes — un cycle au niveau module. Les deux
modules importent désormais ces définitions d'ici, et `citation` les ré-exporte
pour ses appelants historiques (tests, API).

Même motif que `conflit_helpers.py` : quand deux modules d'un même agent ont
besoin des mêmes types, ces types vivent dans un troisième module feuille.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

# Guillemets typographiques (ouvrants/fermants, simples/doubles) → forme droite,
# pour que la vérification d'ancrage ne soit pas sensible au style de citation
# utilisé par le LLM (Mistral 7B reformate parfois « ... » en " ... ").
_GUILLEMETS = str.maketrans(
    {
        "«": '"',
        "»": '"',
        "“": '"',
        "”": '"',
        "„": '"',
        "‘": "'",  # noqa: RUF001 — caractère typographique français légitime
        "’": "'",  # noqa: RUF001 — caractère typographique français légitime
        "‚": "'",  # noqa: RUF001 — caractère typographique français légitime
    }
)

_ESPACES_MULTIPLES = re.compile(r"\s+")


def normaliser_pour_comparaison(texte: str) -> str:
    """Normalise un texte pour la comparaison d'ancrage citation/chunk.

    Neutralise les écarts purement typographiques (espaces multiples,
    retours à la ligne, guillemets courbes vs droits) qui ne changent pas
    le contenu réglementaire mais font échouer une comparaison littérale.
    """
    texte = texte.translate(_GUILLEMETS)
    texte = _ESPACES_MULTIPLES.sub(" ", texte)
    return texte.strip()


class StatutCitation(StrEnum):
    """Statut de vérification d'une citation."""

    VERIFIEE = "vérifiée"  # ancrée dans les preuves récupérées
    DOUTEUSE = "douteuse"  # non retrouvée dans les preuves
    NON_VERIFIEE = "non_vérifiée"  # vérification non encore effectuée


@dataclass
class CitationReglementaire:
    """Référence exacte à un passage réglementaire.
    Chaque citation doit être rattachée à un chunk_id connu.
    """  # noqa: D205

    document_id: str
    article_id: str
    valid_from: date
    valid_to: date | None
    extrait: str  # passage exact cité (max 200 chars)
    chunk_id: str  # identifiant du chunk source
    statut: StatutCitation = StatutCitation.NON_VERIFIEE
    hash_extrait: str = field(default="")  # SHA-256 de l'extrait

    def __post_init__(self) -> None:  # noqa: D105
        if not self.hash_extrait:
            self.hash_extrait = hashlib.sha256(self.extrait.encode("utf-8")).hexdigest()

    def reference_courte(self) -> str:
        """Format court pour affichage : DOCUMENT / ARTICLE [DATE→DATE]."""
        fin = self.valid_to.isoformat() if self.valid_to else "en vigueur"
        return f"{self.document_id} / {self.article_id} [{self.valid_from} → {fin}]"

    def reference_complete(self) -> str:
        """Format complet avec extrait et statut."""
        return (
            f"{self.reference_courte()}\n"
            f"Extrait : « {self.extrait[:200]} »\n"
            f"Statut : {self.statut.value} | hash : {self.hash_extrait[:16]}..."
        )
