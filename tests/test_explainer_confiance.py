"""tests/test_explainer_confiance.py — garde-fou confiance sans preuve.

Avant : `_synthetiser_avec_llm` (use_llm=True) appelait le LLM même avec
`evidences=[]` et estampillait la réponse `ELEVE` — une réponse non
sourcée présentée comme fiable. Le garde-fou court-circuite : `INCERTAIN`,
message « aucun passage », sans charger de modèle.
"""

from __future__ import annotations

from datetime import date

from src.agents.explainer import AgentExplainer, _evaluer_confiance
from src.models import EvidenceRecuperee, NiveauConfiance


def _evidence(score: float) -> EvidenceRecuperee:
    return EvidenceRecuperee(
        chunk_id="c",
        document_id="RGPD_2016_679",
        article_id="art_33",
        texte_extrait="…",
        score_similarite=score,
        valid_from=date(2018, 5, 25),
    )


def test_llm_sans_preuve_retourne_incertain_sans_charger_modele() -> None:
    agent = AgentExplainer(use_llm=True)
    resultat = agent.expliquer(question="Obligations RGPD ?", evidences=[])
    assert resultat.niveau_confiance is NiveauConfiance.INCERTAIN
    assert resultat.mode == "assemblage"
    assert resultat.sources_citees == []
    assert agent._modele is None


def test_assemblage_sans_preuve_reste_incertain() -> None:
    agent = AgentExplainer(use_llm=False)
    resultat = agent.expliquer(question="Obligations RGPD ?", evidences=[])
    assert resultat.niveau_confiance is NiveauConfiance.INCERTAIN


def test_evaluer_confiance_refus_llm_force_incertain() -> None:
    preuves = [_evidence(0.9) for _ in range(5)]
    reponse = "Je ne peux pas répondre à cette question pour des raisons de sécurité."
    assert _evaluer_confiance(reponse, preuves) is NiveauConfiance.INCERTAIN


def test_evaluer_confiance_sans_info_force_incertain() -> None:
    preuves = [_evidence(0.8) for _ in range(3)]
    reponse = "Les sources disponibles ne contiennent pas d'information pertinente."
    assert _evaluer_confiance(reponse, preuves) is NiveauConfiance.INCERTAIN


def test_evaluer_confiance_seuils_sur_moyenne() -> None:
    reponse = "1) Réponse directe : le délai est de 72 heures. 2) Détails : …"
    assert _evaluer_confiance(reponse, [_evidence(0.52)] * 15) is NiveauConfiance.ELEVE
    assert _evaluer_confiance(reponse, [_evidence(0.46)] * 15) is NiveauConfiance.MOYEN
    assert _evaluer_confiance(reponse, [_evidence(0.36)] * 15) is NiveauConfiance.FAIBLE


def test_evaluer_confiance_sans_scores_est_incertain() -> None:
    reponse = "Réponse fondée sur les textes."
    preuves = [
        EvidenceRecuperee(
            chunk_id="c",
            document_id="d",
            article_id="a",
            texte_extrait="…",
            score_similarite=None,
            valid_from=date(2018, 5, 25),
        )
    ]
    assert _evaluer_confiance(reponse, preuves) is NiveauConfiance.INCERTAIN
