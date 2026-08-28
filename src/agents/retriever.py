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
from typing import Any

from config import cfg
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Filter,
    ScoredPoint,
)
from src.agents.retriever_helpers import (
    filtre_sources,
    filtre_themes,
    filtre_valid_from,
    filtre_valid_to_null,
    filtre_valid_to_present,
    point_vers_evidence,
)
from src.mlx_utils import get_embedding
from src.models import EvidenceRecuperee, SourceReglementaire

logger = logging.getLogger(__name__)


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
            qdrant_client: Client Qdrant injecté (utile pour les tests).
                           Si None, créé depuis cfg.
            top_k: Nombre de chunks à retourner. Défaut : cfg.qdrant_top_k.
        """
        self._client = qdrant_client or QdrantClient(
            host=cfg.qdrant_host,
            port=cfg.qdrant_port,
            https=cfg.qdrant_https,
            api_key=cfg.qdrant_api_key or None,
        )
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
        """Génère l'embedding de la question via MLXEmbedding (bge-m3).

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
    # Recherche Qdrant
    # ------------------------------------------------------------------

    def _rechercher(
        self,
        vecteur: list[float],
        limite: int,
        filtre: Filter | None = None,
    ) -> list[ScoredPoint]:
        """Exécute une recherche vectorielle dans Qdrant.

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
            return resultats.points  # noqa: TRY300 - TODO 12 etape 4/6 : revue ciblee au moment du typage / de l extraction
        except Exception as exc:
            logger.exception("Erreur Qdrant (%s) : %s", self._collection, exc)  # noqa: TRY401 — TODO §12 étape 4 : réviser le message en même temps que le typage
            raise RuntimeError(  # noqa: TRY003 — message ponctuel, taxonomie d'erreurs dédiée à traiter en §8 skill
                f"Qdrant inaccessible (collection={self._collection}) : {exc}"
            ) from exc

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
        """Recherche les passages réglementaires pertinents pour une question.

        Deux passes Qdrant :
          Passe A : valid_from <= date_ref ET valid_to >= date_ref
          Passe B : valid_from <= date_ref ET valid_to = null
        Le budget top_k est réparti équitablement entre les deux passes
        (avec repêchage si l'une des deux manque de candidats), puis le
        résultat final est trié par score décroissant.

        Args:
            question:        Question réglementaire en langage naturel.
            date_contexte:   Date réglementaire de contexte. Si None,
                             utilise la date du jour.
            filtres_themes:  Restreint aux chunks portant au moins un des thèmes.
                             None ou liste vide = pas de filtrage thématique.
            filtres_sources: Restreint aux chunks issus des sources listées.
                             None ou liste vide = pas de filtrage par source.

        Returns:
            Liste d'EvidenceRecuperee triée par score décroissant,
            limitée à top_k éléments. Liste vide si aucun résultat.
        """
        logger.info(
            "Retrieval — question=%r date_contexte=%s top_k=%d themes=%s sources=%s",
            question[:80],
            date_contexte,
            self._top_k,
            filtres_themes or [],
            [s.value for s in (filtres_sources or [])],
        )

        # --- Étape 1 : embedding ---
        try:
            vecteur = self.embed_question(question)
        except RuntimeError as exc:
            logger.exception("Embedding impossible, retrieval annulé : %s", exc)  # noqa: TRY401 — TODO §12 étape 4 : réviser le message en même temps que le typage
            return []

        # --- Étape 2 : date de référence ---
        date_ref = date_contexte or datetime.now(UTC).date()

        # --- Étape 3 : construction des filtres ---
        cond_from = filtre_valid_from(date_ref)
        cond_to_present = filtre_valid_to_present(date_ref)
        cond_to_null = filtre_valid_to_null()

        # Conditions communes aux deux passes (thèmes + sources API)
        conditions_communes: list[Any] = []
        cond_themes = filtre_themes(filtres_themes or [])
        if cond_themes is not None:
            conditions_communes.append(cond_themes)
        cond_sources = filtre_sources(filtres_sources or [])
        if cond_sources is not None:
            conditions_communes.append(cond_sources)

        filtre_passe_a = Filter(must=[cond_from, cond_to_present, *conditions_communes])
        filtre_passe_b = Filter(must=[cond_from, cond_to_null, *conditions_communes])

        # --- Étape 4 : deux passes de recherche ---
        # Sur-échantillonnage à top_k complet par passe : nécessaire pour
        # permettre le repêchage (étape suivante) sans jamais perdre de
        # candidat valide.
        resultats_par_passe: dict[str, list[ScoredPoint]] = {}
        for label, filtre in [
            ("valid_to_present", filtre_passe_a),
            ("valid_to_null", filtre_passe_b),
        ]:
            try:
                resultats = self._rechercher(vecteur, limite=self._top_k, filtre=filtre)
                logger.debug("Passe %s — %d résultats", label, len(resultats))
                resultats_par_passe[label] = resultats
            except RuntimeError as exc:
                logger.warning("Passe %s échouée : %s", label, exc)
                resultats_par_passe[label] = []

        # --- Étape 5 : représentation garantie + arbitrage global par score ---
        # 1) Chaque passe NON VIDE obtient au moins un slot — c'est ce qui
        #    empêche l'éviction complète d'une passe par des scores plus élevés
        #    de l'autre (régression B7).
        # 2) Les slots restants sont distribués aux meilleurs candidats
        #    (toutes passes confondues) — évite qu'un quota rigide n'évince
        #    un candidat à haut score au profit d'un candidat à bas score
        #    dans l'autre passe (B3).
        res_a = resultats_par_passe["valid_to_present"]
        res_b = resultats_par_passe["valid_to_null"]

        points_bruts: list[ScoredPoint] = []
        ids_vus: set[str] = set()

        def _prendre(point: ScoredPoint) -> bool:
            if str(point.id) in ids_vus:
                return False
            ids_vus.add(str(point.id))
            points_bruts.append(point)
            return True

        # Représentation garantie : un point de chaque passe non vide,
        # dans la limite du top_k.
        for source in (res_a, res_b):
            if len(points_bruts) >= self._top_k:
                break
            for point in source:
                if _prendre(point):
                    break

        # Complément par score global : on parcourt les meilleurs candidats
        # restants, toutes passes confondues.
        candidats_restants = sorted(
            list(res_a) + list(res_b),
            key=lambda p: p.score,
            reverse=True,
        )
        for point in candidats_restants:
            if len(points_bruts) >= self._top_k:
                break
            _prendre(point)

        if not points_bruts:
            logger.warning("Aucun chunk trouvé pour : %r", question[:80])
            return []

        points_bruts.sort(key=lambda p: p.score, reverse=True)

        # --- Étape 6 : conversion ---
        evidences: list[EvidenceRecuperee] = []
        for point in points_bruts:
            evidence = point_vers_evidence(point)
            if evidence is not None:
                evidences.append(evidence)

        logger.info(
            "Retrieval terminé — %d/%d chunks retournés",
            len(evidences),
            len(points_bruts),
        )
        return evidences


# _parser_date, _point_vers_evidence et les 5 filtres Qdrant ont été
# extraits vers src/agents/retriever_helpers.py (§12 étape 6). Ré-exportés
# ci-dessus pour l'usage interne du Retriever ; les tests continuent à
# les importer depuis src.agents.retriever_helpers directement.
