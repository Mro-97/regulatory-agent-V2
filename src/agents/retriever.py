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
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from config import cfg
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    DatetimeRange,
    FieldCondition,
    Filter,
    IsNullCondition,
    MatchAny,
    PayloadField,
    ScoredPoint,
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
    # Construction des filtres Qdrant
    # ------------------------------------------------------------------

    def _filtre_valid_from(self, date_ref: date) -> FieldCondition:
        """Condition : valid_from <= date_ref.

        Args:
            date_ref: Date de référence.

        Returns:
            FieldCondition Qdrant.
        """
        return FieldCondition(
            key="valid_from",
            range=DatetimeRange(lte=datetime.combine(date_ref, datetime.min.time())),
        )

    def _filtre_valid_to_present(self, date_ref: date) -> FieldCondition:
        """Condition : valid_to >= date_ref (champ renseigné).

        Args:
            date_ref: Date de référence.

        Returns:
            FieldCondition Qdrant.
        """
        return FieldCondition(
            key="valid_to",
            range=DatetimeRange(gte=datetime.combine(date_ref, datetime.min.time())),
        )

    def _filtre_valid_to_null(self) -> IsNullCondition:
        """Condition : valid_to est nul (version en vigueur indéfiniment).

        Returns:
            IsNullCondition Qdrant — syntaxe correcte pour qdrant-client 1.9+.
        """
        return IsNullCondition(is_null=PayloadField(key="valid_to"))

    def _filtre_themes(self, themes: list[str]) -> FieldCondition | None:
        """Condition : le payload 'themes' (array) contient au moins un des thèmes demandés.

        Args:
            themes: Liste de thèmes autorisés. Vide → aucun filtre.

        Returns:
            FieldCondition Qdrant ou None si la liste est vide.
        """
        themes_valides = [t for t in themes if t]
        if not themes_valides:
            return None
        return FieldCondition(key="themes", match=MatchAny(any=themes_valides))

    def _filtre_sources(
        self, sources: list[SourceReglementaire]
    ) -> FieldCondition | None:
        """Condition : le payload 'source' correspond à l'une des sources demandées.

        Args:
            sources: Liste de sources autorisées. Vide → aucun filtre.

        Returns:
            FieldCondition Qdrant ou None si la liste est vide.
        """
        valeurs = [s.value for s in sources if s is not None]
        if not valeurs:
            return None
        return FieldCondition(key="source", match=MatchAny(any=valeurs))

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
            return resultats.points
        except Exception as exc:
            logger.exception("Erreur Qdrant (%s) : %s", self._collection, exc)
            raise RuntimeError(
                f"Qdrant inaccessible (collection={self._collection}) : {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Conversion des résultats
    # ------------------------------------------------------------------

    def _point_vers_evidence(self, point) -> EvidenceRecuperee | None:
        """Convertit un ScoredPoint Qdrant en EvidenceRecuperee.

        Retourne None si le payload est incomplet, avec log d'avertissement.

        Args:
            point: ScoredPoint retourné par Qdrant.

        Returns:
            EvidenceRecuperee ou None.
        """
        payload = point.payload or {}

        champs_requis = [
            "chunk_id",
            "document_id",
            "article_id",
            "texte_chunk",
            "valid_from",
        ]
        for champ in champs_requis:
            if champ not in payload:
                logger.warning(
                    "Chunk ignoré — champ manquant '%s' dans point.id=%s",
                    champ,
                    point.id,
                )
                return None

        try:
            valid_from = _parser_date(payload["valid_from"])
            valid_to = (
                _parser_date(payload["valid_to"]) if payload.get("valid_to") else None
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
            logger.warning("Conversion échouée pour point.id=%s : %s", point.id, exc)
            return None

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
            logger.exception("Embedding impossible, retrieval annulé : %s", exc)
            return []

        # --- Étape 2 : date de référence ---
        date_ref = date_contexte or datetime.now(UTC).date()

        # --- Étape 3 : construction des filtres ---
        cond_from = self._filtre_valid_from(date_ref)
        cond_to_present = self._filtre_valid_to_present(date_ref)
        cond_to_null = self._filtre_valid_to_null()

        # Conditions communes aux deux passes (thèmes + sources API)
        conditions_communes: list = []
        cond_themes = self._filtre_themes(filtres_themes or [])
        if cond_themes is not None:
            conditions_communes.append(cond_themes)
        cond_sources = self._filtre_sources(filtres_sources or [])
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
            evidence = self._point_vers_evidence(point)
            if evidence is not None:
                evidences.append(evidence)

        logger.info(
            "Retrieval terminé — %d/%d chunks retournés",
            len(evidences),
            len(points_bruts),
        )
        return evidences


# ---------------------------------------------------------------------------
# Utilitaire interne
# ---------------------------------------------------------------------------


def _parser_date(valeur: object) -> date:
    """Parse une valeur de date depuis un payload Qdrant.

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
