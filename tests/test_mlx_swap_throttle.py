"""tests/test_mlx_swap_throttle.py — résidence des modèles MLX et anti-DoS.

Une question type utilise DEUX modèles de génération : Qwen pour la synthèse
(Explainer/Temporal) et Mistral pour la vérification des citations. Avec un
seul slot résident, chaque question provoquait deux swaps et, au 4e swap en
moins d'une minute, le quota anti-DoS refusait le chargement — la synthèse
échouait et l'Explainer recopiait les passages (constaté le 2026-09-21 sur
m4pro2 : 3e question consécutive en `mode_reponse='assemblage'`).

`cfg.mlx_modeles_residents_max` (2 par défaut) laisse cohabiter les deux
modèles ; au-delà, le plus ancien est évincé (LRU) et cette éviction
consomme `cfg.mlx_max_swaps_par_minute`.
"""

from __future__ import annotations

import pytest

from config import cfg
from src.errors import ModelSwapThrottledError
from src.mlx_utils import _CacheGeneration


class _FauxModele:
    """Substitut minimal de MLXInference : est_charge + unload, pas d'I/O."""

    def __init__(self, model_name: str, *, charge: bool = True) -> None:
        self.model_name = model_name
        self._loaded = charge

    @property
    def est_charge(self) -> bool:
        return self._loaded

    def unload(self) -> None:
        self._loaded = False


def _cache(noms: tuple[str, ...], ordre: tuple[str, ...]) -> _CacheGeneration:
    """Cache peuplé de faux modèles déjà chargés, dans un ordre LRU donné."""
    cache = _CacheGeneration()
    for nom in noms:
        cache._instances[nom] = _FauxModele(nom)  # type: ignore[assignment]
    cache._ordre = list(ordre)
    return cache


class TestResidence:
    def test_deux_modeles_coexistent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Qwen + Mistral tiennent ensemble : aucun swap pour une question."""
        monkeypatch.setattr(cfg, "mlx_modeles_residents_max", 2)
        cache = _cache(("qwen", "mistral"), ("qwen",))
        cache._promouvoir("mistral")
        assert cache._ordre == ["mistral", "qwen"]
        assert cache.statut() == {"qwen": True, "mistral": True}
        assert cache._historique_swaps == []

    def test_modele_unique_sur_16_go(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Plafond à 1 : comportement historique, l'ancien est déchargé."""
        monkeypatch.setattr(cfg, "mlx_modeles_residents_max", 1)
        cache = _cache(("qwen", "mistral"), ("qwen",))
        cache._promouvoir("mistral")
        assert cache.statut() == {"qwen": False, "mistral": True}
        assert len(cache._historique_swaps) == 1

    def test_troisieme_modele_evince_le_plus_ancien(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Au-delà du plafond, le moins récemment demandé est déchargé."""
        monkeypatch.setattr(cfg, "mlx_modeles_residents_max", 2)
        cache = _cache(("qwen", "mistral", "deepseek"), ("qwen", "mistral"))
        cache._promouvoir("deepseek")
        assert cache._ordre == ["deepseek", "qwen"]
        assert cache.statut() == {"qwen": True, "mistral": False, "deepseek": True}

    def test_modele_jamais_charge_part_sans_consommer_le_quota(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Une instance créée mais jamais chargée ne coûte aucun accès disque."""
        monkeypatch.setattr(cfg, "mlx_modeles_residents_max", 1)
        cache = _CacheGeneration()
        cache._instances["jamais"] = _FauxModele("jamais", charge=False)  # type: ignore[assignment]
        cache._promouvoir("jamais")
        cache._instances["actif"] = _FauxModele("actif")  # type: ignore[assignment]
        cache._promouvoir("actif")
        assert cache._historique_swaps == []
        assert cache.statut() == {"jamais": False, "actif": True}

    def test_redemander_le_meme_modele_ne_compte_pas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dix appels sur le même modèle résident : aucun swap comptabilisé."""
        monkeypatch.setattr(cfg, "mlx_modeles_residents_max", 1)
        cache = _cache(("qwen",), ("qwen",))
        for _ in range(10):
            cache._promouvoir("qwen")
        assert cache._historique_swaps == []
        assert cache.statut()["qwen"] is True


class TestQuotaAntiDos:
    def test_evictions_sous_le_seuil_passent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tant qu'on reste sous `mlx_max_swaps_par_minute`, tout passe."""
        monkeypatch.setattr(cfg, "mlx_modeles_residents_max", 1)
        monkeypatch.setattr(cfg, "mlx_max_swaps_par_minute", 3)
        cache = _cache(("a", "b", "c"), ("a",))
        cache._promouvoir("b")
        cache._promouvoir("c")
        assert len(cache._historique_swaps) == 2

    def test_eviction_au_dela_du_seuil_leve(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """3 évictions en <60 s, puis la 4e = ModelSwapThrottledError."""
        monkeypatch.setattr(cfg, "mlx_modeles_residents_max", 1)
        monkeypatch.setattr(cfg, "mlx_max_swaps_par_minute", 3)
        cache = _cache(("a", "b", "c", "d"), ("a",))
        for nom in ("b", "c", "d"):
            cache._promouvoir(nom)
            cache._instances[nom]._loaded = True  # type: ignore[union-attr]
        with pytest.raises(ModelSwapThrottledError):
            cache._promouvoir("a")

    def test_eviction_refusee_laisse_le_lru_coherent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le quota est vérifié AVANT de sortir le modèle du LRU."""
        monkeypatch.setattr(cfg, "mlx_modeles_residents_max", 1)
        monkeypatch.setattr(cfg, "mlx_max_swaps_par_minute", 1)
        cache = _cache(("a", "b"), ("a",))
        cache._promouvoir("b")  # éviction de a : quota consommé
        with pytest.raises(ModelSwapThrottledError):
            cache._promouvoir("a")
        assert cache.statut() == {"a": False, "b": True}
        assert cache._ordre == ["a", "b"]
