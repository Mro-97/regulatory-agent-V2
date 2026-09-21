"""tests/test_embedding_defaut_config.py — le modèle d'embedding vient de la config.

Régression corrigée : `get_embedding()` avait pour défaut `"BAAI/bge-m3"`, un
dépôt HuggingFace **sans safetensors** — donc inutilisable par `mlx_embeddings`,
comme le documente `config.py`. Tout appelant qui omettait l'argument chargeait
un modèle cassé, et `scripts/launcher.py` le faisait au préchauffage.

Ces tests verrouillent la source unique : `cfg.modele_embedding`.
"""

from __future__ import annotations

import pytest

from config import cfg
from src.mlx_embedding import MLXEmbedding, get_embedding


class TestDefautAligneSurLaConfig:
    def test_get_embedding_sans_argument_utilise_la_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le défaut n'est plus un dépôt HF codé en dur mais la configuration."""
        from src import mlx_embedding

        mlx_embedding.embedding_cache._instances.clear()
        monkeypatch.setattr(cfg, "modele_embedding", "models/modele-de-test")
        instance = get_embedding()
        assert instance.model_name == "models/modele-de-test"
        mlx_embedding.embedding_cache._instances.clear()

    def test_argument_explicite_respecte(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un appelant qui passe un nom garde la main (les agents le font)."""
        from src import mlx_embedding

        mlx_embedding.embedding_cache._instances.clear()
        monkeypatch.setattr(cfg, "modele_embedding", "models/par-defaut")
        instance = get_embedding("models/choisi-explicitement")
        assert instance.model_name == "models/choisi-explicitement"
        mlx_embedding.embedding_cache._instances.clear()

    def test_le_defaut_n_est_pas_un_depot_hf_sans_poids(self) -> None:
        """Garde-fou direct : `BAAI/bge-m3` ne doit plus servir de défaut.

        Ce dépôt HF est documenté comme inutilisable (pas de safetensors) ; le
        rechoisir par défaut ferait retélécharger 4,3 Go et échouer l'encodage.
        """
        import inspect

        from src import mlx_embedding

        signature = inspect.signature(mlx_embedding.get_embedding)
        defaut = signature.parameters["model_name"].default
        assert defaut is None, "le défaut doit être None (résolu via la config)"

    def test_le_cache_est_indexe_par_modele(self) -> None:
        """Deux modèles distincts ne doivent pas partager d'instance."""
        from src import mlx_embedding

        cache = mlx_embedding.embedding_cache
        cache._instances.clear()
        a = cache.get("models/a")
        b = cache.get("models/b")
        assert a is not b
        assert cache.get("models/a") is a, "même modèle = même instance"
        assert isinstance(a, MLXEmbedding)
        cache._instances.clear()
