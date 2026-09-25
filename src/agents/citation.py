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

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

# Structures, statuts et normalisation extraits dans citation_types.py (module
# neutre) : `citation_llm` en a besoin, et ne peut pas les importer d'ici sans
# créer un cycle (ce module importe `citation_llm` dans ses méthodes).
from src.agents.citation_types import (
    CitationReglementaire as CitationReglementaire,
)
from src.agents.citation_types import (
    StatutCitation as StatutCitation,
)
from src.agents.citation_types import (
    normaliser_pour_comparaison as normaliser_pour_comparaison,
)

# Normalisation des replis et calcul des sources citees vivent dans des
# modules feuilles : `explainer` doit pouvoir les utiliser sans refermer
# le cycle citation -> explainer. Re-exportes ici pour les appelants
# historiques (orchestrator, tests).
from src.agents.sources import (
    sources_referencees as sources_referencees,
)
from src.models import EvidenceRecuperee

if TYPE_CHECKING:
    from src.mlx_utils import MLXInference

logger = logging.getLogger(__name__)

# Alias historique : le nom privé reste importable (tests d'ancrage,
# `citation_llm` avant ce découpage, monkey-patch éventuel).
_normaliser_pour_comparaison = normaliser_pour_comparaison


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
    return _extrait_ancre_dans_chunk(cit, chunk)


def _extrait_ancre_dans_chunk(
    cit: CitationReglementaire,
    chunk: EvidenceRecuperee,
) -> bool:
    """True si l'extrait normalisé est non vide et présent dans le chunk."""
    extrait_norm = _normaliser_pour_comparaison(cit.extrait)
    if not extrait_norm:
        # Un extrait vide est contenu dans n'importe quelle chaîne : sans ce
        # garde-fou, une citation sans texte cité serait déclarée VERIFIEE.
        logger.warning("Citation DOUTEUSE — extrait vide (chunk '%s').", cit.chunk_id)
        return False
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


def _citation_depuis_evidence(
    ev: EvidenceRecuperee,
    max_extrait: int,
) -> CitationReglementaire | None:
    """Construit une CitationReglementaire NON_VERIFIEE ; None si extrait vide."""
    extrait = ev.texte_extrait.strip()[:max_extrait]
    if not extrait:
        logger.warning("Chunk %s ignoré : extrait vide.", ev.chunk_id)
        return None
    return CitationReglementaire(
        document_id=ev.document_id,
        article_id=ev.article_id,
        valid_from=ev.valid_from,
        valid_to=ev.valid_to,
        extrait=extrait,
        chunk_id=ev.chunk_id,
        statut=StatutCitation.NON_VERIFIEE,
    )


def _avertissement_citations_douteuses(nb_douteuses: int) -> str | None:
    """Message d'avertissement si au moins une citation est douteuse (sinon None)."""
    if nb_douteuses == 0:
        return None
    return (
        f"{nb_douteuses} citation(s) non vérifiable(s) exclue(s) de la réponse finale."
    )


def _assembler_resultat_citation(
    verifiees: list[CitationReglementaire],
    douteuses: list[CitationReglementaire],
    mode: str,
) -> ResultatCitation:
    """Compose ResultatCitation + avertissement dérivé des citations douteuses."""
    return ResultatCitation(
        citations_verifiees=verifiees,
        citations_douteuses=douteuses,
        mode=mode,
        avertissement=_avertissement_citations_douteuses(len(douteuses)),
    )


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------
# `StatutCitation` et `CitationReglementaire` vivent dans
# `src/agents/citation_types.py` (module neutre) et sont ré-exportés ci-dessus :
# `citation_llm` en a besoin sans pouvoir importer ce module (cycle).


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
        """Une citation par EvidenceRecuperee (métadonnées uniquement)."""
        citations = [
            c
            for c in (_citation_depuis_evidence(ev, max_extrait) for ev in evidences)
            if c is not None
        ]
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
        return _assembler_resultat_citation(verifiees, douteuses, mode)

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
