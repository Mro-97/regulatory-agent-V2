"""tests/test_corpus_sources.py — cohérence du registre des sources (corpus).

Le registre est la source de vérité de `corpus_fetch.py` : un id dupliqué
écraserait un document, une URL non-HTTPS ferait échouer le plafond de
sécurité, et un convertisseur inconnu planterait la conversion. Le cas CELLAR
est le plus vicieux : sans en-tête `Accept`, l'API renvoie une notice texte de
41 Mo au lieu du PDF, et l'échec n'apparaît qu'à la conversion
(« No /Root object! - Is this really a PDF? »).
"""

from __future__ import annotations

from scripts.corpus_fetch import _extension
from scripts.corpus_sources import SOURCES

CONVERTISSEURS = {"eurlex", "pdf_prose", "nist_oscal"}


class TestRegistre:
    def test_ids_uniques(self) -> None:
        ids = [s.id for s in SOURCES]
        assert len(ids) == len(set(ids)), "un id dupliqué écraserait un document"

    def test_urls_https(self) -> None:
        assert all(s.url.startswith("https://") for s in SOURCES)

    def test_convertisseurs_connus(self) -> None:
        assert {s.convertisseur for s in SOURCES} <= CONVERTISSEURS

    def test_chaque_source_a_une_version(self) -> None:
        assert all(s.version for s in SOURCES)


class TestSourceCellar:
    def test_cellar_demande_explicitement_le_pdf(self) -> None:
        """CELLAR négocie son contenu : l'en-tête Accept est obligatoire."""
        sources = [
            s for s in SOURCES if s.url.startswith("https://publications.europa.eu/")
        ]
        assert sources, "le registre doit contenir au moins une source CELLAR"
        for src in sources:
            assert src.entetes.get("Accept") == "application/pdf"
            # Sans langue, CELLAR ne sait pas quelle version servir : 400.
            assert src.entetes.get("Accept-Language") == "fr"
            assert src.convertisseur == "pdf_prose"


class TestExtensionTelechargement:
    def test_pdf_garde_son_extension(self) -> None:
        """Sans extension devinée, le convertisseur PDF refuse le fichier."""
        assert _extension("https://x/y", "application/pdf;charset=UTF-8") == ".pdf"

    def test_type_inconnu_et_url_sans_suffixe(self) -> None:
        assert _extension("https://x/y", "") == ".bin"
