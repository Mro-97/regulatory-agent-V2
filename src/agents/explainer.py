"""src/agents/explainer.py — Agent Explainer de Regulatory Agent V2
=================================================================

Responsabilité : transformer une liste de preuves réglementaires
(EvidenceRecuperee) en une réponse en langage naturel, claire et
structurée, destinée à l'utilisateur final.

Deux modes :

  1. Assemblage structuré (use_llm=False, défaut)
     Construit une réponse lisible à partir des textes récupérés,
     sans consommer de RAM modèle. Utile pour les tests sur Mac A.

  2. Synthèse LLM via Qwen 2.5 7B (use_llm=True)
     Produit une réponse fluide, contextualisée, avec renvoi explicite
     aux sources. Activé quand les modèles sont disponibles.

Principe fondamental :
  L'Explainer ne peut utiliser QUE les textes présents dans les preuves.
  Il n'invente rien, ne complète pas depuis sa mémoire, ne fait pas
  d'affirmations non sourcées. Toute réponse doit être rattachée à une
  EvidenceRecuperee identifiable.

Format de sortie attendu :
  Réponse synthétique
  ↓
  Explication
  ↓
  Sources (document / article / version / dates de validité)

Dépendances : src/mlx_utils.py, src/models.py
"""  # noqa: D205, D415

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from config import cfg

from src.models import EvidenceRecuperee, NiveauConfiance

if TYPE_CHECKING:
    from src.mlx_utils import MLXInference

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structure de sortie
# ---------------------------------------------------------------------------


@dataclass
class ResultatExplication:
    """Résultat complet de l'agent Explainer."""

    reponse: str
    sources_citees: list[str]
    niveau_confiance: NiveauConfiance
    mode: str  # "llm" ou "assemblage"


# ---------------------------------------------------------------------------
# Helpers module-level pour _assembler / _synthetiser_avec_llm
# ---------------------------------------------------------------------------


_MSG_AUCUN_PASSAGE = (
    "Aucun passage réglementaire pertinent n'a été trouvé "
    "dans le corpus pour cette question.\n\n"
    "Vérifiez que les documents correspondants ont été ingérés, "
    "ou reformulez la question."
)

_AVERTISSEMENT = (
    "⚠️ Ces passages sont extraits du corpus réglementaire indexé. "
    "Ils ne constituent pas un avis juridique. "
    "Consultez les textes officiels (EUR-Lex, Légifrance) pour confirmation."
)


def _resultat_assemblage_vide() -> ResultatExplication:
    """Retourne un ResultatExplication INCERTAIN quand aucune preuve n'est fournie."""
    return ResultatExplication(
        reponse=_MSG_AUCUN_PASSAGE,
        sources_citees=[],
        niveau_confiance=NiveauConfiance.INCERTAIN,
        mode="assemblage",
    )


def _entete_assemblage(date_ref: date | None, type_pipeline: str) -> str:
    """Formatte l'en-tête d'un assemblage (temporel ou courant)."""
    if date_ref and type_pipeline == "temporelle":
        return f"Textes applicables à la date du {date_ref.strftime('%d/%m/%Y')} :\n"
    return "Textes réglementaires pertinents :\n"


def _ajouter_blocs_evidences(
    evidences: list[EvidenceRecuperee],
    lignes: list[str],
    sources_citees: list[str],
) -> None:
    """Ajoute (in place) un bloc lisible par preuve, jusqu'à 8 preuves."""
    for i, ev in enumerate(evidences[:8], 1):
        validite = f"{ev.valid_from} → {ev.valid_to or 'en vigueur'}"
        ref = f"{ev.document_id} / {ev.article_id} [{validite}]"
        sources_citees.append(ref)
        lignes.append(f"**[{i}] {ref}**")
        lignes.append(ev.texte_extrait.strip())
        lignes.append("")


def _ajouter_reste_et_avertissement(nb_total: int, lignes: list[str]) -> None:
    """Ajoute la mention du surplus (si > 8) puis l'avertissement final."""
    if nb_total > 8:
        lignes.append(
            f"... et {nb_total - 8} passage(s) supplémentaire(s) non affichés."
        )
        lignes.append("")
    lignes.append(_AVERTISSEMENT)


def _construire_sources_citees(evidences: list[EvidenceRecuperee]) -> list[str]:
    """Formate `document_id/article_id [from→to]` pour les 8 premières preuves."""
    return [
        f"{ev.document_id}/{ev.article_id} "
        f"[{ev.valid_from}→{ev.valid_to or 'en vigueur'}]"
        for ev in evidences[:8]
    ]


def _bloc_source(ev: EvidenceRecuperee) -> str:
    """Formate une preuve en bloc `<SOURCE …>…</SOURCE>` pour le contexte LLM."""
    validite = f"{ev.valid_from} → {ev.valid_to or 'en vigueur'}"
    return (
        f"<SOURCE document={ev.document_id} article={ev.article_id} "
        f"validite={validite}>\n{ev.texte_extrait.strip()}\n</SOURCE>"
    )


def _construire_contexte_temporel(date_ref: date | None, type_pipeline: str) -> str:
    """Retourne la clause temporelle du prompt (vide si non-temporelle)."""
    if not date_ref or type_pipeline != "temporelle":
        return ""
    return (
        f"\nATTENTION : La question porte sur la réglementation applicable "
        f"à la date du {date_ref}. Utilise uniquement les versions valides "
        f"à cette date (indiquées dans les sources).\n"
    )


def _preparer_messages_synthese(
    question: str,
    contexte: str,
    date_ref: date | None,
    type_pipeline: str,
) -> list[dict[str, str]]:
    """Charge le gabarit `explainer/synthetiser` v2 et le rend avec les variables.

    v2 (2026-09-01) durcit le prompt : interdiction stricte de sources
    externes, fallback obligatoire si le corpus est insuffisant, réponse
    figée pour les questions hors droit réglementaire, refus de générer
    du code ou de révéler l'architecture. Défense frontale contre le
    prompt-injection persistant identifié lors de l'audit sécu.
    """
    from src.prompts_loader import charger_prompt

    return charger_prompt("explainer/synthetiser", 2).rendre(
        question=question,
        contexte=contexte,
        contexte_temporel=_construire_contexte_temporel(date_ref, type_pipeline),
    )


# ---------------------------------------------------------------------------
# Agent Explainer
# ---------------------------------------------------------------------------


class AgentExplainer:
    """Agent de synthèse et d'explication réglementaire.

    Paramètres :
        use_llm : Si True, utilise Qwen 2.5 7B pour la synthèse.
                  Si False (défaut), assemble les textes structurellement.
                  Sur Mac A (16 Go), laisser à False pendant les tests.
    """

    def __init__(self, use_llm: bool = False) -> None:  # noqa: D107
        self.use_llm = use_llm
        self._modele: MLXInference | None = None
        logger.info("AgentExplainer initialisé — use_llm=%s", use_llm)

    # ------------------------------------------------------------------
    # Mode assemblage (sans LLM)
    # ------------------------------------------------------------------

    def _assembler(
        self,
        question: str,  # noqa: ARG002 — argument conservé pour signature contractuelle
        evidences: list[EvidenceRecuperee],
        date_ref: date | None,
        type_pipeline: str,
    ) -> ResultatExplication:
        """Assemblage direct : en-tête + blocs par evidence + avertissement."""
        if not evidences:
            return _resultat_assemblage_vide()
        lignes = [_entete_assemblage(date_ref, type_pipeline)]
        sources_citees: list[str] = []
        _ajouter_blocs_evidences(evidences, lignes, sources_citees)
        _ajouter_reste_et_avertissement(len(evidences), lignes)
        return ResultatExplication(
            reponse="\n".join(lignes),
            sources_citees=sources_citees,
            niveau_confiance=NiveauConfiance.MOYEN,
            mode="assemblage",
        )

    # ------------------------------------------------------------------
    # Mode LLM (Qwen 2.5 7B)
    # ------------------------------------------------------------------

    def _charger_modele(self) -> None:
        """Charge Qwen 2.5 7B via le registre MLX (lazy)."""
        if self._modele is None:
            from src.mlx_utils import get_model

            self._modele = get_model(
                model_name=cfg.modele_explainer,
                temperature=0.1,
            )
            logger.info("Modèle Explainer chargé : %s", cfg.modele_explainer)

    def _construire_contexte(
        self,
        evidences: list[EvidenceRecuperee],
        max_chars: int = 6000,
    ) -> str:
        """Construit le contexte LLM depuis les preuves (borné à `max_chars`)."""
        blocs: list[str] = []
        total = 0
        for ev in evidences:
            bloc = _bloc_source(ev)
            if total + len(bloc) > max_chars:
                blocs.append("... [contexte tronqué pour respecter la limite]")
                break
            blocs.append(bloc)
            total += len(bloc)
        return "\n\n---\n\n".join(blocs)

    def _synthetiser_avec_llm(
        self,
        question: str,
        evidences: list[EvidenceRecuperee],
        date_ref: date | None,
        type_pipeline: str,
    ) -> ResultatExplication:
        """Synthèse LLM (Qwen 2.5 7B) avec repli sur assemblage en cas d'échec."""
        from src.errors import ModelNotLoadedError

        if not evidences:
            logger.warning(
                "Explainer — aucune preuve : réponse INCERTAIN, pas d'appel LLM "
                "(garde-fou anti-réponse non sourcée)"
            )
            return _resultat_assemblage_vide()

        self._charger_modele()
        if self._modele is None:
            raise ModelNotLoadedError("Explainer")
        contexte = self._construire_contexte(evidences)
        sources_citees = _construire_sources_citees(evidences)
        messages = _preparer_messages_synthese(
            question, contexte, date_ref, type_pipeline
        )
        try:
            return self._generer_synthese(messages, sources_citees)
        except Exception:
            logger.exception("Synthèse LLM échouée, bascule sur assemblage")
            return self._assembler(question, evidences, date_ref, type_pipeline)

    def _generer_synthese(
        self, messages: list[dict[str, str]], sources_citees: list[str]
    ) -> ResultatExplication:
        """Appelle le LLM et vérifie que la réponse n'est pas vide."""
        from src.errors import StructuredOutputError

        assert self._modele is not None  # noqa: S101 — invariant garanti par le caller
        resultat = self._modele.generate_avec_messages(
            messages=messages,
            max_tokens=cfg.mlx_max_tokens,
        )
        reponse = resultat.texte.strip()
        if not reponse:
            raise StructuredOutputError("Explainer", detail="réponse vide")
        return ResultatExplication(
            reponse=reponse,
            sources_citees=sources_citees,
            niveau_confiance=NiveauConfiance.ELEVE,
            mode="llm",
        )

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    def expliquer(
        self,
        question: str,
        evidences: list[EvidenceRecuperee],
        date_ref: date | None = None,
        type_pipeline: str = "courante",
    ) -> ResultatExplication:
        """Route vers `_synthetiser_avec_llm` ou `_assembler` selon `self.use_llm`."""
        logger.info(
            "Explication — mode=%s type=%s evidences=%d question=%r",
            "llm" if self.use_llm else "assemblage",
            type_pipeline,
            len(evidences),
            question[:80],
        )
        strategie = self._synthetiser_avec_llm if self.use_llm else self._assembler
        return strategie(
            question=question,
            evidences=evidences,
            date_ref=date_ref,
            type_pipeline=type_pipeline,
        )
