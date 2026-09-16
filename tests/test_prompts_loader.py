"""tests/test_prompts_loader.py — chargement des gabarits et garde-fou de chemin.

`charger_prompt` construit un chemin depuis `identifiant` : un identifiant non
maîtrisé (`../…`, chemin absolu) lirait un fichier arbitraire. Le garde-fou
doit accepter la convention réelle du dépôt — `explainer/synthetiser`,
`citation/extraire`, `conflit/analyser`, `temporal/annoter` — sans jamais
laisser sortir de `prompts/`.
"""

from __future__ import annotations

import pytest

from src.errors import PromptNotFoundError
from src.prompts_loader import charger_prompt


class TestIdentifiantsLegitimes:
    """Les gabarits réellement présents se chargent."""

    @pytest.mark.parametrize(
        ("identifiant", "version"),
        [
            ("explainer/synthetiser", 2),
            ("explainer/synthetiser", 1),
            ("citation/extraire", 1),
            ("conflit/analyser", 1),
            ("temporal/annoter", 1),
        ],
    )
    def test_gabarit_existant(self, identifiant: str, version: int) -> None:
        template = charger_prompt(identifiant, version)
        assert template.identifiant.identifiant == identifiant
        assert template.identifiant.version == version


class TestIdentifiantsRefuses:
    """Tout identifiant qui sortirait de `prompts/` est refusé."""

    @pytest.mark.parametrize(
        "identifiant",
        [
            "../.env",
            "../../etc/passwd",
            "explainer/../../.env",
            "/etc/passwd",
            "explainer/",
            "explainer//synthetiser",
            "Explainer/Synthetiser",
            "explainer/synthetiser.md",
            "explainer\\synthetiser",
            "",
        ],
    )
    def test_identifiant_invalide(self, identifiant: str) -> None:
        with pytest.raises(PromptNotFoundError):
            charger_prompt(identifiant, 1)
