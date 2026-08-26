"""
tests/test_bug1_pdf_chapitres.py — B1

Cas non couverts par test_pdf_to_json.py où l'attribution finissait par
mettre tous les articles au premier chapitre détecté :

  a) TOC positionnée après les articles (les marqueurs CHAPITRE viennent
     après tous les articles → fallback `chap_ids[0]` capture tout).

  b) Marqueurs de chapitre répétés (headers de page) → le dict comprehension
     déduplique par id, mais la boucle finale itère toujours sur
     `chapitres_bruts` avec doublons → doublons de Chapitre dans la sortie.
"""

from __future__ import annotations

from datetime import date

from scripts.pdf_to_json import construire_document
from src.models import SourceReglementaire


TEXTE_TOC_A_LA_FIN = """Article 1
Texte de l'article 1.

Article 2
Texte de l'article 2.

Article 3
Texte de l'article 3.

TABLE DES MATIÈRES
CHAPITRE I : Introduction
CHAPITRE II : Développement
CHAPITRE III : Conclusion
"""


TEXTE_CHAPITRES_REPETES = """CHAPITRE I : Titre
Article 1
Texte 1.

CHAPITRE I : Titre
Article 2
Texte 2.

CHAPITRE II : Titre
Article 3
Texte 3.

CHAPITRE II : Titre
Article 4
Texte 4.
"""


def _doc(texte: str, doc_id: str):
    return construire_document(
        texte=texte,
        doc_id=doc_id,
        titre=doc_id,
        source=SourceReglementaire.AUTRE,
        publication_date=date(2024, 1, 1),
        entry_into_force=date(2024, 1, 1),
        themes=[],
    )


class TestTOCAprèsArticles:
    """Si tous les marqueurs de chapitre suivent tous les articles, ils
    ressemblent à une table des matières et ne doivent pas induire de
    fausse partition. On tombe alors en mode chapitre unique.
    """

    def test_toc_a_la_fin_regroupe_en_chapitre_principal(self):
        doc = _doc(TEXTE_TOC_A_LA_FIN, "TEST_TOC_END")
        # Attendu : un seul chapitre, 3 articles.
        assert len(doc.chapitres) == 1, (
            f"Attendu 1 chapitre, obtenu {len(doc.chapitres)}: "
            f"{[c.id for c in doc.chapitres]}"
        )
        assert [a.id for a in doc.chapitres[0].articles] == ["art_1", "art_2", "art_3"]
        # Le chapitre unique ne doit PAS porter le nom d'une entrée TOC.
        assert doc.chapitres[0].id not in {"chap_i", "chap_ii", "chap_iii"}, (
            f"Chapitre labellé '{doc.chapitres[0].id}' — c'est une entrée TOC, "
            "pas une vraie section du corps"
        )


class TestChapitresRépétés:
    """Un marqueur CHAPITRE répété (typique d'un header de page PDF) ne
    doit pas produire de Chapitre dupliqué dans la sortie."""

    def test_pas_de_doublon_de_chapitre(self):
        doc = _doc(TEXTE_CHAPITRES_REPETES, "TEST_CHAP_REP")
        ids = [c.id for c in doc.chapitres]
        assert ids == list(dict.fromkeys(ids)), (
            f"Chapitres dupliqués dans la sortie: {ids}"
        )
        # Attendu : chap_i, chap_ii avec 2 articles chacun.
        assert len(doc.chapitres) == 2
        assert [a.id for a in doc.chapitres[0].articles] == ["art_1", "art_2"]
        assert [a.id for a in doc.chapitres[1].articles] == ["art_3", "art_4"]
