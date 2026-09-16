"""tests/test_classification_temporelle.py — L10 : formulations temporelles.

La liste de mots-clés temporels ne couvrait ni « depuis 2020 », ni
« à partir de 2021 », ni « en vigueur depuis 2019 », ni une année seule.
Elle ne doit pas pour autant confondre une année avec un numéro de règlement
(« 2016/679 », très fréquent dans le corpus).
"""

from __future__ import annotations

import pytest
from src.classification import classifier_requete


class TestFormulationsTemporelles:
    @pytest.mark.parametrize(
        "question",
        [
            "Quelles obligations depuis 2020 ?",
            "Dispositions à partir de 2021",
            "Texte en vigueur depuis 2019",
            "Que prévoit le texte en 2022 ?",
            "Réglementation applicable 2024",
        ],
    )
    def test_formulation_reconnue_temporelle(self, question: str) -> None:
        assert classifier_requete(question, None) == "temporelle"

    def test_numero_de_reglement_n_est_pas_une_annee(self) -> None:
        assert classifier_requete("Que dit le règlement 2016/679 ?", None) == "courante"

    def test_question_simple_reste_courante(self) -> None:
        assert classifier_requete("Question simple", None) == "courante"

    def test_conflit_reste_conflit(self) -> None:
        assert classifier_requete("Contradiction entre NIS2 et RGPD", None) == "conflit"
