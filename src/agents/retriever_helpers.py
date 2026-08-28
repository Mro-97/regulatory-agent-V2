"""src/agents/retriever_helpers.py — Filtres Qdrant et conversion payload.

Extraits de src/agents/retriever.py (§12 étape 6). Regroupe les
constructions de FieldCondition/IsNullCondition, la conversion d'un
ScoredPoint en EvidenceRecuperee et le parsing des dates de payload.
Le Retriever ne conserve que l'orchestration (embedding, deux passes,
fusion, tri).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING

from qdrant_client.http.models import (
    DatetimeRange,
    FieldCondition,
    Filter,
    IsNullCondition,
    MatchAny,
    PayloadField,
)
from src.models import EvidenceRecuperee

if TYPE_CHECKING:
    from typing import Any

    from qdrant_client.http.models import ScoredPoint
    from src.models import SourceReglementaire

logger = logging.getLogger(__name__)


def filtre_valid_from(date_ref: date) -> FieldCondition:
    """Condition Qdrant : valid_from <= date_ref."""
    return FieldCondition(
        key="valid_from",
        range=DatetimeRange(lte=datetime.combine(date_ref, datetime.min.time())),
    )


def filtre_valid_to_present(date_ref: date) -> FieldCondition:
    """Condition Qdrant : valid_to >= date_ref (champ renseigné)."""
    return FieldCondition(
        key="valid_to",
        range=DatetimeRange(gte=datetime.combine(date_ref, datetime.min.time())),
    )


def filtre_valid_to_null() -> IsNullCondition:
    """Condition Qdrant : valid_to est nul (en vigueur indéfiniment)."""
    return IsNullCondition(is_null=PayloadField(key="valid_to"))


def filtre_themes(themes: list[str]) -> FieldCondition | None:
    """Condition : le payload 'themes' contient au moins un des thèmes demandés."""
    themes_valides = [t for t in themes if t]
    if not themes_valides:
        return None
    return FieldCondition(key="themes", match=MatchAny(any=themes_valides))


def filtre_sources(sources: list[SourceReglementaire]) -> FieldCondition | None:
    """Condition : le payload 'source' correspond à l'une des sources demandées."""
    valeurs = [s.value for s in sources if s is not None]
    if not valeurs:
        return None
    return FieldCondition(key="source", match=MatchAny(any=valeurs))


def parser_date(valeur: object) -> date:
    """Parse une valeur de date depuis un payload Qdrant.

    Accepte : str ISO 8601, datetime, date.

    Raises:
        ValueError: Si la valeur ne peut pas être parsée.
    """
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    if isinstance(valeur, str):
        return date.fromisoformat(valeur[:10])
    raise ValueError(f"Impossible de parser la date : {valeur!r}")  # noqa: TRY003 — message ponctuel, taxonomie d'erreurs dédiée à traiter en §8 skill


def construire_filtres_passes(
    date_ref: date,
    filtres_themes_liste: list[str],
    filtres_sources_liste: list[SourceReglementaire],
) -> tuple[Filter, Filter]:
    """Construit les deux filtres Qdrant du retrieval temporel à deux passes.

    Passe A : `valid_from <= date_ref ET valid_to >= date_ref`
    Passe B : `valid_from <= date_ref ET valid_to = null`

    Ajoute aux deux passes les conditions communes optionnelles (thèmes,
    sources).

    Returns:
        Tuple (filtre_passe_a, filtre_passe_b).
    """
    cond_from = filtre_valid_from(date_ref)
    cond_to_present = filtre_valid_to_present(date_ref)
    cond_to_null = filtre_valid_to_null()

    conditions_communes: list[Any] = []
    cond_themes = filtre_themes(filtres_themes_liste)
    if cond_themes is not None:
        conditions_communes.append(cond_themes)
    cond_sources = filtre_sources(filtres_sources_liste)
    if cond_sources is not None:
        conditions_communes.append(cond_sources)

    filtre_passe_a = Filter(must=[cond_from, cond_to_present, *conditions_communes])
    filtre_passe_b = Filter(must=[cond_from, cond_to_null, *conditions_communes])
    return filtre_passe_a, filtre_passe_b


def fusionner_passes(
    res_a: list[ScoredPoint],
    res_b: list[ScoredPoint],
    top_k: int,
) -> list[ScoredPoint]:
    """Fusionne les résultats des deux passes retrieval avec représentation
    garantie puis arbitrage global par score.

    1) Chaque passe non vide obtient au moins un slot (empêche l'éviction
       complète d'une passe par des scores plus élevés de l'autre — B7).
    2) Les slots restants sont distribués aux meilleurs candidats toutes
       passes confondues (évite qu'un quota rigide n'évince un candidat
       à haut score au profit d'un candidat à bas score — B3).
    3) Le résultat final est trié par score décroissant.

    Returns:
        Liste de ScoredPoint sans doublon, ≤ `top_k` éléments.
    """  # noqa: D205 — TODO §12 étape 4 : compléter docstrings
    points_bruts: list[ScoredPoint] = []
    ids_vus: set[str] = set()

    def _prendre(point: ScoredPoint) -> bool:
        """Ajoute `point` à la sélection si son id n'a pas déjà été retenu."""
        if str(point.id) in ids_vus:
            return False
        ids_vus.add(str(point.id))
        points_bruts.append(point)
        return True

    for source in (res_a, res_b):
        if len(points_bruts) >= top_k:
            break
        for point in source:
            if _prendre(point):
                break

    candidats_restants = sorted(
        list(res_a) + list(res_b),
        key=lambda p: p.score,
        reverse=True,
    )
    for point in candidats_restants:
        if len(points_bruts) >= top_k:
            break
        _prendre(point)

    points_bruts.sort(key=lambda p: p.score, reverse=True)
    return points_bruts


def point_vers_evidence(point: ScoredPoint) -> EvidenceRecuperee | None:
    """Convertit un ScoredPoint Qdrant en EvidenceRecuperee.

    Retourne None si le payload est incomplet, avec log d'avertissement.
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
        valid_from = parser_date(payload["valid_from"])
        valid_to = parser_date(payload["valid_to"]) if payload.get("valid_to") else None

        return EvidenceRecuperee(
            chunk_id=str(payload["chunk_id"]),
            document_id=str(payload["document_id"]),
            article_id=str(payload["article_id"]),
            texte_extrait=str(payload["texte_chunk"]),
            score_similarite=round(float(point.score), 4),
            valid_from=valid_from,
            valid_to=valid_to,
        )

    except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
        logger.warning("Conversion échouée pour point.id=%s : %s", point.id, exc)
        return None
