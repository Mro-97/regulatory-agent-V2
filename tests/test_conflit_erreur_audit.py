"""tests/test_conflit_erreur_audit.py — M9 : l'échec de l'agent Conflit est tracé.

Avant, `etape_conflit` enveloppait l'analyse LLM ET la soumission Redis dans
un même `except Exception` qui renvoyait `None` : une panne de l'agent Conflit
disparaissait de `agents_executes` sans aucune trace d'audit.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from src.agents.conflit import NiveauConflit, ResultatConflit
from src.errors import QueueBackendError
from src.orchestrator import Orchestrateur
from src.orchestrator_pipeline import etape_conflit


async def _resultat_a_valider(*_args: object, **_kwargs: object) -> ResultatConflit:
    """Résultat de conflit nécessitant une validation humaine."""
    return ResultatConflit(
        conflits=[],
        niveau_global=NiveauConflit.PROBABLE,
        necessite_validation_humaine=True,
    )


class TestEchecAgentConflit:
    def test_renvoie_une_sortie_portant_l_erreur(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orchestrateur = Orchestrateur(mode="mock")

        async def panne(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("indisponible")

        monkeypatch.setattr("src.orchestrator_pipeline._executer_agent_conflit", panne)

        sortie = asyncio.run(
            etape_conflit(orchestrateur, "Q ?", None, [], uuid.uuid4())
        )
        assert sortie.nom_agent == "Conflict"
        assert "indisponible" in str(sortie.contenu["erreur"])

    def test_echec_redis_ne_fait_pas_echouer_l_etape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """H1 : une panne Redis est journalisée, pas propagée à /ask."""
        orchestrateur = Orchestrateur(mode="mock")

        async def panne(_tache: object) -> None:
            raise QueueBackendError("indisponible")

        monkeypatch.setattr(orchestrateur, "_enregistrer_tache_redis", panne)
        monkeypatch.setattr(
            "src.orchestrator_pipeline._executer_agent_conflit", _resultat_a_valider
        )

        sortie = asyncio.run(
            etape_conflit(orchestrateur, "Q ?", None, [], uuid.uuid4())
        )
        assert sortie.nom_agent == "Conflict"
        assert sortie.contenu["niveau_global"] == "probable"
