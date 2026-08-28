"""
tests/test_bug6_mlx_timeout.py — B6

Un appel `mlx_lm.generate` ou `mlx_embeddings.generate` bloqué (modèle
figé, prompt pathologique, matériel indisponible) figeait l'API sans
borne. `_executer_avec_timeout` impose désormais une limite de temps
configurée par `cfg.mlx_timeout_seconds` (défaut 60 s).

Le test valide directement le helper : il ne dépend pas de MLX et
reste rapide.
"""

from __future__ import annotations

import sys
import time
import types

# Stubs pour éviter le coût d'import de mlx.
for nom in ("mlx", "mlx.core", "mlx_lm", "mlx_lm.sample_utils", "mlx_embeddings"):
    if nom not in sys.modules:
        sys.modules[nom] = types.ModuleType(nom)
sys.modules["mlx.core"].eval = lambda *a, **k: None

import pytest  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)
from src.mlx_utils import MLXTimeoutError, _executer_avec_timeout  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)


class TestExecuterAvecTimeout:
    def test_retourne_le_resultat_si_dans_les_temps(self):
        def rapide() -> str:
            return "ok"

        assert _executer_avec_timeout(rapide, 5.0) == "ok"

    def test_leve_MLXTimeoutError_si_depasse(self):
        def lent() -> str:
            time.sleep(2.0)
            return "trop tard"

        with pytest.raises(MLXTimeoutError):
            _executer_avec_timeout(lent, 0.2)

    def test_timeout_none_pas_de_borne(self):
        def rapide() -> int:
            return 42

        assert _executer_avec_timeout(rapide, None) == 42

    def test_timeout_zero_pas_de_borne(self):
        def rapide() -> int:
            return 7

        assert _executer_avec_timeout(rapide, 0) == 7

    def test_propage_exception_normale(self):
        def crash() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError):
            _executer_avec_timeout(crash, 5.0)
