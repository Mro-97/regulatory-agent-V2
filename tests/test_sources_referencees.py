"""tests/test_sources_referencees.py — ne citer que les sources réellement
mentionnées dans la réponse (fin du « 15 sources vérifiées » systématique).
"""

from __future__ import annotations

from datetime import date

from src.agents.citation import sources_referencees
from src.models import EvidenceRecuperee


def _ev(doc: str, art: str) -> EvidenceRecuperee:
    return EvidenceRecuperee(
        chunk_id=f"{doc}_{art}",
        document_id=doc,
        article_id=art,
        texte_extrait="…",
        score_similarite=0.5,
        valid_from=date(2018, 5, 25),
    )


_TOUTES = [_ev("RGPD_2016_679_FULL", f"art_{n}") for n in (16, 32, 33, 70, 99)]


def test_garde_seulement_les_articles_mentionnes() -> None:
    texte = "Selon l'article 33 du RGPD, notification sous 72h. Voir aussi art_16."
    gardees = sources_referencees(texte, _TOUTES)
    assert sorted(e.article_id for e in gardees) == ["art_16", "art_33"]


def test_numero_avec_borne_de_mot_pas_de_faux_positif() -> None:
    texte = "L'article 32 impose des mesures de sécurité."
    gardees = sources_referencees(texte, _TOUTES)
    assert [e.article_id for e in gardees] == ["art_32"]


def test_reference_par_article_id_brut() -> None:
    texte = "Le texte pertinent est RGPD_2016_679_FULL / art_70."
    gardees = sources_referencees(texte, _TOUTES)
    assert [e.article_id for e in gardees] == ["art_70"]


def test_aucune_reference_reconnue_renvoie_tout() -> None:
    texte = "Je ne peux pas répondre à cette question."
    assert len(sources_referencees(texte, _TOUTES)) == len(_TOUTES)


def test_liste_vide() -> None:
    assert sources_referencees("article 33", []) == []
