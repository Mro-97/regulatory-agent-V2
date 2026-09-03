"""tests/test_ingest_min_chunk.py — les micro-chunks ne sont pas indexés.

Constat terrain : des fragments « paragraphe 3 » / « — Article 33 »
(12-25 caractères, issus de renvois croisés mal découpés) remontaient
dans le retrieval et noyaient les vrais articles.
"""

from __future__ import annotations

import pytest
from config import cfg
from scripts.ingest import Ingester
from src.models import (
    Chapitre,
    DocumentReglementaire,
    IntervalleValidite,
    SourceReglementaire,
    VersionArticle,
)


def _doc(*textes: str) -> DocumentReglementaire:
    articles = [
        VersionArticle(
            id=f"art_{i}",
            titre=f"Article {i}",
            texte=t,
            validite=IntervalleValidite(valid_from="2018-05-25"),
        )
        for i, t in enumerate(textes, 1)
    ]
    return DocumentReglementaire(
        id="DOC_TEST_2024_1",
        titre="Doc test",
        source=SourceReglementaire.EUR_LEX,
        publication_date="2024-01-01",
        entry_into_force="2024-01-01",
        version="2024-01-01",
        chapitres=[Chapitre(id="c1", titre="C1", articles=articles)],
    )


def test_chunk_court_ecarte(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "ingest_taille_min_chunk", 80)
    ing = Ingester.__new__(Ingester)
    chunks = ing.chunk_document(_doc("paragraphe 3", "Texte long " * 20))
    articles = {c.article_id for c in chunks}
    assert "art_1" not in articles
    assert "art_2" in articles


def test_seuil_zero_desactive_le_filtre(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "ingest_taille_min_chunk", 0)
    ing = Ingester.__new__(Ingester)
    chunks = ing.chunk_document(_doc("x", "y"))
    assert {c.article_id for c in chunks} == {"art_1", "art_2"}
