"""
src/mlx_utils.py — Wrapper MLX pour l'inférence locale
=======================================================

Abstraction unique pour charger, utiliser et décharger les modèles
MLX sur Apple Silicon.

Contraintes respectées :
- Aucun appel externe (OpenAI / Anthropic / Google).
- MLX uniquement — pas de PyTorch MPS.
- Lazy loading : le modèle n'est chargé qu'au premier appel.
- Un seul modèle actif à la fois sur Mac A (16 Go).
- Mac B (24 Go) peut accueillir deux modèles 7B simultanément.

API publique :
    get_model(model_name)           → MLXInference (depuis le cache)
    MLXInference.load()             → charge le modèle en mémoire
    MLXInference.generate(...)      → génère du texte
    MLXInference.embed(...)         → calcule un embedding
    MLXInference.unload()           → libère la mémoire

Dépendances : mlx >= 0.16.0, mlx-lm >= 0.16.0 (licence MIT/Apache).
"""

from __future__ import annotations

import gc
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Mémoire maximale allouable par machine (en Go).
# Utilisé uniquement pour les logs d'avertissement — pas d'enforcement matériel.
MEMOIRE_MAC_A_GO = 16
MEMOIRE_MAC_B_GO = 24
MEMOIRE_MAC_C_GO = 24

# Empreinte mémoire approximative par taille de modèle en 4-bit (en Go).
EMPREINTE_ESTIMEE = {
    "3b":  2.0,
    "7b":  4.5,
    "14b": 9.0,
}


# ---------------------------------------------------------------------------
# Structures de résultats
# ---------------------------------------------------------------------------


@dataclass
class StatistiquesGeneration:
    """Métriques de génération retournées avec chaque réponse."""

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
# Classe principale
# ---------------------------------------------------------------------------


class MLXInference:
    """
    Wrapper autour de mlx_lm.load / mlx_lm.generate.

    Gère le cycle de vie complet d'un modèle :
    instanciation (sans chargement) → load() → generate()/embed() → unload().

    Usage direct :
        m = MLXInference("mlx-community/Mistral-7B-Instruct-v0.3-4bit")
        resultat = m.generate("Résume cet article.", max_tokens=512)
        m.unload()

    Usage en context manager (déchargement automatique) :
        with MLXInference("mlx-community/Qwen2.5-7B-Instruct-4bit") as m:
            texte = m.generate("Question réglementaire.", max_tokens=256).texte
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
            model_name: Identifiant HuggingFace du modèle MLX
                        (ex. 'mlx-community/Mistral-7B-Instruct-v0.3-4bit').
            quantized:  Indique que le modèle est quantifié (4-bit).
                        Conservé pour la lisibilité et les logs — mlx_lm
                        détecte automatiquement la quantification.
            temperature: Température de génération par défaut (0.1 = quasi-déterministe).
            top_p:       Paramètre nucleus sampling par défaut.
        """
        self.model_name = model_name
        self.quantized = quantized
        self.temperature = temperature
        self.top_p = top_p

        # Modèle et tokenizer — None jusqu'au premier load()
        self._model = None
        self._tokenizer = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    def load(self) -> None:
        """
        Charge le modèle et le tokenizer en mémoire unifiée via mlx_lm.

        Idempotent : un second appel sans unload() intermédiaire est ignoré.

        Raises:
            RuntimeError: Si le chargement échoue (modèle introuvable,
                          mémoire insuffisante, etc.).
        """
        if self._loaded:
            logger.debug("Modèle déjà chargé : %s", self.model_name)
            return

        logger.info("Chargement du modèle MLX : %s", self.model_name)
        debut = time.time()

        try:
            from mlx_lm import load as mlx_load

            self._model, self._tokenizer = mlx_load(self.model_name)
            self._loaded = True

            duree = time.time() - debut
            logger.info(
                "Modèle chargé en %.1f s : %s (quantifié=%s)",
                duree, self.model_name, self.quantized,
            )

        except Exception as exc:
            logger.error("Échec du chargement de %s : %s", self.model_name, exc)
            self._model = None
            self._tokenizer = None
            self._loaded = False
            raise RuntimeError(
                f"Impossible de charger le modèle '{self.model_name}' : {exc}"
            ) from exc

    def unload(self) -> None:
        """
        Libère le modèle de la mémoire unifiée.

        À appeler explicitement dès que le modèle n'est plus nécessaire,
        en particulier sur Mac A (16 Go) avant de charger un autre modèle.
        Idempotent : appeler unload() sur un modèle déjà déchargé est sûr.
        """
        if not self._loaded:
            return

        logger.info("Déchargement du modèle : %s", self.model_name)
        self._model = None
        self._tokenizer = None
        self._loaded = False

        # Force le ramasse-miettes Python pour rendre la mémoire plus vite
        gc.collect()
        logger.info("Modèle déchargé : %s", self.model_name)

    @property
    def est_charge(self) -> bool:
        """True si le modèle est actuellement en mémoire."""
        return self._loaded

    # ------------------------------------------------------------------
    # Génération de texte
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> ResultatGeneration:
        """
        Génère du texte à partir d'un prompt.

        Charge le modèle automatiquement s'il ne l'est pas encore.

        Args:
            prompt:      Texte d'entrée envoyé au modèle.
            max_tokens:  Nombre maximum de tokens à générer.
            temperature: Surcharge la température de l'instance si fournie.
            top_p:       Surcharge le top_p de l'instance si fourni.

        Returns:
            ResultatGeneration avec le texte généré et les statistiques.

        Raises:
            RuntimeError: Si la génération échoue.
        """
        if not self._loaded:
            self.load()

        temp = temperature if temperature is not None else self.temperature
        tp = top_p if top_p is not None else self.top_p

        logger.debug(
            "Génération — modèle=%s max_tokens=%d temp=%.2f top_p=%.2f",
            self.model_name, max_tokens, temp, tp,
        )

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

            # Estimation du nombre de tokens via le tokenizer
            try:
                tokens_out = len(self._tokenizer.encode(texte))
            except Exception:
                tokens_out = max(1, len(texte.split()))

            tps = tokens_out / duree if duree > 0 else 0.0

            logger.debug(
                "Génération terminée — %d tokens en %.1f s (%.1f tok/s)",
                tokens_out, duree, tps,
            )

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
            raise RuntimeError(
                f"Génération MLX échouée ({self.model_name}) : {exc}"
            ) from exc

    def generate_avec_messages(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: Optional[float] = None,
    ) -> ResultatGeneration:
        """
        Génère une réponse à partir d'une liste de messages structurés.

        Applique le chat template du tokenizer si disponible,
        sinon concatène les messages en texte brut.

        Args:
            messages:    Liste de dicts {"role": "system"|"user"|"assistant",
                         "content": "..."}.
            max_tokens:  Nombre maximum de tokens à générer.
            temperature: Surcharge la température de l'instance si fournie.

        Returns:
            ResultatGeneration.
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
            # Fallback si le tokenizer ne supporte pas les chat templates
            prompt = "\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in messages
            ) + "\nASSISTANT:"

        return self.generate(prompt, max_tokens=max_tokens, temperature=temperature)

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    def embed(self, text: str) -> list[float]:
        """
        Calcule l'embedding d'un texte.

        Utilise la moyenne des états cachés du dernier layer comme
        approximation d'embedding. Pour des embeddings de meilleure qualité
        sur Mac B, préférer un modèle dédié tel que
        'mlx-community/all-MiniLM-L6-v2-mlx'.

        Args:
            text: Texte à encoder.

        Returns:
            Vecteur d'embedding sous forme de liste de floats.

        Raises:
            RuntimeError: Si le calcul échoue.
        """
        if not self._loaded:
            self.load()

        try:
            import mlx.core as mx

            # Tokenisation
            token_ids = self._tokenizer.encode(text)
            input_ids = mx.array([token_ids])

            # Passage forward
            sortie = self._model(input_ids)

            # Extraction du vecteur : moyenne sur la dimension séquence
            if hasattr(sortie, "last_hidden_state"):
                vecteur = sortie.last_hidden_state[0].mean(axis=0)
            elif isinstance(sortie, tuple) and len(sortie) > 0:
                vecteur = sortie[0][0].mean(axis=0)
            else:
                vecteur = sortie[0].mean(axis=0)

            mx.eval(vecteur)
            return vecteur.tolist()

        except Exception as exc:
            logger.error("Erreur d'embedding avec %s : %s", self.model_name, exc)
            raise RuntimeError(
                f"Embedding MLX échoué ({self.model_name}) : {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "MLXInference":
        """Charge le modèle à l'entrée du bloc with."""
        self.load()
        return self

    def __exit__(self, *args) -> None:
        """Décharge le modèle à la sortie du bloc with."""
        self.unload()

    def __repr__(self) -> str:
        etat = "chargé" if self._loaded else "non chargé"
        return f"MLXInference(model_name={self.model_name!r}, état={etat})"


# ---------------------------------------------------------------------------
# Cache global — gestion du modèle actif
# ---------------------------------------------------------------------------


class _CacheModeles:
    """
    Registre interne qui garantit qu'un seul modèle est actif à la fois
    sur Mac A (16 Go), et au plus deux modèles 7B sur Mac B (24 Go).

    Ne jamais instancier directement — utiliser `get_model()`.
    """

    def __init__(self) -> None:
        # Dictionnaire modele_id → instance MLXInference
        self._instances: dict[str, MLXInference] = {}
        # Identifiant du modèle actuellement chargé en mémoire
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

        Si un autre modèle est déjà chargé, il est déchargé avant
        de retourner la nouvelle instance (comportement Mac A).

        Args:
            model_name:  Identifiant du modèle HuggingFace.
            quantized:   Indicateur de quantification 4-bit.
            temperature: Température de génération par défaut.
            top_p:       Paramètre nucleus sampling par défaut.

        Returns:
            Instance MLXInference (le modèle n'est pas encore chargé —
            le chargement se fait au premier appel à generate() ou load()).
        """
        # Décharger le modèle actif s'il est différent de celui demandé
        if self._actif is not None and self._actif != model_name:
            if self._actif in self._instances and self._instances[self._actif].est_charge:
                logger.info(
                    "Remplacement du modèle actif : %s → %s",
                    self._actif, model_name,
                )
                self._instances[self._actif].unload()

        # Créer l'instance si elle n'existe pas encore
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
        """Décharge tous les modèles enregistrés dans le cache."""
        for instance in self._instances.values():
            instance.unload()
        self._actif = None
        logger.info("Tous les modèles MLX ont été déchargés.")

    def statut(self) -> dict[str, bool]:
        """Retourne l'état de chargement de chaque modèle dans le cache."""
        return {nom: inst.est_charge for nom, inst in self._instances.items()}


# Instance globale du cache — importée par orchestrator.py et les agents
model_cache = _CacheModeles()


def get_model(
    model_name: str,
    quantized: bool = True,
    temperature: float = 0.1,
    top_p: float = 0.9,
) -> MLXInference:
    """
    Fonction utilitaire principale — point d'entrée recommandé.

    Retourne une instance MLXInference depuis le cache global.
    Si un autre modèle était actif, il est déchargé automatiquement.
    Le modèle demandé n'est pas encore en mémoire à ce stade :
    il sera chargé au premier appel à generate() ou load().

    Args:
        model_name:  Identifiant HuggingFace du modèle MLX
                     (ex. 'mlx-community/Mistral-7B-Instruct-v0.3-4bit').
        quantized:   True si le modèle est quantifié en 4-bit (défaut).
        temperature: Température de génération (0.1 = quasi-déterministe).
        top_p:       Paramètre nucleus sampling.

    Returns:
        Instance MLXInference prête à l'emploi.

    Exemple :
        model = get_model('mlx-community/Mistral-7B-Instruct-v0.3-4bit')
        resultat = model.generate("Quelles sont les obligations RGPD ?")
        print(resultat.texte)
        model.unload()
    """
    return model_cache.get(
        model_name=model_name,
        quantized=quantized,
        temperature=temperature,
        top_p=top_p,
    )
