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

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Filter,
    ScoredPoint,
)

from config import cfg
from src.agents.retriever_helpers import (
    construire_filtres_passes,
    dedupliquer_evidences,
    extraire_numeros_articles,
    extraire_reglement,
    filtre_articles,
    fusionner_passes,
    point_vers_evidence,
)
from src.mlx_embedding import get_embedding
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


def _verifier_echecs_passes(
    echec_a: Exception | None,
    echec_b: Exception | None,
    repli_disponible: bool,
) -> None:
    """Lève si les deux passes ont échoué sans repli, sinon signale le partiel."""
    if echec_a is not None and echec_b is not None and not repli_disponible:
        logger.error("Les deux passes Qdrant ont échoué — backend indisponible.")
        raise echec_b
    if echec_a is not None or echec_b is not None:
        logger.warning("Une passe Qdrant sur deux a échoué — résultat partiel.")


def _prioriser_articles_cites(
    prioritaires: list[ScoredPoint],
    bruts: list[ScoredPoint],
    top_k: int,
) -> list[ScoredPoint]:
    """Place les chunks de l'article explicitement cité en tête, puis complète."""
    vus: set[str] = set()
    fusion: list[ScoredPoint] = []
    for point in (*prioritaires, *bruts):
        cle = str(point.id)
        if cle in vus:
            continue
        vus.add(cle)
        fusion.append(point)
        if len(fusion) >= top_k:
            break
    return fusion


def _convertir_points_en_evidences(
    points: list[ScoredPoint],
) -> list[EvidenceRecuperee]:
    """Convertit une liste de ScoredPoint en EvidenceRecuperee[] (drop les None)."""
    brutes = [e for e in (point_vers_evidence(p) for p in points) if e is not None]
    evidences = dedupliquer_evidences(brutes)
    logger.info(
        "Retrieval terminé — %d chunks (%d bruts, %d points)",
        len(evidences),
        len(brutes),
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
        """Recherche vectorielle Qdrant (lève VectorStoreError si inaccessible)."""
        try:
            resultats = self._client.query_points(
                collection_name=self._collection,
                query=vecteur,
                query_filter=filtre,
                limit=limite,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:
            from src.errors import VectorStoreError

            logger.exception("Erreur Qdrant (%s)", self._collection)
            raise VectorStoreError(self._collection, cause=str(exc)) from exc
        return resultats.points

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
        """Retrieval en 2 passes temporelles fusionnées ; ≤ `top_k` evidences.

        H5 : l'échec d'embedding (`InferenceError`) et la panne Qdrant
        (`VectorStoreError`, si AUCUNE passe n'a répondu) sont propagés.
        Auparavant, ces deux erreurs étaient converties en `[]` : l'appelant
        affichait « Aucun passage réglementaire pertinent » alors que le
        backend était HS, et le repli `_reponse_retrieval_indisponible`
        de l'orchestrateur devenait inatteignable.

        Raises:
            InferenceError: embedding de la question impossible.
            VectorStoreError: backend vectoriel indisponible.
        """
        _journaliser_debut_retrieval(
            question, date_contexte, self._top_k, filtres_themes, filtres_sources
        )
        vecteur = self.embed_question(question)
        if not vecteur:
            logger.warning("Embedding vide — retrieval annulé pour : %r", question[:80])
            return []
        date_ref = date_contexte or datetime.now(UTC).date()
        return self._fusionner_passes(
            question, vecteur, date_ref, filtres_themes, filtres_sources
        )

    def _fusionner_passes(
        self,
        question: str,
        vecteur: list[float],
        date_ref: date,
        filtres_themes: list[str] | None,
        filtres_sources: list[SourceReglementaire] | None,
    ) -> list[EvidenceRecuperee]:
        """Passe ciblée + 2 passes temporelles, fusion priorisée et conversion."""
        points_articles = self._passe_articles_cites(question, vecteur)
        points_bruts = self._executer_deux_passes(
            vecteur,
            date_ref,
            filtres_themes,
            filtres_sources,
            repli_disponible=bool(points_articles),
        )
        fusionnes = _prioriser_articles_cites(
            points_articles, points_bruts, self._top_k
        )
        if not fusionnes:
            logger.warning("Aucun chunk trouvé pour : %r", question[:80])
            return []
        return _convertir_points_en_evidences(fusionnes)

    def _passe_articles_cites(
        self, question: str, vecteur: list[float]
    ) -> list[ScoredPoint]:
        """Passe ciblée `article_id` si la question cite un ou des numéros.

        Passe d'appoint : une panne Qdrant y est tolérée (log WARNING) tant
        que les passes temporelles répondent ; c'est `_executer_deux_passes`
        qui décide si le backend est globalement HS.
        """
        from src.errors import VectorStoreError

        numeros = extraire_numeros_articles(question)
        reglement = extraire_reglement(question)
        filtre = filtre_articles(numeros, reglement)
        if filtre is None:
            return []
        logger.info(
            "Retrieval — articles %s cités%s",
            numeros,
            f" ({reglement})" if reglement else "",
        )
        try:
            return self._rechercher_passe("articles_cites", vecteur, filtre)
        except VectorStoreError as exc:
            logger.warning("Passe articles cités échouée (passe d'appoint) : %s", exc)
            return []

    def _executer_deux_passes(
        self,
        vecteur: list[float],
        date_ref: date,
        filtres_themes: list[str] | None,
        filtres_sources: list[SourceReglementaire] | None,
        repli_disponible: bool = False,
    ) -> list[ScoredPoint]:
        """Construit les filtres, exécute les 2 passes, retourne la fusion triée.

        Une passe sur deux peut échouer sans interrompre le retrieval : la
        fusion se poursuit avec celle qui a répondu. Si les DEUX passes
        échouent et qu'aucune autre passe n'a ramené de candidat
        (`repli_disponible`), l'erreur est propagée — sans quoi l'API
        afficherait « aucun passage pertinent » alors que Qdrant est HS.
        """
        filtre_a, filtre_b = construire_filtres_passes(
            date_ref,
            filtres_themes or [],
            filtres_sources or [],
        )
        res_a, echec_a = self._passe_toleree("valid_to_present", vecteur, filtre_a)
        res_b, echec_b = self._passe_toleree("valid_to_null", vecteur, filtre_b)
        _verifier_echecs_passes(echec_a, echec_b, repli_disponible)
        return fusionner_passes(res_a, res_b, self._top_k)

    def _passe_toleree(
        self,
        label: str,
        vecteur: list[float],
        filtre: Filter,
    ) -> tuple[list[ScoredPoint], Exception | None]:
        """Exécute une passe en capturant l'échec Qdrant (résultat vide alors)."""
        from src.errors import VectorStoreError

        try:
            resultats = self._rechercher_passe(label, vecteur, filtre)
        except VectorStoreError as exc:
            return [], exc
        return resultats, None

    def _rechercher_passe(
        self,
        label: str,
        vecteur: list[float],
        filtre: Filter,
    ) -> list[ScoredPoint]:
        """Exécute une passe Qdrant (sur-échantillonnée à top_k).

        Raises:
            VectorStoreError: Qdrant injoignable — propagée pour que
                l'appelant distingue « aucun passage pertinent » d'une panne
                de backend (H5).
        """
        resultats = self._rechercher(vecteur, limite=self._top_k, filtre=filtre)
        logger.debug("Passe %s — %d résultats", label, len(resultats))
        return resultats


# _parser_date, _point_vers_evidence et les 5 filtres Qdrant ont été
# extraits vers src/agents/retriever_helpers.py (§12 étape 6). Ré-exportés
# ci-dessus pour l'usage interne du Retriever ; les tests continuent à
# les importer depuis src.agents.retriever_helpers directement.
