"""src/agents/citation.py — Agent Citation de Regulatory Agent V2
===============================================================

Responsabilité : produire et vérifier les références exactes associées
à une réponse réglementaire.

Deux opérations distinctes (conformément au skill evidence-audit) :

  1. generate() — génère les citations depuis les EvidenceRecuperee
     Construit des références structurées (CitationReglementaire) en
     s'appuyant uniquement sur les métadonnées des preuves récupérées.
     Jamais depuis la mémoire du modèle.

  2. verify() — vérifie qu'une citation est ancrée dans les preuves
     Contrôle déterministe : chaque citation doit pointer vers un chunk
     effectivement récupéré. Toute citation non vérifiable est marquée
     DOUTEUSE et ne peut pas être présentée comme autoritaire.

Mode LLM (use_llm=True, Mistral 7B) :
     Utilisé pour extraire des citations précises depuis la réponse
     générée par l'Explainer (repérage des passages cités dans le texte).
     Le LLM ne peut proposer que des citations déjà présentes dans
     les preuves — la vérification déterministe rejette le reste.

Principe absolu : une citation non vérifiée ne sort jamais de l'agent.

Dépendances : src/mlx_utils.py, src/models.py
"""  # noqa: D205, D415

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

from src.models import EvidenceRecuperee

if TYPE_CHECKING:
    from src.mlx_utils import MLXInference

logger = logging.getLogger(__name__)

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


def _normaliser_pour_comparaison(texte: str) -> str:
    """Normalise un texte pour la comparaison d'ancrage citation/chunk.

    Neutralise les écarts purement typographiques (espaces multiples,
    retours à la ligne, guillemets courbes vs droits) qui ne changent pas
    le contenu réglementaire mais font échouer une comparaison littérale.
    """
    texte = texte.translate(_GUILLEMETS)
    texte = _ESPACES_MULTIPLES.sub(" ", texte)
    return texte.strip()


def _est_citation_verifiee(
    cit: CitationReglementaire,
    index_chunks: dict[str, EvidenceRecuperee],
) -> bool:
    """True si le chunk_id existe et si l'extrait est ancré (comparaison normalisée)."""
    chunk = index_chunks.get(cit.chunk_id)
    if chunk is None:
        logger.warning(
            "Citation DOUTEUSE — chunk_id '%s' introuvable dans les preuves.",
            cit.chunk_id,
        )
        return False
    extrait_norm = _normaliser_pour_comparaison(cit.extrait)
    chunk_norm = _normaliser_pour_comparaison(chunk.texte_extrait)
    if extrait_norm not in chunk_norm:
        logger.warning(
            "Citation DOUTEUSE — extrait non retrouvé dans chunk '%s'.",
            cit.chunk_id,
        )
        return False
    return True


def _journaliser_verification(
    nb_verifiees: int, nb_douteuses: int, nb_total: int
) -> None:
    """Trace le résultat de la vérification déterministe."""
    logger.info(
        "Vérification : %d vérifiée(s), %d douteuse(s) sur %d",
        nb_verifiees,
        nb_douteuses,
        nb_total,
    )


def _resultat_citation_vide() -> ResultatCitation:
    """ResultatCitation vide avec avertissement 'aucune preuve disponible'."""
    return ResultatCitation(
        citations_verifiees=[],
        citations_douteuses=[],
        mode="deterministe",
        avertissement="Aucune preuve disponible — aucune citation produite.",
    )


def _avertissement_citations_douteuses(nb_douteuses: int) -> str | None:
    """Message d'avertissement si au moins une citation est douteuse (sinon None)."""
    if nb_douteuses == 0:
        return None
    return (
        f"{nb_douteuses} citation(s) non vérifiable(s) exclue(s) de la réponse finale."
    )


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


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


@dataclass
class ResultatCitation:
    """Résultat complet de l'agent Citation."""

    citations_verifiees: list[CitationReglementaire]
    citations_douteuses: list[CitationReglementaire]
    mode: str  # "deterministe" ou "llm"
    avertissement: str | None = None


# ---------------------------------------------------------------------------
# Agent Citation
# ---------------------------------------------------------------------------


class AgentCitation:
    """Agent de génération et vérification des citations réglementaires.

    Paramètres :
        use_llm : Si True, utilise Mistral 7B pour extraire les passages
                  cités depuis le texte de l'Explainer.
                  Si False (défaut), génère les citations directement
                  depuis les métadonnées des preuves.
    """

    def __init__(self, use_llm: bool = False) -> None:  # noqa: D107
        self.use_llm = use_llm
        self._modele: MLXInference | None = None
        logger.info("AgentCitation initialisé — use_llm=%s", use_llm)

    # ------------------------------------------------------------------
    # Génération déterministe
    # ------------------------------------------------------------------

    def _generer_depuis_evidences(
        self,
        evidences: list[EvidenceRecuperee],
        max_extrait: int = 200,
    ) -> list[CitationReglementaire]:
        """Génère une citation par EvidenceRecuperee.

        Chaque citation est directement construite depuis les métadonnées
        du chunk — aucune inférence, aucun risque d'invention.

        Args:
            evidences:   Preuves récupérées et filtrées.
            max_extrait: Longueur maximale de l'extrait cité.

        Returns:
            Liste de CitationReglementaire non encore vérifiées.
        """
        citations: list[CitationReglementaire] = []

        for ev in evidences:
            extrait = ev.texte_extrait.strip()[:max_extrait]
            if not extrait:
                logger.warning("Chunk %s ignoré : extrait vide.", ev.chunk_id)
                continue

            citations.append(
                CitationReglementaire(
                    document_id=ev.document_id,
                    article_id=ev.article_id,
                    valid_from=ev.valid_from,
                    valid_to=ev.valid_to,
                    extrait=extrait,
                    chunk_id=ev.chunk_id,
                    statut=StatutCitation.NON_VERIFIEE,
                )
            )

        logger.debug(
            "%d citation(s) générée(s) depuis %d preuve(s)",
            len(citations),
            len(evidences),
        )
        return citations

    # ------------------------------------------------------------------
    # Vérification déterministe
    # ------------------------------------------------------------------

    def verify(
        self,
        citations: list[CitationReglementaire],
        evidences_reference: list[EvidenceRecuperee],
    ) -> tuple[list[CitationReglementaire], list[CitationReglementaire]]:
        """Vérifie que chaque citation est ancrée dans les preuves fournies."""
        index_chunks = {ev.chunk_id: ev for ev in evidences_reference}
        verifiees: list[CitationReglementaire] = []
        douteuses: list[CitationReglementaire] = []
        for cit in citations:
            if _est_citation_verifiee(cit, index_chunks):
                cit.statut = StatutCitation.VERIFIEE
                verifiees.append(cit)
            else:
                cit.statut = StatutCitation.DOUTEUSE
                douteuses.append(cit)
        _journaliser_verification(len(verifiees), len(douteuses), len(citations))
        return verifiees, douteuses

    # ------------------------------------------------------------------
    # Mode LLM — extraction depuis le texte de l'Explainer
    # ------------------------------------------------------------------

    def _charger_modele(self) -> None:
        """Charge Mistral 7B via le registre MLX (lazy)."""
        from src.agents.citation_llm import charger_modele_citation

        self._modele = charger_modele_citation(self._modele)

    def _extraire_avec_llm(
        self,
        reponse_explainer: str,
        evidences: list[EvidenceRecuperee],
    ) -> list[CitationReglementaire]:
        """Extraction LLM déléguée à src.agents.citation_llm."""
        from src.agents.citation_llm import extraire_avec_llm
        from src.errors import ModelNotLoadedError

        self._charger_modele()
        if self._modele is None:
            raise ModelNotLoadedError("Citation")
        resultat = extraire_avec_llm(
            modele=self._modele,
            reponse_explainer=reponse_explainer,
            evidences=evidences,
        )
        if resultat is None:
            return self._generer_depuis_evidences(evidences)
        return resultat

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    def generate(
        self,
        evidences: list[EvidenceRecuperee],
        reponse_explainer: str | None = None,
    ) -> ResultatCitation:
        """Génère + vérifie les citations (LLM ou déterministe selon `use_llm`)."""
        logger.info(
            "Génération citations — mode=%s evidences=%d",
            "llm" if self.use_llm else "deterministe",
            len(evidences),
        )
        if not evidences:
            return _resultat_citation_vide()
        citations_brutes, mode = self._produire_citations_brutes(
            evidences, reponse_explainer
        )
        verifiees, douteuses = self.verify(
            citations=citations_brutes,
            evidences_reference=evidences,
        )
        return ResultatCitation(
            citations_verifiees=verifiees,
            citations_douteuses=douteuses,
            mode=mode,
            avertissement=_avertissement_citations_douteuses(len(douteuses)),
        )

    def _produire_citations_brutes(
        self,
        evidences: list[EvidenceRecuperee],
        reponse_explainer: str | None,
    ) -> tuple[list[CitationReglementaire], str]:
        """Choisit la stratégie de génération (LLM ou déterministe) et l'exécute."""
        if self.use_llm and reponse_explainer:
            return (
                self._extraire_avec_llm(
                    reponse_explainer=reponse_explainer,
                    evidences=evidences,
                ),
                "llm",
            )
        return self._generer_depuis_evidences(evidences), "deterministe"
