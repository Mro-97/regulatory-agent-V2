"""tests/test_conflit_verdicts_dernier_json.py — M12 de l'analyse Conflit LLM.

DeepSeek-R1 recopie souvent l'exemple du prompt (avec son `{"verdicts": …}`)
dans son raisonnement avant de produire la vraie réponse. On ne doit parser
que le DERNIER objet JSON équilibré portant `verdicts`, et rejeter tout
libellé hors {CONFIRME, APPARENT, INEXISTANT}.
"""

from __future__ import annotations

from src.agents.conflit_llm import extraire_verdicts


class TestDernierJsonEquilibre:
    def test_echo_du_prompt_ignore(self) -> None:
        analyse = (
            'Exemple du prompt : {"verdicts": [{"conflit": 1, '
            '"verdict": "CONFIRME"}]}\n'
            "Raisonnement... l'exemple ne s'applique pas ici.\n"
            "Ma réponse finale :\n"
            '{"verdicts": [{"conflit": 1, "verdict": "INEXISTANT"}]}'
        )
        assert extraire_verdicts(analyse) == {1: "INEXISTANT"}

    def test_json_dans_texte_libre(self) -> None:
        analyse = (
            "Voici mon analyse :\n"
            '```json\n{"verdicts": [{"conflit": 1, "verdict": "APPARENT"}]}\n```\n'
            "J'espère que ça aide."
        )
        assert extraire_verdicts(analyse) == {1: "APPARENT"}

    def test_aucun_json_renvoie_none(self) -> None:
        assert extraire_verdicts("Blabla verdict CONFIRMÉ pour tout partout") is None

    def test_json_sans_verdicts_ignore(self) -> None:
        assert extraire_verdicts('{"autre": 1}') is None


class TestVocabulaireVerdicts:
    def test_verdict_hors_vocabulaire_rejete(self) -> None:
        analyse = (
            '{"verdicts": ['
            '{"conflit": 1, "verdict": "PEUT_ETRE"},'
            '{"conflit": 2, "verdict": "CONFIRME"}'
            "]}"
        )
        assert extraire_verdicts(analyse) == {2: "CONFIRME"}

    def test_verdicts_accueillis_normalises(self) -> None:
        analyse = (
            '{"verdicts": [{"conflit": 1, "verdict": "Confirmé"},'
            '{"conflit": 2, "verdict": " apparent "}]}'
        )
        assert extraire_verdicts(analyse) == {1: "CONFIRME", 2: "APPARENT"}
