"""src/mlx_utils.py — Wrapper MLX pour l'inférence et l'embedding locaux
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
"""  # noqa: D205, D415

from __future__ import annotations

import gc
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Any, TypeVar

from config import cfg

from src.errors import GenerationTimeoutError

logger = logging.getLogger(__name__)

# Alias descendant : le nom historique reste importable pour ne pas
# casser les callers extérieurs (tests, monkey-patch). La classe unique
# vit désormais dans src.errors (§12 étape 8).
MLXTimeoutError = GenerationTimeoutError


T = TypeVar("T")

# Un unique executor dédié aux appels MLX bornés dans le temps. Un thread
# unique suffit : MLX sérialise déjà l'accès aux poids sur le device.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx-timed")


def _executer_avec_timeout(
    fn: Callable[..., T],
    timeout_seconds: float | None,
    *args: Any,
    **kwargs: Any,
) -> T:
    """Exécute `fn` sous timeout ; sans borne si `timeout_seconds` ≤ 0 ou None.

    MLX n'a pas d'interruption coopérative : le thread continue en tâche
    de fond mais l'appelant récupère la main via `future.result(timeout=)`.

    Raises:
        GenerationTimeoutError: si `fn` dépasse `timeout_seconds`.
    """
    if timeout_seconds is None or timeout_seconds <= 0:
        return fn(*args, **kwargs)
    future = _executor.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError as exc:
        raise GenerationTimeoutError(timeout_seconds) from exc


def _compter_tokens(tokenizer: Any, texte: str) -> int:
    """Compte les tokens de `texte` via `tokenizer.encode` (fallback split blancs)."""
    try:
        return len(tokenizer.encode(texte))
    except Exception:  # noqa: BLE001 — frontière externe : dégradation gracieuse, cf. §8
        return max(1, len(texte.split()))


def _resultat_generation(
    modele_id: str,
    texte: str,
    tokens_out: int,
    duree: float,
) -> ResultatGeneration:
    """Assemble un ResultatGeneration avec ses StatistiquesGeneration."""
    tps = tokens_out / duree if duree > 0 else 0.0
    return ResultatGeneration(
        texte=texte,
        statistiques=StatistiquesGeneration(
            modele_id=modele_id,
            tokens_generes=tokens_out,
            duree_secondes=round(duree, 3),
            tokens_par_seconde=round(tps, 1),
        ),
    )


def _tronquer_pour_embedding(texte: str) -> str:
    """Tronque un texte trop long avant embedding, avec avertissement.

    La limite (`cfg.mlx_taille_max_texte_embedding`, caractères) est un
    filet de sécurité pour les appelants (Retriever, Ingester) qui
    court-circuiteraient le chunking en amont — voir la description du
    champ dans `config.py` pour le rationale.
    """
    limite = cfg.mlx_taille_max_texte_embedding
    if len(texte) > limite:
        logger.warning(
            "Texte tronqué de %d à %d caractères avant embedding.",
            len(texte),
            limite,
        )
        return texte[:limite]
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
    """Wrapper autour de mlx_lm.load / mlx_lm.generate.
    Lazy loading — le modèle n'est chargé qu'au premier appel.
    Un seul modèle actif à la fois via le registre global.
    """  # noqa: D205

    def __init__(
        self,
        model_name: str,
        quantized: bool = True,
        temperature: float = 0.1,
        top_p: float = 0.9,
    ) -> None:
        """Configure l'inférence — le modèle est chargé à la demande via `load()`."""
        self.model_name = model_name
        self.quantized = quantized
        self.temperature = temperature
        self.top_p = top_p
        # `mlx_lm` n'expose pas de types publics — `_model` et `_tokenizer`
        # restent opaques en `Any` côté mypy. Ce sont des ressources natives
        # dont la seule discipline est le cycle load/unload local à ce module.
        self._model: Any = None
        self._tokenizer: Any = None
        self._loaded = False

    def load(self) -> None:
        """Charge le modèle via mlx_lm. Idempotent."""
        if self._loaded:
            return
        logger.info("Chargement du modèle MLX : %s", self.model_name)
        debut = time.time()
        try:
            from mlx_lm import load as mlx_load

            # `mlx_lm.load` déclare renvoyer un 3-tuple mais utilise en
            # pratique 2 valeurs — ce n'est pas maîtrisable côté typage.
            self._model, self._tokenizer = mlx_load(self.model_name)  # type: ignore[misc]
            self._loaded = True
            logger.info(
                "Modèle chargé en %.1f s : %s", time.time() - debut, self.model_name
            )
        except Exception as exc:
            from src.errors import ModelLoadError

            self._model = None
            self._tokenizer = None
            self._loaded = False
            raise ModelLoadError(self.model_name, cause=str(exc)) from exc

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
        """True si le modèle d'inférence est déjà chargé en mémoire."""
        return self._loaded

    def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float | None = None,
        top_p: float | None = None,
        timeout_seconds: float | None = None,
    ) -> ResultatGeneration:
        """Génère du texte via mlx_lm ; lève GenerationFailedError sur échec."""
        if not self._loaded:
            self.load()
        try:
            return self._generer_texte(
                prompt,
                max_tokens,
                temperature,
                top_p,
                timeout_seconds,
            )
        except Exception as exc:
            from src.errors import GenerationFailedError

            raise GenerationFailedError(self.model_name, cause=str(exc)) from exc

    def _generer_texte(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float | None,
        top_p: float | None,
        timeout_seconds: float | None,
    ) -> ResultatGeneration:
        """Appelle `mlx_lm.generate` sous timeout et compose le ResultatGeneration."""
        temp = temperature if temperature is not None else self.temperature
        tp = top_p if top_p is not None else self.top_p
        timeout = (
            timeout_seconds if timeout_seconds is not None else cfg.mlx_timeout_seconds
        )
        debut = time.time()
        texte = self._invoquer_mlx_generate(prompt, max_tokens, temp, tp, timeout)
        duree = time.time() - debut
        tokens_out = _compter_tokens(self._tokenizer, texte)
        return _resultat_generation(self.model_name, texte, tokens_out, duree)

    def _invoquer_mlx_generate(
        self,
        prompt: str,
        max_tokens: int,
        temp: float,
        top_p: float,
        timeout: float | None,
    ) -> str:
        """Appelle `mlx_lm.generate` sous timeout, avec sampler configuré."""
        from mlx_lm import generate as mlx_generate
        from mlx_lm.sample_utils import make_sampler

        return _executer_avec_timeout(
            mlx_generate,
            timeout,
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            sampler=make_sampler(temp=temp, top_p=top_p),
            verbose=False,
        )

    def generate_avec_messages(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: float | None = None,
    ) -> ResultatGeneration:
        """Génère depuis une liste de messages avec chat template."""
        if not self._loaded:
            self.load()
        try:
            prompt = self._tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        except Exception:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
            prompt = (
                "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
                + "\nASSISTANT:"
            )
        return self.generate(prompt, max_tokens=max_tokens, temperature=temperature)

    def __enter__(self) -> MLXInference:  # noqa: D105
        self.load()
        return self

    def __exit__(self, *args: Any) -> None:  # noqa: D105
        self.unload()

    def __repr__(self) -> str:  # noqa: D105
        return f"MLXInference(model_name={self.model_name!r}, chargé={self._loaded})"


# ---------------------------------------------------------------------------
# Registres globaux
# ---------------------------------------------------------------------------


class _CacheGeneration:
    """Un seul modèle de génération actif à la fois."""

    def __init__(self) -> None:
        self._instances: dict[str, MLXInference] = {}
        self._actif: str | None = None

    def get(
        self,
        model_name: str,
        quantized: bool = True,
        temperature: float = 0.1,
        top_p: float = 0.9,
    ) -> MLXInference:
        """Retourne l'instance ; décharge l'actif si un autre modèle est demandé."""
        self._decharger_si_swap(model_name)
        if model_name not in self._instances:
            self._instances[model_name] = MLXInference(
                model_name=model_name,
                quantized=quantized,
                temperature=temperature,
                top_p=top_p,
            )
        self._actif = model_name
        return self._instances[model_name]

    def _decharger_si_swap(self, model_name: str) -> None:
        """Décharge le modèle actif s'il diffère de `model_name` (swap RAM)."""
        actif = self._instances.get(self._actif) if self._actif else None
        if self._actif and self._actif != model_name and actif and actif.est_charge:
            logger.info("Swap modèle : %s → %s", self._actif, model_name)
            actif.unload()

    def unload_all(self) -> None:
        """Décharge toutes les instances mémorisées et remet à zéro l'actif."""
        for inst in self._instances.values():
            inst.unload()
        self._actif = None

    def statut(self) -> dict[str, bool]:
        """Retourne un mapping {nom_modèle: est_chargé}."""
        return {n: i.est_charge for n, i in self._instances.items()}


model_cache = _CacheGeneration()


def get_model(
    model_name: str,
    quantized: bool = True,
    temperature: float = 0.1,
    top_p: float = 0.9,
) -> MLXInference:
    """Retourne un modèle de génération depuis le cache global."""
    return model_cache.get(
        model_name=model_name,
        quantized=quantized,
        temperature=temperature,
        top_p=top_p,
    )


# Embedding (classe, cache, getter) extrait dans src/mlx_embedding.py
# (§12 étape 6). Ré-exporté ici pour compatibilité descendante.
# fmt: off
from src.mlx_embedding import MLXEmbedding as MLXEmbedding  # noqa: E402
from src.mlx_embedding import embedding_cache as embedding_cache  # noqa: E402
from src.mlx_embedding import get_embedding as get_embedding  # noqa: E402
# fmt: on
