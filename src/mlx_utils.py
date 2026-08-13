"""
src/mlx_utils.py — Wrapper MLX pour l'inférence et l'embedding locaux
======================================================================

Deux classes distinctes selon l'usage :

  MLXInference  — génération de texte via mlx-lm
                  (Llama 3.2 3B, Mistral 7B, Qwen 2.5 7B, DeepSeek-R1 14B)

  MLXEmbedding  — embedding de texte via mlx-embedding-models
                  (bge-m3 : multilingue, dimension 1024)

Deux registres globaux correspondants :
  model_cache      → get_model()       pour la génération
  embedding_cache  → get_embedding()   pour l'embedding

Contraintes respectées :
- Aucun appel externe (OpenAI / Anthropic / Google).
- MLX uniquement — pas de PyTorch MPS.
- Lazy loading : rien n'est chargé avant le premier appel.
- Un seul modèle de génération actif à la fois sur Mac A (16 Go).
- Le modèle d'embedding (bge-m3, ~570 Mo) reste chargé en permanence
  sur Mac B — il n'entre pas en conflit avec les modèles de génération.

Dépendances : mlx-lm >= 0.16, mlx-embedding-models >= 0.0.11 (MIT/Apache).
"""

from __future__ import annotations

import gc
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


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

    Gère le cycle de vie : instanciation → load() → generate() → unload().
    Le modèle n'est chargé qu'au premier appel (lazy loading).

    Usage direct :
        m = MLXInference("mlx-community/Mistral-7B-Instruct-v0.3-4bit")
        resultat = m.generate("Question réglementaire.", max_tokens=512)
        m.unload()

    Usage en context manager :
        with MLXInference("mlx-community/Qwen2.5-7B-Instruct-4bit") as m:
            texte = m.generate("Question.", max_tokens=256).texte
    """

    def __init__(
        self,
        model_name: str,
        quantized: bool = True,
        temperature: float = 0.1,
        top_p: float = 0.9,
    ) -> None:
        """
        Initialise le wrapper sans charger le modèle en mémoire.

        Args:
            model_name:  Identifiant HuggingFace du modèle MLX.
            quantized:   Indique que le modèle est quantifié 4-bit (pour les logs).
            temperature: Température de génération par défaut.
            top_p:       Paramètre nucleus sampling par défaut.
        """
        self.model_name = model_name
        self.quantized = quantized
        self.temperature = temperature
        self.top_p = top_p
        self._model = None
        self._tokenizer = None
        self._loaded = False

    def load(self) -> None:
        """
        Charge le modèle et le tokenizer via mlx_lm.
        Idempotent : un second appel sans unload() est ignoré.
        """
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
            logger.error("Échec du chargement de %s : %s", self.model_name, exc)
            raise RuntimeError(f"Impossible de charger '{self.model_name}' : {exc}") from exc

    def unload(self) -> None:
        """
        Libère le modèle de la mémoire unifiée.
        Idempotent : appeler unload() sur un modèle déjà déchargé est sûr.
        """
        if not self._loaded:
            return
        logger.info("Déchargement du modèle : %s", self.model_name)
        self._model = None
        self._tokenizer = None
        self._loaded = False
        gc.collect()
        logger.info("Modèle déchargé : %s", self.model_name)

    @property
    def est_charge(self) -> bool:
        """True si le modèle est actuellement en mémoire."""
        return self._loaded

    def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> ResultatGeneration:
        """
        Génère du texte à partir d'un prompt.
        Charge le modèle automatiquement si nécessaire.

        Args:
            prompt:      Texte d'entrée.
            max_tokens:  Nombre maximum de tokens à générer.
            temperature: Surcharge la température de l'instance si fournie.
            top_p:       Surcharge le top_p de l'instance si fourni.

        Returns:
            ResultatGeneration avec le texte et les statistiques.
        """
        if not self._loaded:
            self.load()

        temp = temperature if temperature is not None else self.temperature
        tp = top_p if top_p is not None else self.top_p

        debut = time.time()
        try:
            from mlx_lm import generate as mlx_generate
            from mlx_lm.sample_utils import make_sampler

            sampler = make_sampler(temp=temp, top_p=tp)
            texte = mlx_generate(
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=max_tokens,
                sampler=sampler,
                verbose=False,
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
            logger.error("Erreur de génération avec %s : %s", self.model_name, exc)
            raise RuntimeError(f"Génération MLX échouée ({self.model_name}) : {exc}") from exc

    def generate_avec_messages(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: Optional[float] = None,
    ) -> ResultatGeneration:
        """
        Génère une réponse depuis une liste de messages structurés.
        Applique le chat template du tokenizer si disponible.

        Args:
            messages:    Liste de dicts {"role": "...", "content": "..."}.
            max_tokens:  Nombre maximum de tokens à générer.
            temperature: Surcharge la température de l'instance si fournie.
        """
        if not self._loaded:
            self.load()
        try:
            prompt = self._tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
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
        etat = "chargé" if self._loaded else "non chargé"
        return f"MLXInference(model_name={self.model_name!r}, état={etat})"


# ---------------------------------------------------------------------------
# MLXEmbedding — embedding de texte dédié
# ---------------------------------------------------------------------------


class MLXEmbedding:
    """
    Wrapper autour de mlx-embedding-models pour produire des embeddings
    de qualité optimisée pour la similarité sémantique.

    Utilise EmbeddingModel.from_registry() avec le nom court du registre
    (ex. "bge-m3") plutôt qu'un identifiant HuggingFace complet.

    bge-m3 : multilingue, dimension 1024, max_length 8192.
    Empreinte mémoire : ~570 Mo — compatible Mac A et Mac B.

    Usage :
        emb = MLXEmbedding("bge-m3")
        vecteur = emb.encode("Quelles sont les obligations RGPD ?")
        emb.unload()

    Usage en context manager :
        with MLXEmbedding("bge-m3") as emb:
            vecteur = emb.encode("Question réglementaire.")
    """

    def __init__(self, model_name: str = "bge-m3") -> None:
        """
        Initialise le wrapper sans charger le modèle.

        Args:
            model_name: Nom du modèle dans le registre mlx-embedding-models
                        (ex. "bge-m3", "multilingual-e5-small").
        """
        self.model_name = model_name
        self._model = None
        self._loaded = False

    def load(self) -> None:
        """
        Charge le modèle d'embedding via EmbeddingModel.from_registry().
        Idempotent.
        """
        if self._loaded:
            return

        logger.info("Chargement du modèle d'embedding : %s", self.model_name)
        debut = time.time()
        try:
            from mlx_embedding_models.embedding import EmbeddingModel
            self._model = EmbeddingModel.from_registry(self.model_name)
            self._loaded = True
            logger.info(
                "Modèle d'embedding chargé en %.1f s : %s",
                time.time() - debut, self.model_name,
            )
        except Exception as exc:
            self._model = None
            self._loaded = False
            logger.error("Échec du chargement de l'embedding %s : %s", self.model_name, exc)
            raise RuntimeError(
                f"Impossible de charger le modèle d'embedding '{self.model_name}' : {exc}"
            ) from exc

    def unload(self) -> None:
        """Libère le modèle d'embedding. Idempotent."""
        if not self._loaded:
            return
        logger.info("Déchargement du modèle d'embedding : %s", self.model_name)
        self._model = None
        self._loaded = False
        gc.collect()
        logger.info("Modèle d'embedding déchargé : %s", self.model_name)

    @property
    def est_charge(self) -> bool:
        """True si le modèle est en mémoire."""
        return self._loaded

    def encode(self, texte: str) -> list[float]:
        """
        Calcule l'embedding d'un texte.
        Charge le modèle automatiquement si nécessaire.

        Args:
            texte: Texte à encoder (question ou chunk de document).

        Returns:
            Vecteur d'embedding normalisé (liste de floats, dimension 1024
            pour bge-m3).

        Raises:
            RuntimeError: Si le calcul échoue.
        """
        if not self._loaded:
            self.load()

        try:
            # encode() attend une liste de phrases
            resultats = self._model.encode(
                [texte],
                batch_size=1,
                show_progress=False,
            )
            # resultats est un tableau numpy/mlx de shape (1, dim)
            # On retourne le premier vecteur sous forme de liste Python
            vecteur = resultats[0]
            if hasattr(vecteur, "tolist"):
                return vecteur.tolist()
            return list(vecteur)

        except Exception as exc:
            logger.error("Erreur d'embedding avec %s : %s", self.model_name, exc)
            raise RuntimeError(
                f"Embedding échoué ({self.model_name}) : {exc}"
            ) from exc

    def encode_batch(self, textes: list[str], batch_size: int = 32) -> list[list[float]]:
        """
        Calcule les embeddings d'une liste de textes en une seule passe.
        Plus efficace que des appels encode() successifs lors de l'ingestion.

        Args:
            textes:     Liste de textes à encoder.
            batch_size: Taille des lots de traitement.

        Returns:
            Liste de vecteurs d'embedding, un par texte.
        """
        if not self._loaded:
            self.load()

        try:
            resultats = self._model.encode(
                textes,
                batch_size=batch_size,
                show_progress=len(textes) > 50,
            )
            return [
                row.tolist() if hasattr(row, "tolist") else list(row)
                for row in resultats
            ]
        except Exception as exc:
            logger.error("Erreur d'embedding batch avec %s : %s", self.model_name, exc)
            raise RuntimeError(
                f"Embedding batch échoué ({self.model_name}) : {exc}"
            ) from exc

    def __enter__(self) -> "MLXEmbedding":
        self.load()
        return self

    def __exit__(self, *args) -> None:
        self.unload()

    def __repr__(self) -> str:
        etat = "chargé" if self._loaded else "non chargé"
        return f"MLXEmbedding(model_name={self.model_name!r}, état={etat})"


# ---------------------------------------------------------------------------
# Registre de génération — un seul modèle actif à la fois sur Mac A
# ---------------------------------------------------------------------------


class _CacheGeneration:
    """
    Garantit qu'un seul modèle de génération est actif à la fois.
    Ne jamais instancier directement — utiliser get_model().
    """

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
        """
        Retourne l'instance MLXInference pour le modèle demandé.
        Décharge le modèle précédent si un autre était actif.
        """
        if self._actif is not None and self._actif != model_name:
            if self._actif in self._instances and self._instances[self._actif].est_charge:
                logger.info("Remplacement : %s → %s", self._actif, model_name)
                self._instances[self._actif].unload()

        if model_name not in self._instances:
            self._instances[model_name] = MLXInference(
                model_name=model_name,
                quantized=quantized,
                temperature=temperature,
                top_p=top_p,
            )

        self._actif = model_name
        return self._instances[model_name]

    def unload_all(self) -> None:
        """Décharge tous les modèles de génération."""
        for inst in self._instances.values():
            inst.unload()
        self._actif = None

    def statut(self) -> dict[str, bool]:
        """État de chargement de chaque modèle."""
        return {nom: inst.est_charge for nom, inst in self._instances.items()}


# ---------------------------------------------------------------------------
# Registre d'embedding — modèle maintenu chargé en permanence sur Mac B
# ---------------------------------------------------------------------------


class _CacheEmbedding:
    """
    Gère les instances MLXEmbedding.
    Le modèle d'embedding reste chargé en permanence (contrairement aux
    modèles de génération qui sont swappés).
    Ne jamais instancier directement — utiliser get_embedding().
    """

    def __init__(self) -> None:
        self._instances: dict[str, MLXEmbedding] = {}

    def get(self, model_name: str = "bge-m3") -> MLXEmbedding:
        """
        Retourne l'instance MLXEmbedding pour le modèle demandé.
        Crée et retourne une nouvelle instance si absente du cache.
        """
        if model_name not in self._instances:
            self._instances[model_name] = MLXEmbedding(model_name=model_name)
        return self._instances[model_name]

    def unload_all(self) -> None:
        """Décharge tous les modèles d'embedding."""
        for inst in self._instances.values():
            inst.unload()

    def statut(self) -> dict[str, bool]:
        """État de chargement de chaque modèle d'embedding."""
        return {nom: inst.est_charge for nom, inst in self._instances.items()}


# ---------------------------------------------------------------------------
# Instances globales et fonctions publiques
# ---------------------------------------------------------------------------

model_cache = _CacheGeneration()
embedding_cache = _CacheEmbedding()


def get_model(
    model_name: str,
    quantized: bool = True,
    temperature: float = 0.1,
    top_p: float = 0.9,
) -> MLXInference:
    """
    Retourne un modèle de génération depuis le cache global.
    Décharge le modèle précédent si nécessaire (contrainte Mac A 16 Go).

    Args:
        model_name:  Identifiant HuggingFace du modèle MLX.
        quantized:   True si le modèle est quantifié 4-bit.
        temperature: Température de génération.
        top_p:       Paramètre nucleus sampling.

    Returns:
        MLXInference prête à l'emploi (lazy — pas encore chargée).
    """
    return model_cache.get(
        model_name=model_name,
        quantized=quantized,
        temperature=temperature,
        top_p=top_p,
    )


def get_embedding(model_name: str = "bge-m3") -> MLXEmbedding:
    """
    Retourne le modèle d'embedding depuis le cache global.
    Le modèle est créé au premier appel et reste en mémoire.

    Args:
        model_name: Nom dans le registre mlx-embedding-models.
                    Défaut : "bge-m3" (multilingue, dim 1024).

    Returns:
        MLXEmbedding prête à l'emploi (lazy — pas encore chargée).

    Exemple :
        emb = get_embedding()
        vecteur = emb.encode("Quelles sont les obligations RGPD ?")
    """
    return embedding_cache.get(model_name=model_name)
