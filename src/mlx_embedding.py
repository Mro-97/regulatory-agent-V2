"""src/mlx_embedding.py — Embedding local via mlx-embeddings.

Extrait de src/mlx_utils.py (§12 étape 6). Regroupe la classe
`MLXEmbedding`, son cache global et le getter public `get_embedding`.

Le module d'inférence (`MLXInference`, `get_model`) reste dans
`src/mlx_utils.py`. Ce dernier ré-exporte les symboles ci-dessous pour
compatibilité descendante.
"""

from __future__ import annotations

import gc
import logging
import time
from typing import Any, cast

import mlx.core as mx  # type: ignore[import-not-found]
from config import cfg

from src.mlx_utils import _executer_avec_timeout, _tronquer_pour_embedding


def _importer_emb_generate() -> Any:
    """Importe `mlx_embeddings.generate` ; lève ModelLoadError si l'import échoue."""
    try:
        from mlx_embeddings import generate as emb_generate
    except Exception as exc:
        from src.errors import ModelLoadError

        raise ModelLoadError("mlx-embeddings", cause=str(exc)) from exc
    return emb_generate


logger = logging.getLogger(__name__)


class MLXEmbedding:
    """Wrapper autour de mlx-embeddings pour produire des embeddings
    de qualité optimisée pour la similarité sémantique.

    Utilise mlx_embeddings.load() + mlx_embeddings.generate() qui supporte
    nativement XLMRoberta (bge-m3) contrairement à mlx-embedding-models.

    Le modèle retourne text_embeds déjà normalisés (mean pooling + L2 norm).

    bge-m3 : multilingue, dimension 1024, excellent pour le français.
    Identifiant HuggingFace : 'BAAI/bge-m3'
    """  # noqa: D205

    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        """Args:
        model_name: Identifiant HuggingFace du modèle d'embedding.
                    - "sentence-transformers/<id>" : bascule sur le backend
                      sentence-transformers (repli utilisé quand
                      `mlx_embeddings.generate` déclenche
                      "There is no Stream(gpu, 2)").
                    - autrement : `mlx_embeddings` (voie native MLX).
                    Défaut : 'BAAI/bge-m3'.
        """  # noqa: D205
        self.model_name = model_name
        self._st_mode = model_name.startswith("sentence-transformers/")
        # mêmes contraintes que MLXInference : `sentence_transformers` et
        # `mlx_embeddings` n'exposent pas de types publics — attributs opaques.
        self._model: Any = None
        self._processor: Any = None
        self._loaded = False

    def load(self) -> None:
        """Charge le modèle sous timeout (sentence-transformers ou mlx-embeddings)."""
        if self._loaded:
            return
        logger.info("Chargement du modèle d'embedding : %s", self.model_name)
        debut = time.time()
        try:
            self._model, self._processor = _executer_avec_timeout(
                self._instancier_backend,
                cfg.mlx_load_timeout_seconds,
            )
            self._loaded = True
            logger.info(
                "Modèle d'embedding chargé en %.1f s : %s (%s)",
                time.time() - debut,
                self.model_name,
                "sentence-transformers" if self._st_mode else "mlx-embeddings",
            )
        except Exception as exc:
            from src.errors import ModelLoadError

            self._model = None
            self._processor = None
            self._loaded = False
            raise ModelLoadError(self.model_name, cause=str(exc)) from exc

    def _instancier_backend(self) -> tuple[Any, Any]:
        """Charge le backend actif (sentence-transformers ou mlx-embeddings)."""
        if self._st_mode:
            from sentence_transformers import (  # type: ignore[import-not-found]
                SentenceTransformer,
            )

            nom_court = self.model_name.split("/", 1)[1]
            return SentenceTransformer(nom_court), None
        from mlx_embeddings import load as emb_load  # type: ignore[import-untyped]  # noqa: I001 — ancrage single-ligne du type: ignore

        modele, processor = emb_load(self.model_name)
        return modele, processor

    def unload(self) -> None:
        """Libère le modèle. Idempotent."""
        if not self._loaded:
            return
        logger.info("Déchargement embedding : %s", self.model_name)
        self._model = None
        self._processor = None
        self._loaded = False
        gc.collect()

    @property
    def est_charge(self) -> bool:
        """True si le modèle d'embedding est déjà chargé en mémoire."""
        return self._loaded

    def encode(self, texte: str, timeout_seconds: float | None = None) -> list[float]:
        """Retourne le vecteur d'embedding normalisé de `texte`."""
        if not self._loaded:
            self.load()
        texte = _tronquer_pour_embedding(texte)
        timeout = (
            timeout_seconds if timeout_seconds is not None else cfg.mlx_timeout_seconds
        )
        try:
            return self._encoder_texte_unique(texte, timeout)
        except Exception as exc:
            from src.errors import EmbeddingFailedError

            raise EmbeddingFailedError(self.model_name, cause=str(exc)) from exc

    def _encoder_texte_unique(self, texte: str, timeout: float) -> list[float]:
        """Encodage bas-niveau : soit `sentence-transformers`, soit `mlx_embeddings`."""
        if self._st_mode:
            vecteur = _executer_avec_timeout(self._model.encode, timeout, texte)
            return cast("list[float]", vecteur.tolist())
        from mlx_embeddings import generate as emb_generate

        sortie = _executer_avec_timeout(
            emb_generate,
            timeout,
            self._model,
            self._processor,
            texts=texte,
            max_length=512,
            padding=True,
            truncation=True,
        )
        vecteur = sortie.text_embeds[0]
        mx.eval(vecteur)
        return cast("list[float]", vecteur.tolist())

    def encode_batch(
        self,
        textes: list[str],
        batch_size: int = 32,
    ) -> list[list[float]]:
        """Encode `textes` par lots pour éviter les explosions mémoire."""
        if not self._loaded:
            self.load()
        emb_generate = _importer_emb_generate()
        textes = [_tronquer_pour_embedding(t) for t in textes]
        vecteurs: list[list[float]] = []
        total = len(textes)
        for debut in range(0, total, batch_size):
            lot = textes[debut : debut + batch_size]
            logger.debug(
                "Embedding batch %d-%d / %d",
                debut + 1,
                debut + len(lot),
                total,
            )
            vecteurs.extend(self._encoder_lot(emb_generate, lot, debut))
        return vecteurs

    def _encoder_lot(
        self, emb_generate: Any, lot: list[str], debut: int
    ) -> list[list[float]]:
        """Encode un lot ; lève EmbeddingFailedError avec la fenêtre du lot."""
        try:
            sortie = emb_generate(
                self._model,
                self._processor,
                texts=lot,
                max_length=512,
                padding=True,
                truncation=True,
            )
            mx.eval(sortie.text_embeds)
            return [v.tolist() for v in sortie.text_embeds]
        except Exception as exc:
            from src.errors import EmbeddingFailedError

            raise EmbeddingFailedError(
                self.model_name,
                cause=str(exc),
                batch=(debut, debut + len(lot)),
            ) from exc

    def __enter__(self) -> MLXEmbedding:  # noqa: D105
        self.load()
        return self

    def __exit__(self, *args: Any) -> None:  # noqa: D105
        self.unload()

    def __repr__(self) -> str:  # noqa: D105
        return f"MLXEmbedding(model_name={self.model_name!r}, chargé={self._loaded})"


class _CacheEmbedding:
    """Modèle d'embedding maintenu chargé en permanence sur Mac B."""

    def __init__(self) -> None:
        self._instances: dict[str, MLXEmbedding] = {}

    def get(self, model_name: str = "BAAI/bge-m3") -> MLXEmbedding:
        """Retourne l'instance mémorisée pour `model_name` (créée si absente)."""
        if model_name not in self._instances:
            self._instances[model_name] = MLXEmbedding(model_name=model_name)
        return self._instances[model_name]

    def unload_all(self) -> None:
        """Décharge toutes les instances mémorisées."""
        for inst in self._instances.values():
            inst.unload()

    def statut(self) -> dict[str, bool]:
        """Retourne un mapping {nom_modèle: est_chargé}."""
        return {n: i.est_charge for n, i in self._instances.items()}


embedding_cache = _CacheEmbedding()


def get_embedding(model_name: str = "BAAI/bge-m3") -> MLXEmbedding:
    """Retourne le modèle d'embedding depuis le cache global.

    Args:
        model_name: Identifiant HuggingFace. Défaut : 'BAAI/bge-m3'.

    Returns:
        MLXEmbedding prête à l'emploi (lazy — pas encore chargée).
    """
    return embedding_cache.get(model_name=model_name)
