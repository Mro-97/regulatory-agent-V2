"""tests/test_confiance_citation.py — la confiance baisse si la réponse
contient des affirmations non ancrées (verdict de l'agent Citation).
"""

from __future__ import annotations

from datetime import date

from src.agents.citation import ResultatCitation
from src.models import EvidenceRecuperee, NiveauConfiance
from src.orchestrator import _confiance_apres_citation, _score_correspondance


def _ev(score: float | None) -> EvidenceRecuperee:
    return EvidenceRecuperee(
        chunk_id="c",
        document_id="RGPD_2016_679",
        article_id="art_33",
        texte_extrait="…",
        score_similarite=score,
        valid_from=date(2018, 5, 25),
    )


def test_score_correspondance_moyenne() -> None:
    assert _score_correspondance([_ev(0.4), _ev(0.6), _ev(None)]) == 0.5
    assert _score_correspondance([_ev(None), _ev(None)]) is None
    assert _score_correspondance([]) is None


def _res(verifiees: int, douteuses: int) -> ResultatCitation:
    return ResultatCitation(
        citations_verifiees=[object()] * verifiees,  # type: ignore[list-item]
        citations_douteuses=[object()] * douteuses,  # type: ignore[list-item]
        mode="llm",
    )


def test_pas_de_citation_step_confiance_inchangee() -> None:
    out = _confiance_apres_citation(NiveauConfiance.ELEVE, None)
    assert out is NiveauConfiance.ELEVE


def test_aucune_douteuse_confiance_inchangee() -> None:
    out = _confiance_apres_citation(NiveauConfiance.ELEVE, _res(5, 0))
    assert out is NiveauConfiance.ELEVE


def test_toutes_douteuses_force_incertain() -> None:
    out = _confiance_apres_citation(NiveauConfiance.ELEVE, _res(0, 3))
    assert out is NiveauConfiance.INCERTAIN


def test_minorite_de_douteuses_ne_change_rien() -> None:
    # 1 paraphrase parmi 4 citations ancrées : réponse correcte, on garde.
    assert _confiance_apres_citation(NiveauConfiance.MOYEN, _res(4, 1)) is (
        NiveauConfiance.MOYEN
    )


def test_majorite_de_douteuses_descend_un_cran() -> None:
    assert _confiance_apres_citation(NiveauConfiance.ELEVE, _res(1, 3)) is (
        NiveauConfiance.MOYEN
    )
    assert _confiance_apres_citation(NiveauConfiance.MOYEN, _res(1, 3)) is (
        NiveauConfiance.FAIBLE
    )


def test_plancher_incertain() -> None:
    out = _confiance_apres_citation(NiveauConfiance.INCERTAIN, _res(1, 3))
    assert out is NiveauConfiance.INCERTAIN
