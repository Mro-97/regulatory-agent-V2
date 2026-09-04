"""src/agents/retriever_helpers.py — Filtres Qdrant et conversion payload.

Extraits de src/agents/retriever.py (§12 étape 6). Regroupe les
constructions de FieldCondition/IsNullCondition, la conversion d'un
ScoredPoint en EvidenceRecuperee et le parsing des dates de payload.
Le Retriever ne conserve que l'orchestration (embedding, deux passes,
fusion, tri).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import TYPE_CHECKING

from qdrant_client.http.models import (
    DatetimeRange,
    FieldCondition,
    Filter,
    IsNullCondition,
    MatchAny,
    MatchValue,
    PayloadField,
)

from src.models import EvidenceRecuperee

if TYPE_CHECKING:
    from qdrant_client.http.models import ScoredPoint

    from src.models import SourceReglementaire

from typing import Any

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
    from src.errors import PayloadDateParseError

    raise PayloadDateParseError(valeur)


def construire_filtres_passes(
    date_ref: date,
    filtres_themes_liste: list[str],
    filtres_sources_liste: list[SourceReglementaire],
) -> tuple[Filter, Filter]:
    """Construit les deux filtres Qdrant du retrieval temporel à deux passes.

    Passe A : `valid_from <= date_ref ET valid_to >= date_ref`.
    Passe B : `valid_from <= date_ref ET valid_to = null`.
    Ajoute aux deux les conditions communes (thèmes, sources) si présentes.
    """
    cond_from = filtre_valid_from(date_ref)
    communes = _conditions_communes(filtres_themes_liste, filtres_sources_liste)
    filtre_passe_a = Filter(
        must=[cond_from, filtre_valid_to_present(date_ref), *communes]
    )
    filtre_passe_b = Filter(must=[cond_from, filtre_valid_to_null(), *communes])
    return filtre_passe_a, filtre_passe_b


def _conditions_communes(
    filtres_themes_liste: list[str],
    filtres_sources_liste: list[SourceReglementaire],
) -> list[Any]:
    """Conditions Qdrant optionnelles (thèmes, sources) partagées entre passes."""
    conditions: list[Any] = []
    cond_themes = filtre_themes(filtres_themes_liste)
    if cond_themes is not None:
        conditions.append(cond_themes)
    cond_sources = filtre_sources(filtres_sources_liste)
    if cond_sources is not None:
        conditions.append(cond_sources)
    return conditions


def fusionner_passes(
    res_a: list[ScoredPoint],
    res_b: list[ScoredPoint],
    top_k: int,
) -> list[ScoredPoint]:
    """Fusion des 2 passes : représentation garantie + complément par score global."""
    points_bruts: list[ScoredPoint] = []
    ids_vus: set[str] = set()
    _garantir_representation(res_a, res_b, points_bruts, ids_vus, top_k)
    _completer_par_score_global(res_a, res_b, points_bruts, ids_vus, top_k)
    points_bruts.sort(key=lambda p: p.score, reverse=True)
    return points_bruts


def _garantir_representation(
    res_a: list[ScoredPoint],
    res_b: list[ScoredPoint],
    points_bruts: list[ScoredPoint],
    ids_vus: set[str],
    top_k: int,
) -> None:
    """Réserve au moins un slot à chaque passe non vide (empêche l'éviction B7)."""
    for source in (res_a, res_b):
        if len(points_bruts) >= top_k:
            break
        for point in source:
            if _prendre_point(point, points_bruts, ids_vus):
                break


def _completer_par_score_global(
    res_a: list[ScoredPoint],
    res_b: list[ScoredPoint],
    points_bruts: list[ScoredPoint],
    ids_vus: set[str],
    top_k: int,
) -> None:
    """Complète jusqu'à `top_k` en piochant les meilleurs candidats des 2 passes."""
    candidats = sorted(list(res_a) + list(res_b), key=lambda p: p.score, reverse=True)
    for point in candidats:
        if len(points_bruts) >= top_k:
            break
        _prendre_point(point, points_bruts, ids_vus)


def _prendre_point(
    point: ScoredPoint,
    points_bruts: list[ScoredPoint],
    ids_vus: set[str],
) -> bool:
    """Ajoute `point` à la sélection si son id n'a pas déjà été retenu."""
    if str(point.id) in ids_vus:
        return False
    ids_vus.add(str(point.id))
    points_bruts.append(point)
    return True


_RE_ARTICLE = re.compile(r"\bart(?:icle|\.)?\s*(\d{1,4})\b", re.IGNORECASE)

# Mots-clés de règlement → document_id exact du corpus. Clés triées du plus
# long au plus court à l'usage (« eidas 2 » avant « eidas », etc.).
_REGLEMENTS: dict[str, str] = {
    "data governance act": "DGA_2022_868",
    "cyber resilience act": "CRA_2024_2847",
    "cybersecurity act": "CSA_2019_881",
    "règlement ia": "AI_ACT_2024_1689",
    "reglement ia": "AI_ACT_2024_1689",
    "ai act": "AI_ACT_2024_1689",
    "data act": "DATA_ACT_2023_2854",
    "eidas 2": "EIDAS2_2024_1183",
    "eidas2": "EIDAS2_2024_1183",
    "eprivacy": "EPRIVACY_2002_58",
    "eidas": "EIDAS_2014_910",
    "nis 2": "NIS2_2022_2555",
    "nis2": "NIS2_2022_2555",
    "rgpd": "RGPD_2016_679",
    "gdpr": "RGPD_2016_679",
    "dora": "DORA_2022_2554",
    "dga": "DGA_2022_868",
    "cra": "CRA_2024_2847",
    "cer": "CER_2022_2557",
}


def extraire_numeros_articles(question: str) -> list[str]:
    """Numéros d'article cités dans la question (« article 33 », « art. 5 »)."""
    vus: dict[str, None] = {}
    for m in _RE_ARTICLE.finditer(question or ""):
        vus.setdefault(m.group(1), None)
    return list(vus)


def extraire_reglement(question: str) -> str | None:
    """`document_id` du règlement nommé dans la question, sinon None."""
    minuscule = (question or "").lower()
    for cle in sorted(_REGLEMENTS, key=len, reverse=True):
        if cle in minuscule:
            return _REGLEMENTS[cle]
    return None


def filtre_articles(
    numeros: list[str], document_id: str | None = None
) -> Filter | None:
    """Filtre Qdrant sur `article_id ∈ {art_N, art_N_v1..v3}`, +`document_id`.

    La recherche vectorielle seule ne fait pas remonter « article 33 »
    quand la question n'en décrit pas le contenu ; cette passe cible
    l'`article_id` exact — et le règlement quand il est nommé.
    """
    if not numeros:
        return None
    valeurs = [
        forme
        for n in numeros
        for forme in (f"art_{n}", f"art_{n}_v1", f"art_{n}_v2", f"art_{n}_v3")
    ]
    must = [FieldCondition(key="article_id", match=MatchAny(any=valeurs))]
    if document_id:
        must.append(
            FieldCondition(key="document_id", match=MatchValue(value=document_id))
        )
    return Filter(must=must)


def dedupliquer_evidences(
    evidences: list[EvidenceRecuperee],
) -> list[EvidenceRecuperee]:
    """Collapse les chunks identiques (doc + article + texte), garde le meilleur score.

    La collection Qdrant contient des doublons d'ingestion (le même
    passage réinséré à chaque run). Sans ce filtre, un unique passage
    occupe plusieurs slots du top-k et fausse la moyenne de confiance de
    l'Explainer. Résultat trié par score décroissant.
    """
    par_cle: dict[tuple[str, str, str], EvidenceRecuperee] = {}
    for ev in evidences:
        cle = (ev.document_id, ev.article_id, ev.texte_extrait)
        gardee = par_cle.get(cle)
        if gardee is None or (ev.score_similarite or 0.0) > (
            gardee.score_similarite or 0.0
        ):
            par_cle[cle] = ev
    return sorted(
        par_cle.values(), key=lambda e: e.score_similarite or 0.0, reverse=True
    )


def point_vers_evidence(point: ScoredPoint) -> EvidenceRecuperee | None:
    """Convertit un ScoredPoint en EvidenceRecuperee (None si payload incomplet)."""
    payload = point.payload or {}
    if not _payload_a_champs_requis(payload, point.id):
        return None
    try:
        return _construire_evidence_depuis_payload(payload, point)
    except Exception as exc:  # noqa: BLE001 — frontière externe : cf. skill §8
        logger.warning("Conversion échouée pour point.id=%s : %s", point.id, exc)
        return None


_CHAMPS_REQUIS_PAYLOAD = [
    "chunk_id",
    "document_id",
    "article_id",
    "texte_chunk",
    "valid_from",
]


def _payload_a_champs_requis(payload: dict[str, Any], point_id: Any) -> bool:
    """True si tous les `_CHAMPS_REQUIS_PAYLOAD` sont présents (log WARNING sinon)."""
    for champ in _CHAMPS_REQUIS_PAYLOAD:
        if champ not in payload:
            logger.warning(
                "Chunk ignoré — champ manquant '%s' dans point.id=%s",
                champ,
                point_id,
            )
            return False
    return True


def _construire_evidence_depuis_payload(
    payload: dict[str, Any],
    point: ScoredPoint,
) -> EvidenceRecuperee:
    """Assemble une EvidenceRecuperee depuis un payload Qdrant validé."""
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
