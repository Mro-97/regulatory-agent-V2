"""tests/test_mlx_swap_throttle.py — anti-DoS par saturation MLX.

`_CacheGeneration.get()` refuse un swap au-delà de
`cfg.mlx_max_swaps_par_minute` : sans ce garde-fou, un attaquant qui
alterne rapidement les modèles (Qwen, Mistral, DeepSeek) force des
unload/load ~1 GB à la chaîne.
"""

from __future__ import annotations

import pytest
from config import cfg
from src.errors import ModelSwapThrottledError
from src.mlx_utils import _CacheGeneration


class _FauxModele:
    """Substitut minimal de MLXInference : est_charge + unload, pas d'I/O."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._loaded = True

    @property
    def est_charge(self) -> bool:
        return self._loaded

    def unload(self) -> None:
        self._loaded = False


@pytest.fixture
def cache_avec_faux_modeles() -> _CacheGeneration:
    """Fabrique un `_CacheGeneration` peuplé de faux modèles.

    Charge 4 modèles fictifs déjà « chargés » pour permettre au test de
    déclencher des swaps sans réellement toucher MLX.
    """
    cache = _CacheGeneration()
    for nom in ("modele-A", "modele-B", "modele-C", "modele-D"):
        cache._instances[nom] = _FauxModele(nom)  # type: ignore[assignment]
    cache._actif = "modele-A"
    return cache


class TestQuotaSwaps:
    def test_swaps_sous_seuil_passent(  # noqa: ANN201
        self,
        cache_avec_faux_modeles,  # noqa: ANN001
        monkeypatch,  # noqa: ANN001
    ):
        """Tant qu'on reste sous `mlx_max_swaps_par_minute`, tout passe."""
        monkeypatch.setattr(cfg, "mlx_max_swaps_par_minute", 3)
        cache = cache_avec_faux_modeles
        cache._decharger_si_swap("modele-B")
        cache._actif = "modele-B"
        cache._decharger_si_swap("modele-C")
        cache._actif = "modele-C"
        # 2 swaps < 3 → pas d'erreur.

    def test_swap_au_dela_du_seuil_leve(  # noqa: ANN201
        self,
        cache_avec_faux_modeles,  # noqa: ANN001
        monkeypatch,  # noqa: ANN001
    ):
        """3 swaps en <60s puis le 4e = ModelSwapThrottledError."""
        monkeypatch.setattr(cfg, "mlx_max_swaps_par_minute", 3)
        cache = cache_avec_faux_modeles
        cache._decharger_si_swap("modele-B")
        cache._actif = "modele-B"
        cache._instances["modele-B"]._loaded = True  # type: ignore[union-attr]
        cache._decharger_si_swap("modele-C")
        cache._actif = "modele-C"
        cache._instances["modele-C"]._loaded = True  # type: ignore[union-attr]
        cache._decharger_si_swap("modele-D")
        cache._actif = "modele-D"
        cache._instances["modele-D"]._loaded = True  # type: ignore[union-attr]
        with pytest.raises(ModelSwapThrottledError):
            cache._decharger_si_swap("modele-A")

    def test_meme_modele_ne_compte_pas(  # noqa: ANN201
        self,
        cache_avec_faux_modeles,  # noqa: ANN001
        monkeypatch,  # noqa: ANN001
    ):
        """Redemander le modèle déjà actif ne consomme pas le quota."""
        monkeypatch.setattr(cfg, "mlx_max_swaps_par_minute", 1)
        cache = cache_avec_faux_modeles
        # 10 appels sur le même modèle actif : aucun swap comptabilisé.
        for _ in range(10):
            cache._decharger_si_swap("modele-A")
        assert cache._historique_swaps == []
