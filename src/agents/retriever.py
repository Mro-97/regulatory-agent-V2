"""src/agents/retriever.py — Agent Retriever de Regulatory Agent V2
=================================================================

Responsabilité : recevoir une question, générer son embedding via MLX,
interroger Qdrant et retourner les passages réglementaires pertinents
sous forme d'objets EvidenceRecuperee.

Filtrage temporel en deux passes :
  Passe A — valid_from <= date_ref ET valid_to >= date_ref
  Passe B — valid_from <= date_ref ET valid_to = null (en vigueur indéfiniment)
Les deux passes sont fusionnées avec un budget top_k réparti équitablement
entre elles (repêchage si une passe manque de candidats), pour qu'une
disposition transitoire pertinente (passe A) ne soit jamais totalement
évincée par les dispositions permanentes (passe B) au seul motif d'un score
de similarité légèrement inférieur.

Dépendances : qdrant-client >= 1.9, mlx-lm >= 0.16 (MIT/Apache).
"""  # noqa: D205, D415

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from config import cfg
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Filter,
    ScoredPoint,
)
from src.agents.retriever_helpers import (
    construire_filtres_passes,
    fusionner_passes,
    point_vers_evidence,
)
from src.mlx_utils import get_embedding
from src.models import EvidenceRecuperee, SourceReglementaire

logger = logging.getLogger(__name__)


def _nouveau_client_qdrant() -> QdrantClient:
    """Fabrique un `QdrantClient` avec les paramètres cfg (host/port/https/api_key)."""
    return QdrantClient(
        host=cfg.qdrant_host,
        port=cfg.qdrant_port,
        https=cfg.qdrant_https,
        api_key=cfg.qdrant_api_key or None,
    )


def _journaliser_debut_retrieval(
    question: str,
    date_contexte: date | None,
    top_k: int,
    filtres_themes: list[str] | None,
    filtres_sources: list[SourceReglementaire] | None,
) -> None:
    """Trace un appel `retrieve()` avec filtres et question tronquée."""
    logger.info(
        "Retrieval — question=%r date_contexte=%s top_k=%d themes=%s sources=%s",
        question[:80],
        date_contexte,
        top_k,
        filtres_themes or [],
        [s.value for s in (filtres_sources or [])],
    )


def _convertir_points_en_evidences(
    points: list[ScoredPoint],
) -> list[EvidenceRecuperee]:
    """Convertit une liste de ScoredPoint en EvidenceRecuperee[] (drop les None)."""
    evidences = [e for e in (point_vers_evidence(p) for p in points) if e is not None]
    logger.info(
        "Retrieval terminé — %d/%d chunks retournés",
        len(evidences),
        len(points),
    )
    return evidences


class Retriever:
    """Agent de recherche vectorielle dans Qdrant.

    Cycle d'un appel retrieve() :
      question → embed_question() → deux passes Qdrant
      → fusion → tri par score → EvidenceRecuperee[]
    """

    def __init__(
        self,
        qdrant_client: QdrantClient | None = None,
        top_k: int | None = None,
    ) -> None:
        """Initialise le Retriever sans charger le modèle en mémoire.

        Args:
            qdrant_client: Client injecté (tests) ; sinon créé depuis cfg.
            top_k: Nombre de chunks à retourner. Défaut : cfg.qdrant_top_k.
        """
        self._client = qdrant_client or _nouveau_client_qdrant()
        self._collection = cfg.qdrant_collection
        self._top_k = top_k if top_k is not None else cfg.qdrant_top_k
        logger.info(
            "Retriever initialisé — collection=%s top_k=%d",
            self._collection,
            self._top_k,
        )

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def embed_question(self, question: str) -> list[float]:
        """Génère l'embedding via MLXEmbedding (bge-m3, cache global).

        Raises:
            InferenceError: Chargement modèle ou encodage échoué.
        """
        logger.debug("Génération de l'embedding — question=%r", question[:80])
        modele = get_embedding(cfg.modele_embedding)
        vecteur = modele.encode(question)
        logger.debug("Embedding généré — dimension=%d", len(vecteur))
        return vecteur

    # ------------------------------------------------------------------
    # Recherche Qdrant
    # ------------------------------------------------------------------

    def _rechercher(
        self,
        vecteur: list[float],
        limite: int,
        filtre: Filter | None = None,
    ) -> list[ScoredPoint]:
        """Exécute une recherche vectorielle Qdrant.

        Raises:
            VectorStoreError: Si Qdrant est inaccessible.
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
            return resultats.points  # noqa: TRY300 — sortie normale du bloc try
        except Exception as exc:
            from src.errors import VectorStoreError

            logger.exception("Erreur Qdrant (%s)", self._collection)
            raise VectorStoreError(self._collection, cause=str(exc)) from exc

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    def retrieve(
        self,
        question: str,
        date_contexte: date | None = None,
        filtres_themes: list[str] | None = None,
        filtres_sources: list[SourceReglementaire] | None = None,
    ) -> list[EvidenceRecuperee]:
        """Retrieval en 2 passes temporelles fusionnées ; ≤ `top_k` evidences."""
        _journaliser_debut_retrieval(
            question, date_contexte, self._top_k, filtres_themes, filtres_sources
        )
        vecteur = self._encoder_question_ou_vide(question)
        if not vecteur:
            return []
        date_ref = date_contexte or datetime.now(UTC).date()
        points_bruts = self._executer_deux_passes(
            vecteur, date_ref, filtres_themes, filtres_sources
        )
        if not points_bruts:
            logger.warning("Aucun chunk trouvé pour : %r", question[:80])
            return []
        return _convertir_points_en_evidences(points_bruts)

    def _encoder_question_ou_vide(self, question: str) -> list[float]:
        """Retourne l'embedding, ou une liste vide si l'inférence échoue."""
        from src.errors import InferenceError

        try:
            return self.embed_question(question)
        except InferenceError:
            logger.exception("Embedding impossible, retrieval annulé")
            return []

    def _executer_deux_passes(
        self,
        vecteur: list[float],
        date_ref: date,
        filtres_themes: list[str] | None,
        filtres_sources: list[SourceReglementaire] | None,
    ) -> list[ScoredPoint]:
        """Construit les filtres, exécute les 2 passes, retourne la fusion triée."""
        filtre_a, filtre_b = construire_filtres_passes(
            date_ref,
            filtres_themes or [],
            filtres_sources or [],
        )
        res_a = self._rechercher_passe("valid_to_present", vecteur, filtre_a)
        res_b = self._rechercher_passe("valid_to_null", vecteur, filtre_b)
        return fusionner_passes(res_a, res_b, self._top_k)

    def _rechercher_passe(
        self,
        label: str,
        vecteur: list[float],
        filtre: Filter,
    ) -> list[ScoredPoint]:
        """Exécute une passe Qdrant (sur-échantillonnée à top_k) ; [] sur échec."""
        from src.errors import VectorStoreError

        try:
            resultats = self._rechercher(vecteur, limite=self._top_k, filtre=filtre)
            logger.debug("Passe %s — %d résultats", label, len(resultats))
            return resultats  # noqa: TRY300 — sortie normale du try
        except VectorStoreError as exc:
            logger.warning("Passe %s échouée : %s", label, exc)
            return []


# _parser_date, _point_vers_evidence et les 5 filtres Qdrant ont été
# extraits vers src/agents/retriever_helpers.py (§12 étape 6). Ré-exportés
# ci-dessus pour l'usage interne du Retriever ; les tests continuent à
# les importer depuis src.agents.retriever_helpers directement.
