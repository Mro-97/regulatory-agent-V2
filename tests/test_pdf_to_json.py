"""
tests/test_pdf_to_json.py — Tests de scripts/pdf_to_json.py
=============================================================

Couvre la construction du DocumentReglementaire depuis un texte extrait,
en particulier l'attribution des articles à leur chapitre d'origine.
"""

from __future__ import annotations

from datetime import date

from scripts.pdf_to_json import (
    construire_document,
    detecter_articles,
    detecter_chapitres,
)
from src.models import SourceReglementaire

TEXTE_MULTI_CHAPITRES = """CHAPITRE I : Dispositions generales

Article 1
Texte de l'article 1.

Article 2
Texte de l'article 2.

CHAPITRE II : Dispositions particulieres

Article 3
Texte de l'article 3.

Article 4
Texte de l'article 4.
"""


class TestDetectionArticles:
    def test_debut_present_sur_chaque_article(self):  # noqa: ANN201
        articles = detecter_articles(TEXTE_MULTI_CHAPITRES)
        assert len(articles) == 4
        for art in articles:
            assert "debut" in art
        # Les positions doivent être strictement croissantes (ordre du texte).
        debuts = [a["debut"] for a in articles]
        assert debuts == sorted(debuts)


class TestAttributionChapitres:
    def test_articles_attribues_au_bon_chapitre(self):  # noqa: ANN201
        """
        Bug corrigé : auparavant, tous les articles étaient rattachés au
        premier chapitre détecté quelle que soit leur position réelle.
        """
        doc = construire_document(
            texte=TEXTE_MULTI_CHAPITRES,
            doc_id="TEST_MULTI_CHAP",
            titre="Document de test",
            source=SourceReglementaire.AUTRE,
            publication_date=date(2024, 1, 1),
            entry_into_force=date(2024, 1, 1),
            themes=[],
        )

        assert len(doc.chapitres) == 2
        chap_1, chap_2 = doc.chapitres

        ids_chap_1 = [a.id for a in chap_1.articles]
        ids_chap_2 = [a.id for a in chap_2.articles]

        assert ids_chap_1 == ["art_1", "art_2"]
        assert ids_chap_2 == ["art_3", "art_4"]

    def test_articles_avant_premier_chapitre_vont_au_premier(self):  # noqa: ANN201
        """Un article situé avant le premier marqueur de chapitre est rattaché
        au premier chapitre par défaut (aucun chapitre antérieur n'existe)."""
        texte = "Article 1\nTexte préliminaire.\n\nCHAPITRE I : Corps\n\nArticle 2\nTexte 2.\n"  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
        doc = construire_document(
            texte=texte,
            doc_id="TEST_PREAMBULE",
            titre="Document de test",
            source=SourceReglementaire.AUTRE,
            publication_date=date(2024, 1, 1),
            entry_into_force=date(2024, 1, 1),
            themes=[],
        )
        assert len(doc.chapitres) == 1
        assert [a.id for a in doc.chapitres[0].articles] == ["art_1", "art_2"]

    def test_document_sans_chapitre_un_seul_bloc(self):  # noqa: ANN201
        texte = "Article 1\nTexte 1.\n\nArticle 2\nTexte 2.\n"
        assert detecter_chapitres(texte) == []
        doc = construire_document(
            texte=texte,
            doc_id="TEST_SANS_CHAP",
            titre="Document de test",
            source=SourceReglementaire.AUTRE,
            publication_date=date(2024, 1, 1),
            entry_into_force=date(2024, 1, 1),
            themes=[],
        )
        assert len(doc.chapitres) == 1
        assert doc.chapitres[0].id == "chap_principal"
        assert [a.id for a in doc.chapitres[0].articles] == ["art_1", "art_2"]
