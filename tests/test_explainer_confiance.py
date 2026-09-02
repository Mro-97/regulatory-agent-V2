"""tests/test_explainer_confiance.py — garde-fou confiance sans preuve.

Avant : `_synthetiser_avec_llm` (use_llm=True) appelait le LLM même avec
`evidences=[]` et estampillait la réponse `ELEVE` — une réponse non
sourcée présentée comme fiable. Le garde-fou court-circuite : `INCERTAIN`,
message « aucun passage », sans charger de modèle.
"""

from __future__ import annotations

from src.agents.explainer import AgentExplainer
from src.models import NiveauConfiance


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
