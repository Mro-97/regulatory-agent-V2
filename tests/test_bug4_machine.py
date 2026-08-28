"""
Test dédié Bug #4 — l'audit trail reflète la vraie machine d'exécution.

Historique : sous l'ancienne architecture 3-machines, chaque agent était
mappé statiquement à Mac_A/B/C. Le bug d'origine venait d'un hardcode
`machine="Mac_A"` qui persistait pour Temporal alors qu'il tournait sur B.

Sous l'architecture unique m4pro2 (§ 3.1 CONTEXTE_PROJET), tous les agents
tournent sur le même hôte. `_machine_pour_agent()` renvoie désormais le
hostname réel via `platform.node()` — le mapping figé Mac_A/B/C est retiré.
Les tests vérifient donc que l'audit contient le hostname local et non
plus des étiquettes obsolètes.
"""

import asyncio
import platform
import sys
import types

# Stubs MLX pour environnement non-Apple
for nom in ("mlx", "mlx.core", "mlx_lm", "mlx_lm.sample_utils", "mlx_embeddings"):
    if nom not in sys.modules:
        sys.modules[nom] = types.ModuleType(nom)
sys.modules["mlx.core"].eval = lambda *a, **k: None

from datetime import date  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)
from unittest.mock import MagicMock, patch  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)

from src.models import (  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)
    EvidenceRecuperee,
    NiveauConfiance,
)
from src.orchestrator import (  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)
    _MACHINE,
    _MACHINE_INCONNUE,
    Orchestrateur,
)

HOSTNAME_LOCAL = platform.node() or _MACHINE_INCONNUE


# ---------------------------------------------------------------------------
# Constante d'architecture — un seul hôte
# ---------------------------------------------------------------------------


class TestMachineUnique:
    """Sous m4pro2 unique, _MACHINE doit refléter le hostname réel."""

    def test_machine_est_hostname_reel(self):
        assert _MACHINE == HOSTNAME_LOCAL
        assert _MACHINE  # non vide

    def test_machine_non_hardcodee_A_B_C(self):
        assert _MACHINE not in {"Mac_A", "Mac_B", "Mac_C"}


# ---------------------------------------------------------------------------
# Méthode utilitaire
# ---------------------------------------------------------------------------


class TestMachinePourAgent:
    def test_tous_les_agents_retournent_le_hostname_local(self):
        for agent in (
            "Retriever",
            "Temporal",
            "Explainer",
            "Citation",
            "Conflict",
            "Orchestrateur",
        ):
            assert Orchestrateur._machine_pour_agent(agent) == HOSTNAME_LOCAL

    def test_agent_inconnu_retourne_aussi_le_hostname_local(self):
        # Sous architecture unique, il n'y a plus d'agent « sur une autre
        # machine » à signaler. Un nom d'agent inconnu retourne quand même
        # le hostname local — l'exécution s'est bien passée sur cette machine.
        assert Orchestrateur._machine_pour_agent("AgentBidon") == HOSTNAME_LOCAL


# ---------------------------------------------------------------------------
# Bout-en-bout — l'audit trail contient le hostname local
# ---------------------------------------------------------------------------


class TestAuditContientHostname:
    """Vérifie qu'après un traitement, SortieAgent.machine est le hostname réel."""

    def _evidence(self, chunk: str, doc: str, art: str) -> EvidenceRecuperee:
        return EvidenceRecuperee(
            chunk_id=chunk,
            document_id=doc,
            article_id=art,
            texte_extrait=f"Texte {chunk}",
            valid_from=date(2020, 1, 1),
            valid_to=None,
        )

    def test_machine_retriever_est_hostname_dans_audit(self):
        orch = Orchestrateur(mode="real")
        retriever_mock = MagicMock()
        retriever_mock.retrieve.return_value = [
            self._evidence("c1", "RGPD", "art_32"),
        ]
        orch._retriever = retriever_mock

        async def _run():
            _, sortie = await orch._etape_retrieval(
                question="Q ?",
                date_contexte=date(2025, 6, 15),
                filtres_themes=[],
                filtres_sources=[],
            )
            return sortie

        sortie = asyncio.run(_run())
        assert sortie.nom_agent == "Retriever"
        assert sortie.machine == HOSTNAME_LOCAL

    def test_machine_temporal_est_hostname_dans_audit(self):
        orch = Orchestrateur(mode="real")
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
            assert sortie.machine == HOSTNAME_LOCAL

    def test_machine_explainer_est_hostname_dans_audit(self):
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
            assert sortie.machine == HOSTNAME_LOCAL


# ---------------------------------------------------------------------------
# Anti-régression — plus aucun "Mac_A/B/C" hardcodé dans orchestrator.py
# ---------------------------------------------------------------------------


class TestAntiRegressionHardcode:
    def test_aucun_mac_a_b_c_hardcode_dans_orchestrator(self):
        """L'orchestrateur ne doit plus assigner une étiquette figée Mac_X."""
        from pathlib import Path

        source = (Path(__file__).parent.parent / "src" / "orchestrator.py").read_text(
            encoding="utf-8"
        )
        for etiquette in ('machine="Mac_A"', 'machine="Mac_B"', 'machine="Mac_C"'):
            assert etiquette not in source, (
                f"Régression : '{etiquette}' hardcodé réapparu."
            )
