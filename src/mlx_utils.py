"""
src/mlx_utils.py — Wrapper MLX pour l'inférence et l'embedding locaux
======================================================================

Deux classes distinctes selon l'usage :

  MLXInference  — génération de texte via mlx-lm
                  (Llama 3.2 3B, Mistral 7B, Qwen 2.5 7B, DeepSeek-R1 14B)

  MLXEmbedding  — embedding de texte via mlx-embeddings
                  (bge-m3 : XLMRoberta multilingue, dimension 1024)

Deux registres globaux :
  model_cache      → get_model()      pour la génération
  embedding_cache  → get_embedding()  pour l'embedding

Contraintes :
- Aucun appel externe (OpenAI / Anthropic / Google).
- MLX uniquement — pas de PyTorch MPS.
- Lazy loading : rien n'est chargé avant le premier appel.
- Un seul modèle de génération actif à la fois sur Mac A (16 Go).
- Le modèle d'embedding (~570 Mo) reste chargé en permanence sur Mac B.

Dépendances : mlx-lm >= 0.16, mlx-embeddings >= 0.1.0 (MIT/Apache).
"""

from __future__ import annotations

import gc
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

import mlx.core as mx

from config import cfg

logger = logging.getLogger(__name__)


class MLXTimeoutError(RuntimeError):
    """Levée quand un appel MLX dépasse le délai configuré."""


T = TypeVar("T")

# Un unique executor dédié aux appels MLX bornés dans le temps. Un thread
# unique suffit : MLX sérialise déjà l'accès aux poids sur le device.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx-timed")


def _executer_avec_timeout(
    fn: Callable[..., T],
    timeout_seconds: Optional[float],
    *args,
    **kwargs,
) -> T:
    """
    Exécute `fn` en imposant une borne supérieure de temps.

    MLX ne fournit pas d'interruption coopérative : un appel bloqué ne
    peut pas être annulé côté device. Le thread continue en tâche de
    fond, mais l'appelant récupère la main via `future.result(timeout=)`.
    Cette borne empêche un modèle figé ou un prompt pathologique de
    figer l'API indéfiniment.

    Args:
        fn: Fonction à exécuter (synchrone).
        timeout_seconds: Délai maximum. None, 0, ou négatif = pas de timeout.
        *args, **kwargs: Passés à fn.

    Returns:
        Le résultat de fn.

    Raises:
        MLXTimeoutError: Si fn dépasse `timeout_seconds`.
    """
    if timeout_seconds is None or timeout_seconds <= 0:
        return fn(*args, **kwargs)
    future = _executor.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError as exc:
        raise MLXTimeoutError(
            f"Appel MLX dépassé après {timeout_seconds}s"
        ) from exc

# Longueur max (caractères) d'un texte envoyé à l'embedding. `max_length=512`
# et `truncation=True` passés à emb_generate() agissent sur les *tokens*, pas
# les caractères — sur un texte très long, la tokenisation elle-même peut
# consommer une mémoire excessive avant que la troncature ne s'applique
# (cause du crash mémoire observé en ingérant REACH sans chunking en amont).
# Cette limite est un filet de sécurité : les appelants (Retriever, Ingester)
# doivent chunker en amont ; elle protège contre un appel qui les court-circuite.
TAILLE_MAX_TEXTE_EMBEDDING = 8000


def _tronquer_pour_embedding(texte: str) -> str:
    """Tronque un texte trop long avant embedding, avec avertissement."""
    if len(texte) > TAILLE_MAX_TEXTE_EMBEDDING:
        logger.warning(
            "Texte tronqué de %d à %d caractères avant embedding.",
            len(texte), TAILLE_MAX_TEXTE_EMBEDDING,
        )
        return texte[:TAILLE_MAX_TEXTE_EMBEDDING]
    return texte


# ---------------------------------------------------------------------------
# Structures de résultats
# ---------------------------------------------------------------------------


@dataclass
class StatistiquesGeneration:
    """Métriques retournées avec chaque génération."""
    modele_id: str
    tokens_generes: int
    duree_secondes: float
    tokens_par_seconde: float


@dataclass
class ResultatGeneration:
    """Résultat complet d'un appel generate()."""
    texte: str
    statistiques: StatistiquesGeneration


# ---------------------------------------------------------------------------
# MLXInference — génération de texte
# ---------------------------------------------------------------------------


class MLXInference:
    """
    Wrapper autour de mlx_lm.load / mlx_lm.generate.
    Lazy loading — le modèle n'est chargé qu'au premier appel.
    Un seul modèle actif à la fois via le registre global.
    """

    def __init__(
        self,
        model_name: str,
        quantized: bool = True,
        temperature: float = 0.1,
        top_p: float = 0.9,
    ) -> None:
        self.model_name = model_name
        self.quantized = quantized
        self.temperature = temperature
        self.top_p = top_p
        self._model = None
        self._tokenizer = None
        self._loaded = False

    def load(self) -> None:
        """Charge le modèle via mlx_lm. Idempotent."""
        if self._loaded:
            return
        logger.info("Chargement du modèle MLX : %s", self.model_name)
        debut = time.time()
        try:
            from mlx_lm import load as mlx_load
            self._model, self._tokenizer = mlx_load(self.model_name)
            self._loaded = True
            logger.info("Modèle chargé en %.1f s : %s", time.time() - debut, self.model_name)
        except Exception as exc:
            self._model = None
            self._tokenizer = None
            self._loaded = False
            raise RuntimeError(f"Impossible de charger '{self.model_name}' : {exc}") from exc

    def unload(self) -> None:
        """Libère le modèle. Idempotent."""
        if not self._loaded:
            return
        logger.info("Déchargement : %s", self.model_name)
        self._model = None
        self._tokenizer = None
        self._loaded = False
        gc.collect()

    @property
    def est_charge(self) -> bool:
        return self._loaded

    def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        timeout_seconds: Optional[float] = None,
    ) -> ResultatGeneration:
        """Génère du texte. Charge le modèle si nécessaire.

        Args:
            timeout_seconds: Délai max. None = utilise cfg.mlx_timeout_seconds.
                             0 ou négatif = pas de borne (comportement d'avant).
        """
        if not self._loaded:
            self.load()
        temp = temperature if temperature is not None else self.temperature
        tp = top_p if top_p is not None else self.top_p
        timeout = timeout_seconds if timeout_seconds is not None else cfg.mlx_timeout_seconds
        debut = time.time()
        try:
            from mlx_lm import generate as mlx_generate
            from mlx_lm.sample_utils import make_sampler
            sampler = make_sampler(temp=temp, top_p=tp)
            texte = _executer_avec_timeout(
                mlx_generate,
                timeout,
                self._model, self._tokenizer,
                prompt=prompt, max_tokens=max_tokens,
                sampler=sampler, verbose=False,
            )
            duree = time.time() - debut
            try:
                tokens_out = len(self._tokenizer.encode(texte))
            except Exception:
                tokens_out = max(1, len(texte.split()))
            tps = tokens_out / duree if duree > 0 else 0.0
            return ResultatGeneration(
                texte=texte,
                statistiques=StatistiquesGeneration(
                    modele_id=self.model_name,
                    tokens_generes=tokens_out,
                    duree_secondes=round(duree, 3),
                    tokens_par_seconde=round(tps, 1),
                ),
            )
        except Exception as exc:
            raise RuntimeError(f"Génération échouée ({self.model_name}) : {exc}") from exc

    def generate_avec_messages(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: Optional[float] = None,
    ) -> ResultatGeneration:
        """Génère depuis une liste de messages avec chat template."""
        if not self._loaded:
            self.load()
        try:
            prompt = self._tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True,
            )
        except Exception:
            prompt = "\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in messages
            ) + "\nASSISTANT:"
        return self.generate(prompt, max_tokens=max_tokens, temperature=temperature)

    def __enter__(self) -> "MLXInference":
        self.load()
        return self

    def __exit__(self, *args) -> None:
        self.unload()

    def __repr__(self) -> str:
        return f"MLXInference(model_name={self.model_name!r}, chargé={self._loaded})"


# ---------------------------------------------------------------------------
# MLXEmbedding — embedding via mlx-embeddings (supporte XLMRoberta / bge-m3)
# ---------------------------------------------------------------------------


class MLXEmbedding:
    """
    Wrapper autour de mlx-embeddings pour produire des embeddings
    de qualité optimisée pour la similarité sémantique.

    Utilise mlx_embeddings.load() + mlx_embeddings.generate() qui supporte
    nativement XLMRoberta (bge-m3) contrairement à mlx-embedding-models.

    Le modèle retourne text_embeds déjà normalisés (mean pooling + L2 norm).

    bge-m3 : multilingue, dimension 1024, excellent pour le français.
    Identifiant HuggingFace : 'BAAI/bge-m3'
    """

    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        """
        Args:
            model_name: Identifiant HuggingFace du modèle d'embedding.
                        - "sentence-transformers/<id>" : bascule sur le backend
                          sentence-transformers (repli utilisé quand
                          `mlx_embeddings.generate` déclenche
                          "There is no Stream(gpu, 2)").
                        - autrement : `mlx_embeddings` (voie native MLX).
                        Défaut : 'BAAI/bge-m3'.
        """
        self.model_name = model_name
        self._st_mode = model_name.startswith("sentence-transformers/")
        self._model = None
        self._processor = None
        self._loaded = False

    def load(self) -> None:
        """Charge le modèle. Idempotent."""
        if self._loaded:
            return
        logger.info("Chargement du modèle d'embedding : %s", self.model_name)
        debut = time.time()
        try:
            if self._st_mode:
                from sentence_transformers import SentenceTransformer
                nom_court = self.model_name.split("/", 1)[1]
                self._model = SentenceTransformer(nom_court)
                self._processor = None
            else:
                from mlx_embeddings import load as emb_load
                self._model, self._processor = emb_load(self.model_name)
            self._loaded = True
            logger.info(
                "Modèle d'embedding chargé en %.1f s : %s (%s)",
                time.time() - debut, self.model_name,
                "sentence-transformers" if self._st_mode else "mlx-embeddings",
            )
        except Exception as exc:
            self._model = None
            self._processor = None
            self._loaded = False
            raise RuntimeError(
                f"Impossible de charger le modèle d'embedding '{self.model_name}' : {exc}"
            ) from exc

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
        return self._loaded

    def encode(self, texte: str, timeout_seconds: Optional[float] = None) -> list[float]:
        """
        Calcule l'embedding d'un texte.

        Args:
            texte: Texte à encoder.
            timeout_seconds: Délai max. None = utilise cfg.mlx_timeout_seconds.

        Returns:
            Vecteur normalisé (liste de floats, dimension 1024 pour bge-m3).
        """
        if not self._loaded:
            self.load()
        texte = _tronquer_pour_embedding(texte)
        timeout = timeout_seconds if timeout_seconds is not None else cfg.mlx_timeout_seconds
        try:
            if self._st_mode:
                vecteur = _executer_avec_timeout(
                    self._model.encode, timeout, texte
                )
                return vecteur.tolist()
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
            # text_embeds est déjà normalisé (mean pooling + L2)
            vecteur = sortie.text_embeds[0]
            mx.eval(vecteur)
            return vecteur.tolist()
        except Exception as exc:
            raise RuntimeError(f"Embedding échoué ({self.model_name}) : {exc}") from exc

    def encode_batch(
        self,
        textes: list[str],
        batch_size: int = 32,
    ) -> list[list[float]]:
        """
        Calcule les embeddings d'une liste de textes.
        Traite par lots pour éviter les problèmes mémoire.

        Args:
            textes:     Liste de textes à encoder.
            batch_size: Taille des lots.

        Returns:
            Liste de vecteurs, un par texte.
        """
        if not self._loaded:
            self.load()

        try:
            from mlx_embeddings import generate as emb_generate
        except Exception as exc:
            raise RuntimeError(f"mlx-embeddings non disponible : {exc}") from exc

        textes = [_tronquer_pour_embedding(t) for t in textes]
        vecteurs: list[list[float]] = []
        total = len(textes)

        for debut in range(0, total, batch_size):
            lot = textes[debut: debut + batch_size]
            logger.debug("Embedding batch %d-%d / %d", debut + 1, debut + len(lot), total)
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
                for vecteur in sortie.text_embeds:
                    vecteurs.append(vecteur.tolist())
            except Exception as exc:
                raise RuntimeError(
                    f"Embedding batch échoué lot {debut}-{debut + len(lot)} : {exc}"
                ) from exc

        return vecteurs

    def __enter__(self) -> "MLXEmbedding":
        self.load()
        return self

    def __exit__(self, *args) -> None:
        self.unload()

    def __repr__(self) -> str:
        return f"MLXEmbedding(model_name={self.model_name!r}, chargé={self._loaded})"


# ---------------------------------------------------------------------------
# Registres globaux
# ---------------------------------------------------------------------------


class _CacheGeneration:
    """Un seul modèle de génération actif à la fois."""

    def __init__(self) -> None:
        self._instances: dict[str, MLXInference] = {}
        self._actif: Optional[str] = None

    def get(
        self,
        model_name: str,
        quantized: bool = True,
        temperature: float = 0.1,
        top_p: float = 0.9,
    ) -> MLXInference:
        if self._actif and self._actif != model_name:
            if self._instances.get(self._actif, None) and self._instances[self._actif].est_charge:
                logger.info("Swap modèle : %s → %s", self._actif, model_name)
                self._instances[self._actif].unload()
        if model_name not in self._instances:
            self._instances[model_name] = MLXInference(
                model_name=model_name, quantized=quantized,
                temperature=temperature, top_p=top_p,
            )
        self._actif = model_name
        return self._instances[model_name]

    def unload_all(self) -> None:
        for inst in self._instances.values():
            inst.unload()
        self._actif = None

    def statut(self) -> dict[str, bool]:
        return {n: i.est_charge for n, i in self._instances.items()}


class _CacheEmbedding:
    """Modèle d'embedding maintenu chargé en permanence sur Mac B."""

    def __init__(self) -> None:
        self._instances: dict[str, MLXEmbedding] = {}

    def get(self, model_name: str = "BAAI/bge-m3") -> MLXEmbedding:
        if model_name not in self._instances:
            self._instances[model_name] = MLXEmbedding(model_name=model_name)
        return self._instances[model_name]

    def unload_all(self) -> None:
        for inst in self._instances.values():
            inst.unload()

    def statut(self) -> dict[str, bool]:
        return {n: i.est_charge for n, i in self._instances.items()}


model_cache = _CacheGeneration()
embedding_cache = _CacheEmbedding()


def get_model(
    model_name: str,
    quantized: bool = True,
    temperature: float = 0.1,
    top_p: float = 0.9,
) -> MLXInference:
    """Retourne un modèle de génération depuis le cache global."""
    return model_cache.get(
        model_name=model_name, quantized=quantized,
        temperature=temperature, top_p=top_p,
    )


def get_embedding(model_name: str = "BAAI/bge-m3") -> MLXEmbedding:
    """
    Retourne le modèle d'embedding depuis le cache global.

    Args:
        model_name: Identifiant HuggingFace. Défaut : 'BAAI/bge-m3'.

    Returns:
        MLXEmbedding prête à l'emploi (lazy — pas encore chargée).
    """
    return embedding_cache.get(model_name=model_name)
