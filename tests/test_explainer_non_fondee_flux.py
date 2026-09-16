"""tests/test_explainer_non_fondee_flux.py — M6/M8 de l'agent Explainer.

M6 : la sous-chaîne générique « ne contient pas d'information » servait au
prompt v2 à signaler une couverture PARTIELLE ; elle ne doit plus faire
passer une réponse fondée pour un refus. Seules les phrases de repli
complètes prescrites par `prompts/explainer/synthetiser.v2.md` comptent.

M8 : un flux LLM vide retombait sur le message d'erreur de l'orchestrateur
au lieu de l'assemblage des preuves, comme le fait le chemin non-stream.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest

from src.agents.explainer import (
    AgentExplainer,
    _evaluer_confiance,
    reponse_est_non_fondee,
)
from src.models import EvidenceRecuperee, NiveauConfiance


class _ModeleFluxVide:
    """Faux modèle MLX qui ne produit aucun fragment."""

    def stream_generer_avec_messages(self, **_kwargs: object) -> Iterator[str]:
        """Retourne un flux vide."""
        return iter(())


def _ev(score: float) -> EvidenceRecuperee:
    return EvidenceRecuperee(
        chunk_id="c1",
        document_id="RGPD_2016_679",
        article_id="art_32",
        texte_extrait="Le responsable met en œuvre des mesures appropriées.",
        score_similarite=score,
        valid_from=date(2018, 5, 25),
    )


class TestReponseNonFondee:
    @pytest.mark.parametrize(
        "reponse",
        [
            "Les sources disponibles ne contiennent pas d'information permettant de "
            "répondre à cette question.",
            "Cette question ne relève pas du droit réglementaire. Je ne peux pas y "
            "répondre.",
            "Je ne peux pas répondre à cette question pour des raisons de sécurité "
            "et de confidentialité.",
        ],
    )
    def test_phrases_de_repli_prescrites_detectees(self, reponse: str) -> None:
        assert reponse_est_non_fondee(reponse) is True

    def test_couverture_partielle_n_est_pas_un_refus(self) -> None:
        reponse = (
            "1) Réponse directe : l'article 32 impose des mesures appropriées. "
            "2) Détails : la source art_32 ne contient pas d'information sur les "
            "délais, mais l'article 33 en précise 72 heures. "
            "3) Sources utilisées : [RGPD_2016_679 / art_32]"
        )
        assert reponse_est_non_fondee(reponse) is False

    def test_couverture_partielle_ne_force_pas_incertain(self) -> None:
        reponse = (
            "L'article 32 impose des mesures. La source ne contient pas "
            "d'information sur les sanctions."
        )
        assert _evaluer_confiance(reponse, [_ev(0.9)] * 3) != NiveauConfiance.INCERTAIN


class TestFluxVide:
    def test_flux_vide_retombe_sur_l_assemblage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = AgentExplainer(use_llm=True)
        monkeypatch.setattr(agent, "_modele", _ModeleFluxVide())
        evidences = [_ev(0.9)]
        fragments = list(
            agent.expliquer_stream(
                question="Obligations ?",
                evidences=evidences,
            )
        )
        attendu = agent._assembler("Obligations ?", evidences, None, "courante").reponse
        assert fragments == [attendu]
        assert "RGPD_2016_679" in fragments[0]
