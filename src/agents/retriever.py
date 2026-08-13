"""
src/agents/retriever.py — Agent Retriever de Regulatory Agent V2
=================================================================

Responsabilité : recevoir une question, générer son embedding via MLX,
interroger Qdrant et retourner les passages réglementaires pertinents
sous forme d'objets EvidenceRecuperee.

Filtrage temporel en deux passes :
  Passe A — valid_from <= date_ref ET valid_to >= date_ref
  Passe B — valid_from <= date_ref ET valid_to = null (en vigueur indéfiniment)
Les deux passes sont fusionnées et triées par score.

Dépendances : qdrant-client >= 1.9, mlx-lm >= 0.16 (MIT/Apache).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    DatetimeRange,
    FieldCondition,
    Filter,
    IsNullCondition,
    PayloadField,
)

from config import cfg
from src.models import EvidenceRecuperee, SourceReglementaire
from src.mlx_utils import get_embedding

logger = logging.getLogger(__name__)


class Retriever:
    """
    Agent de recherche vectorielle dans Qdrant.

    Cycle d'un appel retrieve() :
      question → embed_question() → deux passes Qdrant
      → fusion → tri par score → EvidenceRecuperee[]
    """

    def __init__(
        self,
        qdrant_client: Optional[QdrantClient] = None,
        top_k: Optional[int] = None,
    ) -> None:
        """
        Initialise le Retriever sans charger le modèle en mémoire.

        Args:
            qdrant_client: Client Qdrant injecté (utile pour les tests).
                           Si None, créé depuis cfg.
            top_k: Nombre de chunks à retourner. Défaut : cfg.qdrant_top_k.
        """
        self._client = qdrant_client or QdrantClient(
            host=cfg.qdrant_host,
            port=cfg.qdrant_port,
        )
        self._collection = cfg.qdrant_collection
        self._top_k = top_k if top_k is not None else cfg.qdrant_top_k
        logger.info(
            "Retriever initialisé — collection=%s top_k=%d",
            self._collection, self._top_k,
        )

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def embed_question(self, question: str) -> list[float]:
        """
        Génère l'embedding de la question via MLXEmbedding (bge-m3).

        Utilise get_embedding() pour bénéficier du lazy loading et du cache
        global. Le modèle reste chargé en permanence sur Mac B.

        Args:
            question: Texte de la question à encoder.

        Returns:
            Vecteur d'embedding normalisé (dimension 1024 pour bge-m3).

        Raises:
            RuntimeError: Si le modèle ne peut pas être chargé.
        """
        logger.debug("Génération de l'embedding — question=%r", question[:80])
        modele = get_embedding(cfg.modele_embedding)
        vecteur = modele.encode(question)
        logger.debug("Embedding généré — dimension=%d", len(vecteur))
        return vecteur

    # ------------------------------------------------------------------
    # Construction des filtres Qdrant
    # ------------------------------------------------------------------

    def _filtre_valid_from(self, date_ref: date) -> FieldCondition:
        """
        Condition : valid_from <= date_ref.

        Args:
            date_ref: Date de référence.

        Returns:
            FieldCondition Qdrant.
        """
        return FieldCondition(
            key="valid_from",
            range=DatetimeRange(
                lte=datetime.combine(date_ref, datetime.min.time())
            ),
        )

    def _filtre_valid_to_present(self, date_ref: date) -> FieldCondition:
        """
        Condition : valid_to >= date_ref (champ renseigné).

        Args:
            date_ref: Date de référence.

        Returns:
            FieldCondition Qdrant.
        """
        return FieldCondition(
            key="valid_to",
            range=DatetimeRange(
                gte=datetime.combine(date_ref, datetime.min.time())
            ),
        )

    def _filtre_valid_to_null(self) -> IsNullCondition:
        """
        Condition : valid_to est nul (version en vigueur indéfiniment).

        Returns:
            IsNullCondition Qdrant — syntaxe correcte pour qdrant-client 1.9+.
        """
        return IsNullCondition(is_null=PayloadField(key="valid_to"))

    # ------------------------------------------------------------------
    # Recherche Qdrant
    # ------------------------------------------------------------------

    def _rechercher(
        self,
        vecteur: list[float],
        limite: int,
        filtre: Optional[Filter] = None,
    ) -> list:
        """
        Exécute une recherche vectorielle dans Qdrant.

        Args:
            vecteur: Vecteur de la requête.
            limite:  Nombre maximum de résultats.
            filtre:  Filtre Qdrant optionnel.

        Returns:
            Liste de ScoredPoint retournés par Qdrant.

        Raises:
            RuntimeError: Si Qdrant est inaccessible.
        """
        try:
            resultats = self._client.query_points(
                collection_name=self._collection,
                query=vecteur,
                query_filter=filtre,
                limit=limite,
                with_payload=True,
                with_vectors=False,
            )
            return resultats.points
        except Exception as exc:
            logger.error("Erreur Qdrant (%s) : %s", self._collection, exc)
            raise RuntimeError(
                f"Qdrant inaccessible (collection={self._collection}) : {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Conversion des résultats
    # ------------------------------------------------------------------

    def _point_vers_evidence(self, point) -> Optional[EvidenceRecuperee]:
        """
        Convertit un ScoredPoint Qdrant en EvidenceRecuperee.

        Retourne None si le payload est incomplet, avec log d'avertissement.

        Args:
            point: ScoredPoint retourné par Qdrant.

        Returns:
            EvidenceRecuperee ou None.
        """
        payload = point.payload or {}

        champs_requis = ["chunk_id", "document_id", "article_id", "texte_chunk", "valid_from"]
        for champ in champs_requis:
            if champ not in payload:
                logger.warning(
                    "Chunk ignoré — champ manquant '%s' dans point.id=%s",
                    champ, point.id,
                )
                return None

        try:
            valid_from = _parser_date(payload["valid_from"])
            valid_to = (
                _parser_date(payload["valid_to"])
                if payload.get("valid_to") else None
            )

            return EvidenceRecuperee(
                chunk_id=str(payload["chunk_id"]),
                document_id=str(payload["document_id"]),
                article_id=str(payload["article_id"]),
                texte_extrait=str(payload["texte_chunk"]),
                score_similarite=round(float(point.score), 4),
                valid_from=valid_from,
                valid_to=valid_to,
            )

        except Exception as exc:
            logger.warning(
                "Conversion échouée pour point.id=%s : %s", point.id, exc
            )
            return None

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    def retrieve(
        self,
        question: str,
        date_contexte: Optional[date] = None,
    ) -> list[EvidenceRecuperee]:
        """
        Recherche les passages réglementaires pertinents pour une question.

        Deux passes Qdrant :
          Passe A : valid_from <= date_ref ET valid_to >= date_ref
          Passe B : valid_from <= date_ref ET valid_to = null
        Les résultats sont fusionnés (dédupliqués) et triés par score.

        Args:
            question:      Question réglementaire en langage naturel.
            date_contexte: Date réglementaire de contexte. Si None,
                           utilise la date du jour.

        Returns:
            Liste d'EvidenceRecuperee triée par score décroissant,
            limitée à top_k éléments. Liste vide si aucun résultat.
        """
        logger.info(
            "Retrieval — question=%r date_contexte=%s top_k=%d",
            question[:80], date_contexte, self._top_k,
        )

        # --- Étape 1 : embedding ---
        try:
            vecteur = self.embed_question(question)
        except RuntimeError as exc:
            logger.error("Embedding impossible, retrieval annulé : %s", exc)
            return []

        # --- Étape 2 : date de référence ---
        date_ref = date_contexte or datetime.now(timezone.utc).date()

        # --- Étape 3 : construction des filtres ---
        cond_from = self._filtre_valid_from(date_ref)
        cond_to_present = self._filtre_valid_to_present(date_ref)
        cond_to_null = self._filtre_valid_to_null()

        filtre_passe_a = Filter(must=[cond_from, cond_to_present])
        filtre_passe_b = Filter(must=[cond_from, cond_to_null])

        # --- Étape 4 : deux passes de recherche ---
        points_bruts: list = []
        ids_vus: set[str] = set()

        for label, filtre in [
            ("valid_to_present", filtre_passe_a),
            ("valid_to_null", filtre_passe_b),
        ]:
            try:
                resultats = self._rechercher(vecteur, limite=self._top_k, filtre=filtre)
                logger.debug("Passe %s — %d résultats", label, len(resultats))
                for point in resultats:
                    if str(point.id) not in ids_vus:
                        ids_vus.add(str(point.id))
                        points_bruts.append(point)
            except RuntimeError as exc:
                logger.warning("Passe %s échouée : %s", label, exc)

        if not points_bruts:
            logger.warning("Aucun chunk trouvé pour : %r", question[:80])
            return []

        # --- Étape 5 : tri et limitation ---
        points_bruts.sort(key=lambda p: p.score, reverse=True)
        points_bruts = points_bruts[: self._top_k]

        # --- Étape 6 : conversion ---
        evidences: list[EvidenceRecuperee] = []
        for point in points_bruts:
            evidence = self._point_vers_evidence(point)
            if evidence is not None:
                evidences.append(evidence)

        logger.info(
            "Retrieval terminé — %d/%d chunks retournés",
            len(evidences), len(points_bruts),
        )
        return evidences


# ---------------------------------------------------------------------------
# Utilitaire interne
# ---------------------------------------------------------------------------


def _parser_date(valeur: object) -> date:
    """
    Parse une valeur de date depuis un payload Qdrant.

    Accepte : str ISO 8601, datetime, date.

    Args:
        valeur: Valeur brute du payload.

    Returns:
        Objet date Python.

    Raises:
        ValueError: Si la valeur ne peut pas être parsée.
    """
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    if isinstance(valeur, str):
        return date.fromisoformat(valeur[:10])
    raise ValueError(f"Impossible de parser la date : {valeur!r}")
