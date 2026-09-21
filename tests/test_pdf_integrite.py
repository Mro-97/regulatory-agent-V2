"""tests/test_pdf_integrite.py — une page PDF inversée est remise à l'endroit.

Le Journal officiel REACH contient des pages pivotées à 180 degrés que
pdfplumber relit à l'envers. `_texte_page` les redresse AVANT la détection
d'articles : sans cela, les articles — donc tous les chunks — sont du texte
inversé, et la détection de chapitres ne reconnaît plus rien.
"""

from __future__ import annotations

from scripts.pdf_parsing import _texte_page

TEXTE_NORMAL = (
    "Article 33 — Notification d'une violation de donnees a caractere "
    "personnel. En cas de violation, le responsable du traitement notifie "
    "la violation a l'autorite de controle competente dans les meilleurs "
    "delais et, si possible, 72 heures au plus tard apres en avoir pris "
    "connaissance. La notification est accompagnee des informations que la "
    "commission exige pour la protection des personnes concernees."
)
TEXTE_INVERSE = TEXTE_NORMAL[::-1]


class _FaussePage:
    """Page pdfplumber minimale : seul `extract_text` est utilisé."""

    def __init__(self, texte: str | None) -> None:
        self.texte = texte

    def extract_text(self) -> str | None:
        return self.texte


class TestTextePage:
    def test_page_inversee_est_redressee(self) -> None:
        assert _texte_page(_FaussePage(TEXTE_INVERSE), 12) == TEXTE_NORMAL

    def test_page_normale_inchangee(self) -> None:
        assert _texte_page(_FaussePage(TEXTE_NORMAL), 3) == TEXTE_NORMAL

    def test_page_sans_texte(self) -> None:
        """pdfplumber renvoie None sur une page image : chaîne vide, pas None."""
        assert _texte_page(_FaussePage(None), 1) == ""

    def test_page_courte_non_redressee(self) -> None:
        """Trop courte pour être jugée inversée : laissée telle quelle."""
        assert _texte_page(_FaussePage("eD"), 1) == "eD"
