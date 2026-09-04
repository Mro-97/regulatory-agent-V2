"""tests/test_retriever_articles_cites.py — passe ciblée sur l'article cité.

La recherche vectorielle ne fait pas remonter « que dit l'article 33 »
(la question ne décrit pas le contenu de l'art. 33) : une passe filtrée
sur `article_id` complète, et ses résultats passent en tête.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.agents.retriever import _prioriser_articles_cites
from src.agents.retriever_helpers import (
    extraire_numeros_articles,
    extraire_reglement,
    filtre_articles,
)


def test_extraire_numeros_articles() -> None:
    assert extraire_numeros_articles("que dit l'article 33 du RGPD") == ["33"]
    assert extraire_numeros_articles("art. 5 et art 6, article 5 encore") == ["5", "6"]
    assert extraire_numeros_articles("obligations de notification") == []


def test_extraire_reglement() -> None:
    assert extraire_reglement("que dit l'article 33 du RGPD") == "RGPD_2016_679"
    assert extraire_reglement("article 21 NIS 2") == "NIS2_2022_2555"
    assert extraire_reglement("eIDAS 2 article 5") == "EIDAS2_2024_1183"
    assert extraire_reglement("obligations générales") is None


def test_filtre_articles_formes_versionnees() -> None:
    f = filtre_articles(["33"])
    assert f is not None
    valeurs = f.must[0].match.any
    assert "art_33" in valeurs and "art_33_v1" in valeurs
    assert filtre_articles([]) is None


def test_filtre_articles_ajoute_le_reglement() -> None:
    f = filtre_articles(["33"], "RGPD_2016_679")
    assert f is not None
    assert len(f.must) == 2
    assert f.must[1].match.value == "RGPD_2016_679"


def _pt(pid: str) -> SimpleNamespace:
    return SimpleNamespace(id=pid, score=0.5, payload={})


def test_prioriser_place_les_articles_cites_en_tete() -> None:
    prioritaires = [_pt("art-a"), _pt("art-b")]
    bruts = [_pt("vec-1"), _pt("art-a"), _pt("vec-2")]
    fusion = _prioriser_articles_cites(prioritaires, bruts, top_k=4)
    assert [p.id for p in fusion] == ["art-a", "art-b", "vec-1", "vec-2"]


def test_prioriser_respecte_top_k() -> None:
    fusion = _prioriser_articles_cites(
        [_pt("a"), _pt("b")], [_pt("c"), _pt("d")], top_k=3
    )
    assert [p.id for p in fusion] == ["a", "b", "c"]
