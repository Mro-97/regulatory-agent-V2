"""
tests/test_bug5_temporal_validation.py — B5

`AgentTemporel.analyser()` ne validait pas `date_contexte` : un `datetime`
passé par erreur cassait la comparaison `ev.valid_from > date_ref` avec un
`TypeError` obscur ; une date hors intervalle raisonnable (année 1 ou 9999
arrivée dans une requête) produisait un `INCERTAIN` techniquement vrai
mais faussement rassurant.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.agents.temporal import AgentTemporel, _valider_date_contexte
from src.models import EvidenceRecuperee


def _evidence(valid_from: date, valid_to=None) -> EvidenceRecuperee:
    return EvidenceRecuperee(
        chunk_id="c",
        document_id="D",
        article_id="a",
        texte_extrait="t",
        score_similarite=0.9,
        valid_from=valid_from,
        valid_to=valid_to,
    )


class TestValidationDateContexte:
    def test_datetime_normalise_en_date(self):
        # Ne doit pas lever, et doit produire une date.
        d = _valider_date_contexte(datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc))
        assert d == date(2025, 6, 15)

    def test_date_dans_bornes_accepte(self):
        assert _valider_date_contexte(date(2025, 6, 15)) == date(2025, 6, 15)

    def test_none_reste_none(self):
        assert _valider_date_contexte(None) is None

    def test_annee_aberrante_haute_rejete(self):
        with pytest.raises(ValueError):
            _valider_date_contexte(date(9999, 12, 31))

    def test_annee_aberrante_basse_rejete(self):
        with pytest.raises(ValueError):
            _valider_date_contexte(date(1, 1, 1))

    def test_type_non_date_rejete(self):
        with pytest.raises(ValueError):
            _valider_date_contexte("2025-06-15")

    def test_analyser_rejette_date_aberrante(self):
        agent = AgentTemporel(use_llm=False)
        ev = [_evidence(date(2020, 1, 1))]
        with pytest.raises(ValueError):
            agent.analyser(question="Q", evidences=ev, date_contexte=date(9999, 12, 31))

    def test_analyser_accepte_datetime_en_convertissant(self):
        """Avant le fix : `datetime > date` levait TypeError dans le filtre."""
        agent = AgentTemporel(use_llm=False)
        ev = [_evidence(date(2020, 1, 1))]
        res = agent.analyser(
            question="Q",
            evidences=ev,
            date_contexte=datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc),
        )
        assert res.date_ref == date(2025, 6, 15)
        assert len(res.evidences_applicables) == 1
