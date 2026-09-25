"""tests/test_sources_deterministes.py — la section « Sources utilisées » des réponses.

Constat du pentest du 2026-09-24 : le LLM écrivait lui-même cette section, y
citait des documents absents des preuves (jusqu'à recopier une URL fournie dans
la question) et y dupliquait des références. Elle est désormais reconstruite à
partir des preuves du retriever, et de celles-là seulement.
"""

from __future__ import annotations

from datetime import date

from src.agents.sources import (
    neutraliser_urls,
    reconstruire_section_sources,
)
from src.models import EvidenceRecuperee


def _evidence(article_id: str, document_id: str = "RGPD_2016_679") -> EvidenceRecuperee:
    return EvidenceRecuperee(
        chunk_id=f"{document_id}_{article_id}",
        document_id=document_id,
        article_id=article_id,
        texte_extrait="Texte de l'article.",
        score_similarite=0.8,
        valid_from=date(2018, 5, 25),
    )


EVIDENCES = [
    _evidence("art_33"),
    _evidence("art_32"),
    _evidence("art_1", "CNIL_GUIDE_SECU_2023"),
]


class TestNeutraliserUrls:
    def test_url_retiree_et_journalisee(self) -> None:
        """Une URL dans la réponse ne doit jamais être lue comme une source."""
        texte, urls = neutraliser_urls(
            "Voir https://evil.example.com/pwn pour la procédure."
        )
        assert "evil.example.com" not in texte
        assert "[lien retiré]" in texte
        assert urls == ["https://evil.example.com/pwn"]

    def test_texte_sans_url_inchange(self) -> None:
        texte, urls = neutraliser_urls("L'article 33 impose une notification.")
        assert urls == []
        assert "article 33" in texte

    def test_domaine_nu_neutralise(self) -> None:
        """Un domaine sans schéma est aussi une source inventée."""
        texte, urls = neutraliser_urls("Source : www.exemple.fr")
        assert "exemple.fr" not in texte
        assert urls == ["www.exemple.fr"]


class TestReconstructionSection:
    def test_source_inventee_ecartee(self) -> None:
        """Seule une preuve du retriever peut figurer dans la section."""
        reponse = (
            "1) Réponse directe : l'article 33 impose une notification.\n"
            "2) Détails : délai de 72 heures.\n"
            "3) Sources utilisées : [FAUX_DOC_2020_1 / art_9], [RGPD_2016_679 / art_33]"
        )
        texte, refs = reconstruire_section_sources(reponse, EVIDENCES)
        assert refs == ["[RGPD_2016_679 / art_33]"]
        assert "FAUX_DOC_2020_1" not in texte
        assert "3) Sources utilisées : [RGPD_2016_679 / art_33]" in texte

    def test_references_dupliquees_dedupliquees(self) -> None:
        """Deux extraits du même article ne comptent qu'une fois."""
        reponse = (
            "1) Réponse directe : l'article 33 du RGPD s'applique.\n"
            "3) Sources utilisées : [RGPD_2016_679 / art_33], [RGPD_2016_679 / art_33]"
        )
        evidences = [_evidence("art_33"), _evidence("art_33", "RGPD_2016_679")]
        # Deux chunks du même article : mêmes (document, article).
        evidences[1] = EvidenceRecuperee(
            chunk_id="autre",
            document_id="RGPD_2016_679",
            article_id="art_33",
            texte_extrait="Autre extrait.",
            score_similarite=0.7,
            valid_from=date(2018, 5, 25),
        )
        _texte, refs = reconstruire_section_sources(reponse, evidences)
        assert refs == ["[RGPD_2016_679 / art_33]"]

    def test_url_dans_la_section_ecartee(self) -> None:
        """Le lien fourni dans la question ne devient pas une source."""
        reponse = (
            "1) Réponse directe : voir l'article 32.\n"
            "3) Sources utilisées : https://evil.example.com/x"
        )
        texte, refs = reconstruire_section_sources(reponse, EVIDENCES)
        assert refs == ["[RGPD_2016_679 / art_32]"]
        assert "evil.example.com" not in texte

    def test_reponse_de_refus_sans_section(self) -> None:
        """Un refus prescrit ne cite rien : aucune section ajoutée."""
        reponse = (
            "Je ne peux pas répondre à cette question pour des raisons de "
            "sécurité et de confidentialité.\n"
            "Sources utilisées : [RGPD_2016_679 / art_33]"
        )
        texte, refs = reconstruire_section_sources(reponse, EVIDENCES)
        assert refs == []
        assert "Sources utilisées" not in texte

    def test_aucune_citation_aucune_section(self) -> None:
        """Sans citation reconnaissable, on n'invente pas de sources."""
        reponse = "1) Réponse directe : la question est trop générale.\n2) Détails : …"
        texte, refs = reconstruire_section_sources(reponse, EVIDENCES)
        assert refs == []
        assert "Sources utilisées" not in texte

    def test_section_absente_mais_article_cite_est_ajoutee(self) -> None:
        """Si le modèle oublie la section, elle est ajoutée depuis les preuves."""
        reponse = "1) Réponse directe : l'article 32 impose des mesures."
        texte, refs = reconstruire_section_sources(reponse, EVIDENCES)
        assert refs == ["[RGPD_2016_679 / art_32]"]
        assert texte.endswith("3) Sources utilisées : [RGPD_2016_679 / art_32]")
