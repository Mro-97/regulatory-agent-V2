"""Test dédié Bug #5 — élévation de conflit par le LLM correctement isolée."""

import sys
import types
from datetime import date
from unittest.mock import MagicMock

# Stubs MLX pour environnement non-Apple
for nom in ("mlx", "mlx.core", "mlx_lm", "mlx_lm.sample_utils", "mlx_embeddings"):
    if nom not in sys.modules:
        sys.modules[nom] = types.ModuleType(nom)
sys.modules["mlx.core"].eval = lambda *a, **k: None

import pytest  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)
from src.agents.conflit import (  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)
    AgentConflit,
    ConflitDetecte,
    NiveauConflit,
    _normaliser_verdict,
)
from src.models import EvidenceRecuperee  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ev(chunk_id: str, doc: str, art: str, texte: str) -> EvidenceRecuperee:
    return EvidenceRecuperee(
        chunk_id=chunk_id,
        document_id=doc,
        article_id=art,
        texte_extrait=texte,
        valid_from=date(2020, 1, 1),
        valid_to=None,
    )


@pytest.fixture
def deux_conflits():
    """Deux conflits distincts pour tester l'isolation des verdicts."""
    ev_a1 = _ev("c1", "RGPD_2016_679", "art_33", "notification obligatoire")
    ev_b1 = _ev("c2", "NIS2_2022_2555", "art_23", "ne doit pas retarder")
    ev_a2 = _ev("c3", "DOC_X", "art_1", "responsable doit")
    ev_b2 = _ev("c4", "DOC_Y", "art_2", "responsable ne doit pas")

    return [
        ConflitDetecte(
            evidence_a=ev_a1,
            evidence_b=ev_b1,
            niveau=NiveauConflit.POTENTIEL,
            description="Tension notification",
        ),
        ConflitDetecte(
            evidence_a=ev_a2,
            evidence_b=ev_b2,
            niveau=NiveauConflit.POTENTIEL,
            description="Tension doit/ne doit pas",
        ),
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestNormaliserVerdict:
    def test_accents_retires(self):
        assert _normaliser_verdict("Confirmé") == "CONFIRME"
        assert _normaliser_verdict("Confirmé.") == "CONFIRME"

    def test_espaces_et_casse(self):
        assert _normaliser_verdict(" apparent ") == "APPARENT"
        assert _normaliser_verdict("INEXISTANT") == "INEXISTANT"

    def test_ponctuation_peripherique(self):
        assert _normaliser_verdict("CONFIRMÉ,") == "CONFIRME"
        assert _normaliser_verdict("'inexistant'") == "INEXISTANT"


# ---------------------------------------------------------------------------
# Bug #5 — verdict par conflit, pas global
# ---------------------------------------------------------------------------


class TestBug5VerdictParConflit:
    """
    Régression du Bug #5 : avant le fix, si le LLM répondait
    'Conflit 1 CONFIRMÉ, conflit 2 INEXISTANT', les deux conflits étaient
    élevés à PROBABLE parce que 'CONFIRMÉ' était cherché globalement.
    """

    def test_verdicts_isoles_par_conflit(self, deux_conflits):
        agent = AgentConflit(use_llm=True)
        agent._modele = MagicMock()

        # Le LLM répond en JSON structuré : verdict par conflit
        sortie_llm = MagicMock()
        sortie_llm.texte = (
            '{"verdicts": ['
            '{"conflit": 1, "verdict": "CONFIRMÉ",   "justification": "vrai"},'
            '{"conflit": 2, "verdict": "INEXISTANT", "justification": "faux"}'
            "]}"
        )
        agent._modele.generate_avec_messages.return_value = sortie_llm

        conflits_maj, _ = agent._analyser_avec_llm("Q ?", deux_conflits)

        # Le conflit 1 doit être PROBABLE (CONFIRMÉ)
        # Le conflit 2 doit avoir été RETIRÉ (INEXISTANT)
        assert len(conflits_maj) == 1
        assert conflits_maj[0].niveau == NiveauConflit.PROBABLE
        assert conflits_maj[0].evidence_a.document_id == "RGPD_2016_679"

    def test_tous_confirmes(self, deux_conflits):
        agent = AgentConflit(use_llm=True)
        agent._modele = MagicMock()
        sortie = MagicMock()
        sortie.texte = (
            '{"verdicts": ['
            '{"conflit": 1, "verdict": "CONFIRMÉ", "justification": "x"},'
            '{"conflit": 2, "verdict": "CONFIRMÉ", "justification": "y"}'
            "]}"
        )
        agent._modele.generate_avec_messages.return_value = sortie

        conflits_maj, _ = agent._analyser_avec_llm("Q ?", deux_conflits)
        assert len(conflits_maj) == 2
        assert all(c.niveau == NiveauConflit.PROBABLE for c in conflits_maj)

    def test_apparent_conserve_niveau_initial(self, deux_conflits):
        agent = AgentConflit(use_llm=True)
        agent._modele = MagicMock()
        sortie = MagicMock()
        sortie.texte = (
            '{"verdicts": ['
            '{"conflit": 1, "verdict": "APPARENT",  "justification": "x"},'
            '{"conflit": 2, "verdict": "INEXISTANT","justification": "y"}'
            "]}"
        )
        agent._modele.generate_avec_messages.return_value = sortie

        conflits_maj, _ = agent._analyser_avec_llm("Q ?", deux_conflits)
        # APPARENT → niveau initial (POTENTIEL) conservé
        # INEXISTANT → retiré
        assert len(conflits_maj) == 1
        assert conflits_maj[0].niveau == NiveauConflit.POTENTIEL

    def test_parsing_json_echoue_niveaux_conserves(self, deux_conflits):
        """Sortie LLM non-JSON : fallback sur les niveaux déterministes."""
        agent = AgentConflit(use_llm=True)
        agent._modele = MagicMock()
        sortie = MagicMock()
        sortie.texte = "Blabla verdict CONFIRMÉ pour tout partout"
        agent._modele.generate_avec_messages.return_value = sortie

        conflits_maj, _ = agent._analyser_avec_llm("Q ?", deux_conflits)
        # Aucun n'est élevé, aucun n'est retiré
        assert len(conflits_maj) == 2
        assert all(c.niveau == NiveauConflit.POTENTIEL for c in conflits_maj)

    def test_llm_leve_exception_fallback(self, deux_conflits):
        agent = AgentConflit(use_llm=True)
        agent._modele = MagicMock()
        agent._modele.generate_avec_messages.side_effect = RuntimeError("boom")

        conflits_maj, analyse = agent._analyser_avec_llm("Q ?", deux_conflits)
        assert len(conflits_maj) == 2  # conservés
        assert "indisponible" in analyse.lower()

    def test_bloc_json_dans_texte_libre(self, deux_conflits):
        """Le LLM entoure parfois le JSON de texte — on doit l'extraire."""
        agent = AgentConflit(use_llm=True)
        agent._modele = MagicMock()
        sortie = MagicMock()
        sortie.texte = (
            "Voici mon analyse :\n"
            '{"verdicts": ['
            '{"conflit": 1, "verdict": "CONFIRMÉ",   "justification": "x"},'
            '{"conflit": 2, "verdict": "INEXISTANT", "justification": "y"}'
            "]}\n"
            "J'espère que ça aide."
        )
        agent._modele.generate_avec_messages.return_value = sortie

        conflits_maj, _ = agent._analyser_avec_llm("Q ?", deux_conflits)
        assert len(conflits_maj) == 1
        assert conflits_maj[0].niveau == NiveauConflit.PROBABLE
