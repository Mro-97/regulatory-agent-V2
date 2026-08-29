"""src/agents/temporal.py — Agent Temporel de Regulatory Agent V2
===============================================================

Responsabilité : déterminer quelles preuves (EvidenceRecuperee) sont
applicables à une date donnée, et enrichir chacune d'une explication
temporelle.

Deux niveaux de traitement :

  1. Filtre déterministe (toujours exécuté en premier)
     Règle : valid_from <= date_ref <= valid_to (ou valid_to = None).
     Rapide, fiable à 100 %, ne consomme pas de RAM modèle.

  2. Analyse LLM optionnelle via Qwen 2.5 7B (si use_llm=True)
     Utilisée pour les cas ambigus détectés par le filtre déterministe :
     - chevauchements de versions
     - lacunes temporelles
     - questions où la date est implicite dans le texte
     Le LLM ne modifie jamais le résultat du filtre déterministe —
     il l'annote et signale les ambiguïtés.

Principe fondamental du skill :
  Le LLM ne fait PAS autorité sur les dates. Le filtre déterministe
  est la vérité. Le LLM apporte une explication en langage naturel.

Dépendances : src/mlx_utils.py, src/models.py
"""  # noqa: D205, D415

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from src.models import EvidenceRecuperee, NiveauConfiance

if TYPE_CHECKING:
    from src.mlx_utils import MLXInference

logger = logging.getLogger(__name__)


# Bornes considérées comme raisonnables pour une date réglementaire.
# Empêche de propager silencieusement une date(1, 1, 1) ou une année 9999
# arrivée dans une requête, qui produirait un résultat "INCERTAIN"
# techniquement vrai mais faussement rassurant.
_DATE_MIN_RAISONNABLE = date(1900, 1, 1)
_DATE_MAX_RAISONNABLE = date(2100, 12, 31)


def _valider_date_contexte(valeur: object) -> date | None:
    """Normalise et valide une date de contexte réglementaire.

    Accepte None, un `date`, ou un `datetime` (converti en date UTC).
    Rejette tout autre type et toute date hors [1900-01-01, 2100-12-31].
    Ces bornes sont volontairement larges — elles écartent les valeurs
    aberrantes (année 1 ou 9999) sans contraindre l'usage légitime.
    """
    from src.errors import InvalidContextDateError

    if valeur is None:
        return None
    if isinstance(valeur, datetime):
        valeur = valeur.date()
    if not isinstance(valeur, date):
        raise InvalidContextDateError(
            reason=f"type reçu {type(valeur).__name__}",
            value=valeur,
        )
    if not (_DATE_MIN_RAISONNABLE <= valeur <= _DATE_MAX_RAISONNABLE):
        raise InvalidContextDateError(
            reason=f"{valeur} hors intervalle "
            f"[{_DATE_MIN_RAISONNABLE}, {_DATE_MAX_RAISONNABLE}]",
            value=valeur,
        )
    return valeur


# ---------------------------------------------------------------------------
# Structures de sortie
# ---------------------------------------------------------------------------


@dataclass
class EvidenceTemporelle:
    """Evidence enrichie d'une annotation temporelle.
    Wrappée autour d'EvidenceRecuperee — on ne modifie pas le modèle source.
    """  # noqa: D205

    evidence: EvidenceRecuperee
    applicable: bool
    raison_exclusion: str | None = None
    explication: str | None = None


@dataclass
class ResultatTemporel:
    """Résultat complet de l'agent temporel."""

    date_ref: date
    evidences_applicables: list[EvidenceRecuperee]
    evidences_exclues: list[EvidenceTemporelle]
    chevauchements: list[str] = field(default_factory=list)
    lacunes: list[str] = field(default_factory=list)
    explication_llm: str | None = None
    niveau_confiance: NiveauConfiance = NiveauConfiance.ELEVE


# ---------------------------------------------------------------------------
# Agent Temporel
# ---------------------------------------------------------------------------


class AgentTemporel:
    """Agent de filtrage et de raisonnement temporel.

    Paramètres :
        use_llm : Si True et que le modèle est disponible, utilise Qwen 2.5 7B
                  pour annoter les cas ambigus. Défaut : False (filtre seul).
                  Sur Mac A (16 Go), laisser à False pendant les tests.
    """

    def __init__(self, use_llm: bool = False) -> None:
        """Initialise l'agent sans charger le modèle.

        Args:
            use_llm: Active l'annotation LLM pour les cas ambigus.
        """
        self.use_llm = use_llm
        self._modele: MLXInference | None = None
        logger.info("AgentTemporel initialisé — use_llm=%s", use_llm)

    # ------------------------------------------------------------------
    # Filtrage déterministe — cœur de l'agent
    # ------------------------------------------------------------------

    def filtrer(
        self,
        evidences: list[EvidenceRecuperee],
        date_ref: date,
    ) -> tuple[list[EvidenceRecuperee], list[EvidenceTemporelle]]:
        """Filtre les preuves selon leur intervalle de validité.

        Règle : une preuve est applicable si
            valid_from <= date_ref  ET  (valid_to est None OU valid_to >= date_ref)

        Les deux bornes sont inclusives.

        Args:
            evidences: Liste de preuves issues du Retriever.
            date_ref:  Date réglementaire de référence.

        Returns:
            Tuple (applicables, exclues).
            - applicables : preuves valides à date_ref.
            - exclues     : preuves non valides avec raison d'exclusion.
        """
        applicables: list[EvidenceRecuperee] = []
        exclues: list[EvidenceTemporelle] = []

        for ev in evidences:
            # Borne inférieure
            if ev.valid_from > date_ref:
                raison = (
                    f"Pas encore en vigueur à {date_ref} "
                    f"(entrée en vigueur : {ev.valid_from})"
                )
                exclues.append(
                    EvidenceTemporelle(
                        evidence=ev,
                        applicable=False,
                        raison_exclusion=raison,
                    )
                )
                continue

            # Borne supérieure (None = en vigueur indéfiniment)
            if ev.valid_to is not None and ev.valid_to < date_ref:
                raison = f"Abrogé avant {date_ref} (fin de validité : {ev.valid_to})"
                exclues.append(
                    EvidenceTemporelle(
                        evidence=ev,
                        applicable=False,
                        raison_exclusion=raison,
                    )
                )
                continue

            applicables.append(ev)

        logger.info(
            "Filtre temporel — date_ref=%s : %d applicables, %d exclues sur %d",
            date_ref,
            len(applicables),
            len(exclues),
            len(evidences),
        )
        return applicables, exclues

    # ------------------------------------------------------------------
    # Détection des anomalies
    # ------------------------------------------------------------------

    def detecter_anomalies(
        self,
        evidences: list[EvidenceRecuperee],
    ) -> tuple[list[str], list[str]]:
        """Détecte les chevauchements et lacunes dans les intervalles de validité
        pour un même article (même article_id, document_id identique).

        Ces anomalies sont des problèmes de qualité des données sources —
        elles sont signalées, jamais silencieusement corrigées.

        Args:
            evidences: Preuves applicables après filtrage.

        Returns:
            Tuple (chevauchements, lacunes) — listes de messages descriptifs.
        """  # noqa: D205
        chevauchements: list[str] = []
        lacunes: list[str] = []

        # Regrouper par (document_id, base de l'article_id sans suffixe version)
        groupes: dict[str, list[EvidenceRecuperee]] = {}
        for ev in evidences:
            cle = f"{ev.document_id}:{ev.article_id.split('_')[0]}"
            groupes.setdefault(cle, []).append(ev)

        for cle, groupe in groupes.items():
            if len(groupe) < 2:
                continue

            # Trier par valid_from
            trie = sorted(groupe, key=lambda e: e.valid_from)

            for i in range(len(trie) - 1):
                a, b = trie[i], trie[i + 1]

                # Chevauchement : valid_to de A >= valid_from de B
                if a.valid_to is not None and a.valid_to >= b.valid_from:
                    chevauchements.append(
                        f"{cle} : chevauchement entre [{a.valid_from}→{a.valid_to}] "
                        f"et [{b.valid_from}→{b.valid_to}]"
                    )

                # Lacune : valid_to de A + 1 jour < valid_from de B
                if a.valid_to is not None:
                    from datetime import timedelta

                    lendemain = a.valid_to + timedelta(days=1)
                    if lendemain < b.valid_from:
                        lacunes.append(
                            f"{cle} : lacune du {lendemain} au {b.valid_from}"
                        )

        if chevauchements:
            logger.warning(
                "%d chevauchement(s) détecté(s) : %s",
                len(chevauchements),
                chevauchements,
            )
        if lacunes:
            logger.warning("%d lacune(s) détectée(s) : %s", len(lacunes), lacunes)

        return chevauchements, lacunes

    # ------------------------------------------------------------------
    # Annotation LLM optionnelle
    # ------------------------------------------------------------------

    def _charger_modele(self) -> None:
        """Charge Qwen 2.5 7B via le registre MLX (lazy)."""
        from src.agents.temporal_llm import charger_modele_temporel

        self._modele = charger_modele_temporel(self._modele)

    def _annoter_avec_llm(
        self,
        question: str,
        date_ref: date,
        applicables: list[EvidenceRecuperee],
        exclues: list[EvidenceTemporelle],
        chevauchements: list[str],
        lacunes: list[str],
    ) -> str:
        """Annotation LLM déléguée à src.agents.temporal_llm."""
        from src.agents.temporal_llm import annoter_avec_llm
        from src.errors import ModelNotLoadedError

        self._charger_modele()
        if self._modele is None:
            raise ModelNotLoadedError("Temporal")
        return annoter_avec_llm(
            modele=self._modele,
            question=question,
            date_ref=date_ref,
            applicables=applicables,
            exclues=exclues,
            chevauchements=chevauchements,
            lacunes=lacunes,
        )

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    def analyser(
        self,
        question: str,
        evidences: list[EvidenceRecuperee],
        date_contexte: date | None = None,
    ) -> ResultatTemporel:
        """Analyse temporelle complète d'une liste de preuves.

        Étapes :
        1. Résolution de la date de référence.
        2. Filtre déterministe (toujours exécuté).
        3. Détection des anomalies (chevauchements, lacunes).
        4. Annotation LLM si use_llm=True et anomalies ou cas ambigus.
        5. Calcul du niveau de confiance.

        Args:
            question:       Question originale de l'utilisateur.
            evidences:      Preuves issues du Retriever.
            date_contexte:  Date explicite fournie par l'utilisateur.
                            Si None, utilise la date du jour.

        Returns:
            ResultatTemporel avec les preuves filtrées et annotations.
        """
        # --- Étape 1 : validation puis résolution de la date de référence ---
        date_contexte = _valider_date_contexte(date_contexte)
        date_ref = date_contexte or datetime.now(UTC).date()
        logger.info(
            "Analyse temporelle — date_ref=%s question=%r",
            date_ref,
            question[:80],
        )

        if not evidences:
            logger.warning("Aucune preuve à analyser.")
            return ResultatTemporel(
                date_ref=date_ref,
                evidences_applicables=[],
                evidences_exclues=[],
                niveau_confiance=NiveauConfiance.INCERTAIN,
            )

        # --- Étape 2 : filtre déterministe ---
        applicables, exclues = self.filtrer(evidences, date_ref)

        # --- Étape 3 : détection des anomalies ---
        chevauchements, lacunes = self.detecter_anomalies(applicables)

        # --- Étape 4 : niveau de confiance ---
        if chevauchements:
            confiance = NiveauConfiance.FAIBLE
        elif lacunes:
            confiance = NiveauConfiance.MOYEN
        elif not applicables:
            confiance = NiveauConfiance.INCERTAIN
        else:
            confiance = NiveauConfiance.ELEVE

        # --- Étape 5 : annotation LLM si activée ---
        explication_llm: str | None = None
        if self.use_llm and (chevauchements or lacunes or not applicables):
            logger.info("Cas ambigu détecté — annotation LLM activée.")
            explication_llm = self._annoter_avec_llm(
                question=question,
                date_ref=date_ref,
                applicables=applicables,
                exclues=exclues,
                chevauchements=chevauchements,
                lacunes=lacunes,
            )
        elif self.use_llm:
            # Cas simple mais LLM demandé — explication courte
            explication_llm = self._annoter_avec_llm(
                question=question,
                date_ref=date_ref,
                applicables=applicables,
                exclues=exclues,
                chevauchements=[],
                lacunes=[],
            )

        return ResultatTemporel(
            date_ref=date_ref,
            evidences_applicables=applicables,
            evidences_exclues=exclues,
            chevauchements=chevauchements,
            lacunes=lacunes,
            explication_llm=explication_llm,
            niveau_confiance=confiance,
        )
