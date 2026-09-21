"""tests/test_mlx_thread_unique.py — tout le travail MLX sur un seul thread.

MLX lie le stream GPU et l'état des tableaux au thread qui les crée. Les
agents tournaient sur `asyncio.to_thread`, donc sur des workers variables du
pool par défaut : le modèle était chargé sur un thread et utilisé sur un
autre, et `mx.eval` du cache de prompt levait « There is no Stream(gpu, 2) in
current thread » — environ une question sur six (mesuré le 2026-09-21, trace
à l'appui). `executer_mlx` fixe un thread unique et permanent ; le stream de
génération est mémorisé par thread au lieu d'être recréé à chaque appel.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import types

import pytest

from src import mlx_utils
from src.mlx_utils import _lier_stream_generation_au_thread_courant, executer_mlx
from src.orchestrator import Orchestrateur


def _appel_executeur() -> int:
    """Fonction synchrone : rend l'identifiant du thread qui l'exécute."""

    async def scenario() -> list[int]:
        return [
            await executer_mlx(threading.get_ident),
            await executer_mlx(threading.get_ident),
        ]

    return asyncio.run(scenario())


class TestThreadMlxUnique:
    def test_deux_appels_sur_le_meme_thread_dedie(self) -> None:
        premier, second = _appel_executeur()
        assert premier == second
        assert premier != threading.get_ident(), "pas le thread appelant"

    def test_executeur_mono_thread(self) -> None:
        assert mlx_utils.EXECUTEUR_MLX._max_workers == 1

    def test_orchestrateur_utilise_le_thread_mlx(self) -> None:
        """`_executer_bloquant` passe bien par l'executeur MLX dédié."""

        async def scenario() -> tuple[int, int]:
            orchestrateur = Orchestrateur(mode="mock")
            premier = await orchestrateur._executer_bloquant(threading.get_ident)
            second = await orchestrateur._executer_bloquant(threading.get_ident)
            return premier, second

        premier, second = asyncio.run(scenario())
        assert premier == second


class TestStreamParThread:
    def test_stream_memorise_et_non_recree(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deux appels sur le même thread réutilisent le même stream GPU."""
        faux_mx = types.ModuleType("mlx.core")
        creations: list[object] = []

        def _nouveau_stream(_device: object) -> object:
            creations.append(object())
            return creations[-1]

        faux_mx.new_stream = _nouveau_stream  # type: ignore[attr-defined]
        faux_mx.default_device = lambda: "gpu"  # type: ignore[attr-defined]

        faux_paquet = types.ModuleType("mlx_lm")
        faux_gen = types.ModuleType("mlx_lm.generate")
        faux_gen.generation_stream = None  # type: ignore[attr-defined]
        faux_paquet.generate = faux_gen  # type: ignore[attr-defined]

        monkeypatch.setitem(sys.modules, "mlx.core", faux_mx)
        monkeypatch.setitem(sys.modules, "mlx_lm", faux_paquet)
        monkeypatch.setitem(sys.modules, "mlx_lm.generate", faux_gen)
        monkeypatch.setattr(mlx_utils, "_streams_par_thread", {})

        _lier_stream_generation_au_thread_courant()
        _lier_stream_generation_au_thread_courant()

        assert len(creations) == 1, "un stream neuf par appel = fuite de streams GPU"
        assert faux_gen.generation_stream is creations[0]  # type: ignore[attr-defined]
