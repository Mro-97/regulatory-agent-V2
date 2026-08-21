"""Test dédié Bug #4 — l'audit trail reflète la vraie machine d'exécution."""

import asyncio
import sys
import types

# Stubs MLX pour environnement non-Apple
for nom in ("mlx", "mlx.core", "mlx_lm", "mlx_lm.sample_utils", "mlx_embeddings"):
    if nom not in sys.modules:
        sys.modules[nom] = types.ModuleType(nom)
sys.modules["mlx.core"].eval = lambda *a, **k: None

from datetime import date  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from src.orchestrator import (  # noqa: E402
    Orchestrateur,
    _MACHINE_PAR_AGENT,
    _MACHINE_INCONNUE,
)
from src.models import (  # noqa: E402
    EvidenceRecuperee,
    NiveauConfiance,
    RequeteQuestion,
    SourceReglementaire,
)


# ---------------------------------------------------------------------------
# Mapping statique — invariants du contexte projet
# ---------------------------------------------------------------------------


class TestMappingMachines:
    """Bug #4 : chaque agent doit être mappé à sa vraie machine (contexte § 3)."""

    def test_orchestrateur_sur_mac_a(self):
        assert _MACHINE_PAR_AGENT["Orchestrateur"] == "Mac_A"

    def test_moteur_sur_mac_b(self):
        """Retriever, Temporal, Explainer, Citation tournent sur Mac B."""
        for agent in ("Retriever", "Temporal", "Explainer", "Citation"):
            assert _MACHINE_PAR_AGENT[agent] == "Mac_B", (
                f"{agent} doit être sur Mac B (contexte § 3.2)"
            )

    def test_conflict_sur_mac_c(self):
        assert _MACHINE_PAR_AGENT["Conflict"] == "Mac_C"


# ---------------------------------------------------------------------------
# Méthode utilitaire
# ---------------------------------------------------------------------------


class TestMachinePourAgent:
    def test_agents_connus(self):
        assert Orchestrateur._machine_pour_agent("Retriever") == "Mac_B"
        assert Orchestrateur._machine_pour_agent("Conflict") == "Mac_C"
        assert Orchestrateur._machine_pour_agent("Orchestrateur") == "Mac_A"

    def test_agent_inconnu_retourne_inconnue_avec_log(self, caplog):
        import logging
        caplog.set_level(logging.WARNING)
        resultat = Orchestrateur._machine_pour_agent("AgentBidon")
        assert resultat == _MACHINE_INCONNUE
        assert "AgentBidon" in caplog.text
        assert "audit imprécis" in caplog.text


# ---------------------------------------------------------------------------
# Bout-en-bout — l'audit trail contient les bonnes machines
# ---------------------------------------------------------------------------


class TestAuditContientBonnesMachines:
    """Vérifie qu'après un traitement, SortieAgent.machine est exact."""

    def _evidence(self, chunk: str, doc: str, art: str) -> EvidenceRecuperee:
        return EvidenceRecuperee(
            chunk_id=chunk, document_id=doc, article_id=art,
            texte_extrait=f"Texte {chunk}",
            valid_from=date(2020, 1, 1), valid_to=None,
        )

    def test_machine_retriever_est_mac_b_dans_audit(self):
        orch = Orchestrateur(mode="real")

        # On mocke le Retriever pour ne pas taper Qdrant
        retriever_mock = MagicMock()
        retriever_mock.retrieve.return_value = [
            self._evidence("c1", "RGPD", "art_32"),
        ]
        orch._retriever = retriever_mock

        async def _run():
            evidences, sortie = await orch._etape_retrieval(
                question="Q ?",
                date_contexte=date(2025, 6, 15),
                filtres_themes=[],
                filtres_sources=[],
            )
            return sortie

        sortie = asyncio.run(_run())
        assert sortie.nom_agent == "Retriever"
        assert sortie.machine == "Mac_B", (
            f"Retriever doit être audité comme Mac_B, pas {sortie.machine!r}"
        )

    def test_machine_temporal_est_mac_b_dans_audit(self):
        """Bug #4 : le regex 'en 2023' déclenchait Temporal avec machine='Mac_A'."""
        orch = Orchestrateur(mode="real")

        # Patch AgentTemporel pour éviter le LLM
        with patch("src.agents.temporal.AgentTemporel") as MockAgent:
            instance = MockAgent.return_value
            resultat = MagicMock()
            resultat.date_ref = date(2025, 6, 15)
            resultat.evidences_applicables = []
            resultat.evidences_exclues = []
            resultat.chevauchements = []
            resultat.lacunes = []
            resultat.explication_llm = None
            resultat.niveau_confiance = NiveauConfiance.ELEVE
            instance.analyser.return_value = resultat

            async def _run():
                return await orch._etape_temporal(
                    question="Q ?",
                    date_contexte=date(2025, 6, 15),
                    evidences=[self._evidence("c1", "RGPD", "art_32")],
                )

            _, sortie = asyncio.run(_run())
            assert sortie.nom_agent == "Temporal"
            assert sortie.machine == "Mac_B"

    def test_machine_explainer_est_mac_b_dans_audit(self):
        orch = Orchestrateur(mode="real")

        with patch("src.agents.explainer.AgentExplainer") as MockAgent:
            instance = MockAgent.return_value
            resultat = MagicMock()
            resultat.reponse = "Réponse"
            resultat.mode = "assemblage"
            resultat.sources_citees = []
            resultat.niveau_confiance = NiveauConfiance.MOYEN
            instance.expliquer.return_value = resultat

            async def _run():
                return await orch._etape_explainer(
                    question="Q ?",
                    evidences=[self._evidence("c1", "RGPD", "art_32")],
                    type_pipeline="courante",
                )

            _, _, sortie = asyncio.run(_run())
            assert sortie.nom_agent == "Explainer"
            assert sortie.machine == "Mac_B"


# ---------------------------------------------------------------------------
# Anti-régression — plus aucun "Mac_A" hardcodé pour un agent moteur
# ---------------------------------------------------------------------------


class TestAntiRegressionHardcode:
    def test_aucun_mac_a_hardcode_dans_orchestrator(self):
        """
        Bug #4 : le fichier ne doit plus contenir la chaîne 'machine="Mac_A"'.
        Elle doit passer par _machine_pour_agent().
        """
        from pathlib import Path
        source = (
            Path(__file__).parent.parent / "src" / "orchestrator.py"
        ).read_text(encoding="utf-8")
        assert 'machine="Mac_A"' not in source, (
            "Régression Bug #4 : 'machine=\"Mac_A\"' hardcodé réapparu."
        )
        assert 'machine="Mac_B"' not in source
        assert 'machine="Mac_C"' not in source
