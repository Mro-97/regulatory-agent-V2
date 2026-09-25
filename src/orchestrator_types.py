"""src/orchestrator_types.py — Ce que les étapes attendent de l'Orchestrateur.

`orchestrator_pipeline` a besoin du type de l'Orchestrateur dans ses signatures,
et `orchestrator` importe `orchestrator_pipeline` au moment de l'exécution : un
`from src.orchestrator import Orchestrateur` — même sous `TYPE_CHECKING` —
refermait le cycle.

Ce protocole décrit les SEULS membres que les étapes utilisent. Typage
structurel : aucun import de l'orchestrateur, et mypy vérifie à chaque appel
que l'objet passé (l'Orchestrateur lui-même) satisfait bien le contrat.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from src.models import TacheValidation

if TYPE_CHECKING:
    from src.agents.retriever import Retriever

T = TypeVar("T")


class OrchestrateurEtapes(Protocol):
    """Contrat minimal des étapes du pipeline (retrieval, temporal, explainer…)."""

    def _obtenir_retriever(self) -> Retriever:
        """Retriever réel, créé au premier appel."""
        ...

    async def _executer_bloquant(
        self,
        fonction: Callable[..., T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Exécute un appel synchrone (MLX) hors de l'event loop."""
        ...

    def _machine_pour_agent(self, nom_agent: str) -> str:
        """Machine d'exécution à inscrire dans la trace d'audit."""
        ...

    async def _enregistrer_tache_redis(self, tache: TacheValidation) -> None:
        """Pousse une tâche de validation humaine dans Redis."""
        ...
