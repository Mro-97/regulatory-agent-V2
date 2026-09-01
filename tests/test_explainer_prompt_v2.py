"""tests/test_explainer_prompt_v2.py — durcissement prompt Explainer.

Le v2 (2026-09-01) verrouille l'Explainer contre trois vecteurs
identifiés lors de l'audit sécurité :
- fuite de connaissances externes / URLs hors corpus,
- réponse à des questions techniques (fuite d'architecture),
- suivi d'instructions injectées dans le contenu des chunks.

Ces tests vérifient uniquement que le prompt système effectivement
chargé contient les garde-fous — ils ne vérifient pas le comportement
du LLM (qui reste imparfait, cf. skill security §prompt-injection).
"""

from __future__ import annotations


def _rendre_prompt_explainer() -> str:
    """Retourne le contenu system du gabarit Explainer effectivement utilisé."""
    from src.prompts_loader import charger_prompt

    messages = charger_prompt("explainer/synthetiser", 2).rendre(
        question="Question test",
        contexte="<SOURCE>vide</SOURCE>",
        contexte_temporel="",
    )
    return messages[0]["content"]


class TestPromptExplainerV2:
    def test_interdit_sources_externes(self):  # noqa: ANN201
        """Le prompt doit explicitement bannir URLs et docs en ligne."""
        system = _rendre_prompt_explainer()
        assert "JAMAIS" in system
        assert "URL" in system or "connaissances externes" in system

    def test_fallback_si_corpus_insuffisant(self):  # noqa: ANN201
        """Réponse figée obligatoire quand les sources ne couvrent pas."""
        system = _rendre_prompt_explainer()
        assert "Les sources disponibles ne contiennent pas" in system

    def test_refuse_questions_techniques(self):  # noqa: ANN201
        """Fuite d'architecture bloquée — réponse figée obligatoire."""
        system = _rendre_prompt_explainer()
        assert "Cette question ne relève pas du droit réglementaire" in system

    def test_refuse_generation_code(self):  # noqa: ANN201
        """Interdiction stricte de générer du code (blocage RCE indirect)."""
        system = _rendre_prompt_explainer()
        assert "JAMAIS générer de code" in system

    def test_avertissement_prompt_injection(self):  # noqa: ANN201
        """Le contenu <SOURCE> est data, pas instruction — doit être rappelé."""
        system = _rendre_prompt_explainer()
        assert "<SOURCE>" in system
        assert "DONNÉE" in system or "jamais une consigne" in system

    def test_structure_reponse_trois_parties(self):  # noqa: ANN201
        """Format de réponse figé (1/ Réponse directe, 2/ Détails, 3/ Sources)."""
        system = _rendre_prompt_explainer()
        assert "Réponse directe" in system
        assert "Détails" in system
        assert "Sources utilisées" in system

    def test_refus_pour_raisons_securite(self):  # noqa: ANN201
        """Réponse figée pour les questions enfreignant les règles."""
        system = _rendre_prompt_explainer()
        assert "raisons de sécurité et de confidentialité" in system
