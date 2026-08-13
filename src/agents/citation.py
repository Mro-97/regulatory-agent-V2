"""
src/agents/citation.py — Agent Citation de Regulatory Agent V2
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
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

from config import cfg
from src.models import EvidenceRecuperee

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


class StatutCitation(str, Enum):
    """Statut de vérification d'une citation."""
    VERIFIEE = "vérifiée"       # ancrée dans les preuves récupérées
    DOUTEUSE = "douteuse"       # non retrouvée dans les preuves
    NON_VERIFIEE = "non_vérifiée"  # vérification non encore effectuée


@dataclass
class CitationReglementaire:
    """
    Référence exacte à un passage réglementaire.
    Chaque citation doit être rattachée à un chunk_id connu.
    """
    document_id: str
    article_id: str
    valid_from: date
    valid_to: Optional[date]
    extrait: str                    # passage exact cité (max 200 chars)
    chunk_id: str                   # identifiant du chunk source
    statut: StatutCitation = StatutCitation.NON_VERIFIEE
    hash_extrait: str = field(default="")  # SHA-256 de l'extrait

    def __post_init__(self) -> None:
        if not self.hash_extrait:
            self.hash_extrait = hashlib.sha256(
                self.extrait.encode("utf-8")
            ).hexdigest()

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
    avertissement: Optional[str] = None


# ---------------------------------------------------------------------------
# Agent Citation
# ---------------------------------------------------------------------------


class AgentCitation:
    """
    Agent de génération et vérification des citations réglementaires.

    Paramètres :
        use_llm : Si True, utilise Mistral 7B pour extraire les passages
                  cités depuis le texte de l'Explainer.
                  Si False (défaut), génère les citations directement
                  depuis les métadonnées des preuves.
    """

    def __init__(self, use_llm: bool = False) -> None:
        self.use_llm = use_llm
        self._modele = None
        logger.info("AgentCitation initialisé — use_llm=%s", use_llm)

    # ------------------------------------------------------------------
    # Génération déterministe
    # ------------------------------------------------------------------

    def _generer_depuis_evidences(
        self,
        evidences: list[EvidenceRecuperee],
        max_extrait: int = 200,
    ) -> list[CitationReglementaire]:
        """
        Génère une citation par EvidenceRecuperee.

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
                logger.warning(
                    "Chunk %s ignoré : extrait vide.", ev.chunk_id
                )
                continue

            citations.append(CitationReglementaire(
                document_id=ev.document_id,
                article_id=ev.article_id,
                valid_from=ev.valid_from,
                valid_to=ev.valid_to,
                extrait=extrait,
                chunk_id=ev.chunk_id,
                statut=StatutCitation.NON_VERIFIEE,
            ))

        logger.debug(
            "%d citation(s) générée(s) depuis %d preuve(s)",
            len(citations), len(evidences),
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
        """
        Vérifie que chaque citation est ancrée dans les preuves récupérées.

        Règle : une citation est VERIFIEE si son chunk_id figure dans
        la liste des preuves de référence ET que l'extrait est un
        sous-ensemble du texte du chunk source.

        Une citation DOUTEUSE ne peut pas être présentée à l'utilisateur
        comme une référence autoritaire.

        Args:
            citations:           Citations à vérifier.
            evidences_reference: Preuves récupérées servant de référence.

        Returns:
            Tuple (vérifiées, douteuses).
        """
        # Index des chunks par chunk_id pour la vérification O(1)
        index_chunks: dict[str, EvidenceRecuperee] = {
            ev.chunk_id: ev for ev in evidences_reference
        }

        verifiees: list[CitationReglementaire] = []
        douteuses: list[CitationReglementaire] = []

        for cit in citations:
            chunk = index_chunks.get(cit.chunk_id)

            if chunk is None:
                cit.statut = StatutCitation.DOUTEUSE
                douteuses.append(cit)
                logger.warning(
                    "Citation DOUTEUSE — chunk_id '%s' introuvable dans les preuves.",
                    cit.chunk_id,
                )
                continue

            # Vérification de l'extrait : doit être contenu dans le texte source
            if cit.extrait.strip() not in chunk.texte_extrait:
                cit.statut = StatutCitation.DOUTEUSE
                douteuses.append(cit)
                logger.warning(
                    "Citation DOUTEUSE — extrait non retrouvé dans chunk '%s'.",
                    cit.chunk_id,
                )
                continue

            cit.statut = StatutCitation.VERIFIEE
            verifiees.append(cit)

        logger.info(
            "Vérification : %d vérifiée(s), %d douteuse(s) sur %d",
            len(verifiees), len(douteuses), len(citations),
        )
        return verifiees, douteuses

    # ------------------------------------------------------------------
    # Mode LLM — extraction depuis le texte de l'Explainer
    # ------------------------------------------------------------------

    def _charger_modele(self) -> None:
        """Charge Mistral 7B via le registre MLX (lazy)."""
        if self._modele is None:
            from src.mlx_utils import get_model
            self._modele = get_model(
                model_name=cfg.modele_citation,
                temperature=0.0,
            )
            logger.info("Modèle Citation chargé : %s", cfg.modele_citation)

    def _extraire_avec_llm(
        self,
        reponse_explainer: str,
        evidences: list[EvidenceRecuperee],
    ) -> list[CitationReglementaire]:
        """
        Utilise Mistral 7B pour identifier quels passages des preuves
        ont été utilisés dans la réponse de l'Explainer.

        Le LLM reçoit la réponse et les textes des preuves, et retourne
        les chunk_id des preuves effectivement citées.
        La vérification déterministe s'applique ensuite.

        Args:
            reponse_explainer: Texte généré par l'Explainer.
            evidences:         Preuves disponibles.

        Returns:
            Citations identifiées (statut NON_VERIFIEE avant verify()).
        """
        self._charger_modele()

        # Contexte des preuves pour le LLM
        contexte_preuves = "\n\n".join(
            f"CHUNK_ID: {ev.chunk_id}\n"
            f"SOURCE: {ev.document_id}/{ev.article_id}\n"
            f"TEXTE: {ev.texte_extrait[:300]}"
            for ev in evidences[:10]
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "Tu es un assistant qui identifie les sources utilisées "
                    "dans un texte réglementaire. Tu réponds UNIQUEMENT avec "
                    "une liste de CHUNK_ID séparés par des virgules. "
                    "Tu n'inventes aucun chunk_id. "
                    "Si aucun chunk n'est clairement utilisé, réponds: AUCUN"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Voici la réponse générée :\n{reponse_explainer[:1000]}\n\n"
                    f"Voici les chunks disponibles :\n{contexte_preuves}\n\n"
                    "Quels CHUNK_ID ont été utilisés pour construire cette réponse ? "
                    "Réponds uniquement avec les CHUNK_ID séparés par des virgules."
                ),
            },
        ]

        try:
            resultat = self._modele.generate_avec_messages(
                messages=messages,
                max_tokens=128,
            )
            texte = resultat.texte.strip()

            if texte.upper() == "AUCUN" or not texte:
                logger.info("LLM : aucun chunk identifié comme cité.")
                return []

            # Parser les chunk_id retournés
            chunk_ids_bruts = [c.strip() for c in texte.split(",")]
            index_chunks = {ev.chunk_id: ev for ev in evidences}

            citations: list[CitationReglementaire] = []
            for chunk_id in chunk_ids_bruts:
                ev = index_chunks.get(chunk_id)
                if ev:
                    citations.append(CitationReglementaire(
                        document_id=ev.document_id,
                        article_id=ev.article_id,
                        valid_from=ev.valid_from,
                        valid_to=ev.valid_to,
                        extrait=ev.texte_extrait[:200],
                        chunk_id=ev.chunk_id,
                    ))
                else:
                    logger.warning(
                        "LLM a proposé un chunk_id inexistant : '%s' — ignoré.",
                        chunk_id,
                    )

            return citations

        except Exception as exc:
            logger.error("Extraction LLM échouée, bascule déterministe : %s", exc)
            return self._generer_depuis_evidences(evidences)

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    def generate(
        self,
        evidences: list[EvidenceRecuperee],
        reponse_explainer: Optional[str] = None,
    ) -> ResultatCitation:
        """
        Génère et vérifie les citations pour une réponse réglementaire.

        Flux :
          1. Génération des citations (déterministe ou LLM).
          2. Vérification déterministe de chaque citation.
          3. Retour du résultat avec séparation vérifiées / douteuses.

        Args:
            evidences:          Preuves récupérées et filtrées.
            reponse_explainer:  Texte de l'Explainer (requis si use_llm=True).

        Returns:
            ResultatCitation avec citations vérifiées et douteuses.
        """
        logger.info(
            "Génération citations — mode=%s evidences=%d",
            "llm" if self.use_llm else "deterministe", len(evidences),
        )

        if not evidences:
            return ResultatCitation(
                citations_verifiees=[],
                citations_douteuses=[],
                mode="deterministe",
                avertissement="Aucune preuve disponible — aucune citation produite.",
            )

        # --- Génération ---
        if self.use_llm and reponse_explainer:
            citations_brutes = self._extraire_avec_llm(
                reponse_explainer=reponse_explainer,
                evidences=evidences,
            )
            mode = "llm"
        else:
            citations_brutes = self._generer_depuis_evidences(evidences)
            mode = "deterministe"

        # --- Vérification (toujours déterministe) ---
        verifiees, douteuses = self.verify(
            citations=citations_brutes,
            evidences_reference=evidences,
        )

        avertissement = None
        if douteuses:
            avertissement = (
                f"{len(douteuses)} citation(s) non vérifiable(s) exclue(s) "
                f"de la réponse finale."
            )

        return ResultatCitation(
            citations_verifiees=verifiees,
            citations_douteuses=douteuses,
            mode=mode,
            avertissement=avertissement,
        )
