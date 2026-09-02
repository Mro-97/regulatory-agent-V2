"""src/errors.py — Taxonomie d'erreurs de Regulatory Agent V2.

Hiérarchie unique enracinée sur `RegulatoryAgentError` (skill §8). Chaque
exception concrète porte les identifiants de diagnostic (document_id,
request_id, date visée, nom de modèle…) — jamais un secret. Les messages
sont composés dans les `__init__` des sous-classes, ce qui évite les
`raise NewError("chaîne littérale longue")` (règle ruff TRY003).

Règles associées appliquées dans le code :
- `except Exception:` interdit sauf à la frontière la plus externe (API,
  Watcher, main.py), et alors avec journalisation complète puis re-levée
  ou réponse d'erreur explicite (voir `# noqa: BLE001` justifiés).
- `raise NewError(...) from err` obligatoire lors d'une re-levée pour ne
  jamais perdre la cause d'un incident réglementaire (règle ruff B904).
- Aucune exception ne doit être avalée silencieusement (`except: pass`).
"""

from __future__ import annotations

from typing import Any


class RegulatoryAgentError(Exception):
    """Racine de la hiérarchie d'exceptions du projet.

    Toutes les erreurs métier héritent de cette classe. Les callers à la
    frontière externe (API FastAPI, Watcher, entrypoints CLI) peuvent
    filtrer sur `RegulatoryAgentError` pour distinguer une défaillance
    métier connue d'une exception réellement inattendue.
    """


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ConfigurationError(RegulatoryAgentError):
    """Configuration invalide détectée au démarrage ou à l'usage."""


class PromptNotFoundError(ConfigurationError):
    """Le gabarit LLM demandé n'existe pas sous `prompts/`."""

    def __init__(self, chemin: object) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(f"Prompt introuvable : {chemin}")
        self.chemin = chemin


class MalformedPromptError(ConfigurationError):
    """Un fichier prompt existe mais son format `# system` / `# user` est invalide."""

    def __init__(self, sections_manquantes: list[str]) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(
            f"Prompt malformé : section(s) manquante(s) {sections_manquantes}"
        )
        self.sections_manquantes = sections_manquantes


# ---------------------------------------------------------------------------
# Ingestion (corpus JSON, PDF, upsert Qdrant)
# ---------------------------------------------------------------------------


class IngestionError(RegulatoryAgentError):
    """Racine des erreurs du pipeline d'ingestion."""


class MissingMetadataError(IngestionError, ValueError):
    """Un champ obligatoire (contenu_json, id, source…) est absent.

    Hérite aussi de `ValueError` : les callers historiques (API `/ingest`,
    tests d'intégration) l'attrapent via `except ValueError`, et Pydantic
    v2 attend `ValueError` pour convertir un rejet en `ValidationError`.
    """

    def __init__(self, field: str, detail: str | None = None) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        message = f"champ requis manquant : {field}"
        if detail:
            message = f"{message} — {detail}"
        super().__init__(message)
        self.field = field


class InvalidDocumentError(IngestionError, ValueError):
    """Un document ne respecte pas le schéma `DocumentReglementaire`.

    Compat `ValueError` : cf. `MissingMetadataError`.
    """

    def __init__(self, reason: str, *, document_id: str | None = None) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(f"document invalide : {reason}")
        self.reason = reason
        self.document_id = document_id


class ExtractionFailedError(IngestionError):
    """Impossible d'extraire le contenu d'un fichier source (PDF, JSON, …)."""

    def __init__(self, path: str, *, reason: str | None = None) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        suffix = f" — {reason}" if reason else ""
        super().__init__(f"extraction échouée : {path}{suffix}")
        self.path = path


class DocumentAlreadyIndexedError(IngestionError):
    """Document déjà présent dans Qdrant, sans `forcer_reindexation`."""

    def __init__(self, document_id: str, existing_chunks: int) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(
            f"Document '{document_id}' déjà indexé ({existing_chunks} chunks) — "
            f"renvoyer avec forcer_reindexation=true pour le remplacer."
        )
        self.document_id = document_id
        self.existing_chunks = existing_chunks


class PayloadTooLargeError(IngestionError, ValueError):
    """Le payload d'ingestion dépasse la borne anti-DoS.

    Compat `ValueError` : `PayloadTooLargeError` est levée depuis un
    `model_validator` Pydantic qui n'enveloppe que les `ValueError`.
    """

    def __init__(self, size: int, limit: int) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(f"contenu_json trop volumineux ({size} octets > {limit})")
        self.size = size
        self.limit = limit


# ---------------------------------------------------------------------------
# Temporal (validation des dates de contexte, applicabilité)
# ---------------------------------------------------------------------------


class TemporalError(RegulatoryAgentError):
    """Racine des erreurs du raisonnement temporel."""


class InconsistentDatesError(TemporalError, ValueError):
    """Intervalle de validité incohérent (`valid_to` antérieur à `valid_from`).

    Compat `ValueError` : levée depuis un `model_validator` Pydantic.
    """

    def __init__(self, valid_from: object, valid_to: object) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(
            f"valid_to ({valid_to}) ne peut pas être antérieur "
            f"à valid_from ({valid_from})."
        )
        self.valid_from = valid_from
        self.valid_to = valid_to


class InvalidContextDateError(TemporalError, ValueError):
    """`date_contexte` est d'un type non pris en charge ou hors bornes.

    Compat `ValueError` : Pydantic (`RequeteQuestion.date_contexte`) et
    les callers historiques attendent une `ValueError`.
    """

    def __init__(self, reason: str, *, value: object | None = None) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(f"date_contexte invalide : {reason}")
        self.reason = reason
        self.value = value


class NoApplicableVersionError(TemporalError):
    """Aucune version d'article n'est applicable à la date de référence."""


class OverlappingVersionsError(TemporalError):
    """Deux versions d'un même article se chevauchent."""


class ValidityGapError(TemporalError):
    """Un intervalle de validité présente une lacune non couverte."""


# ---------------------------------------------------------------------------
# Evidence (retrieval Qdrant, ancrage citations)
# ---------------------------------------------------------------------------


class EvidenceError(RegulatoryAgentError):
    """Racine des erreurs relatives aux preuves et citations."""


class InsufficientEvidenceError(EvidenceError):
    """Retrieval n'a pas trouvé de preuve exploitable pour la requête."""


class CitationNotVerifiedError(EvidenceError):
    """Une citation n'est pas ancrée dans les chunks récupérés."""


class VectorStoreError(EvidenceError):
    """Qdrant est inaccessible ou renvoie une réponse invalide."""

    def __init__(self, collection: str, *, cause: str | None = None) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        suffix = f" : {cause}" if cause else ""
        super().__init__(f"Qdrant inaccessible (collection={collection}){suffix}")
        self.collection = collection


class PayloadDateParseError(EvidenceError, ValueError):
    """Une date de payload Qdrant n'est pas parsable.

    Compat `ValueError` : la fonction `parser_date()` documente ce type
    d'erreur comme `ValueError` (contrat historique du helper).
    """

    def __init__(self, value: object) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(f"Impossible de parser la date : {value!r}")
        self.value = value


# ---------------------------------------------------------------------------
# Inference (chargement de modèles, timeouts, sortie structurée)
# ---------------------------------------------------------------------------


class InferenceError(RegulatoryAgentError):
    """Racine des erreurs d'inférence MLX (chargement, génération, embedding)."""


class ModelLoadError(InferenceError):
    """Un modèle MLX n'a pas pu être chargé."""

    def __init__(self, model_name: str, *, cause: str | None = None) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        suffix = f" : {cause}" if cause else ""
        super().__init__(f"Impossible de charger '{model_name}'{suffix}")
        self.model_name = model_name


class ModelNotLoadedError(InferenceError):
    """Un agent tente d'utiliser un modèle qu'il n'a pas chargé."""

    def __init__(self, agent: str) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(f"Modèle {agent} non chargé")
        self.agent = agent


class ModelSwapThrottledError(InferenceError):
    """Le quota de swaps de modèle MLX par minute est épuisé (anti-DoS)."""

    def __init__(self, swaps_par_minute: int) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(
            f"Trop de swaps MLX (>{swaps_par_minute}/min) — service occupé"
        )
        self.swaps_par_minute = swaps_par_minute


class GenerationTimeoutError(InferenceError):
    """La génération MLX a dépassé le délai imparti."""

    def __init__(self, timeout_seconds: float) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(f"Appel MLX dépassé après {timeout_seconds}s")
        self.timeout_seconds = timeout_seconds


class GenerationFailedError(InferenceError):
    """La génération MLX a échoué (hors timeout)."""

    def __init__(self, model_name: str, *, cause: str) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(f"Génération échouée ({model_name}) : {cause}")
        self.model_name = model_name


class EmbeddingFailedError(InferenceError):
    """Le calcul d'embedding a échoué."""

    def __init__(  # noqa: D107 — constructeur documenté par la classe (§0.2)
        self, model_name: str, *, cause: str, batch: tuple[int, int] | None = None
    ) -> None:
        scope = f" lot {batch[0]}-{batch[1]}" if batch else ""
        super().__init__(f"Embedding échoué ({model_name}){scope} : {cause}")
        self.model_name = model_name
        self.batch = batch


class StructuredOutputError(InferenceError):
    """La sortie LLM ne respecte pas le format structuré attendu."""

    def __init__(self, agent: str, *, detail: str | None = None) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        suffix = f" : {detail}" if detail else ""
        super().__init__(f"Sortie LLM invalide ({agent}){suffix}")
        self.agent = agent


# ---------------------------------------------------------------------------
# Validation queue (Redis pending_*)
# ---------------------------------------------------------------------------


class ValidationQueueError(RegulatoryAgentError):
    """Racine des erreurs de la file de validation humaine."""


class TaskNotFoundError(ValidationQueueError, ValueError):
    """L'identifiant de tâche demandé n'existe pas dans les files pendantes.

    Compat `ValueError` : `Orchestrateur.valider_tache()` catche
    `ValueError` avant re-raise, l'API `/approve` mappe déjà cette
    branche vers un HTTP 404.
    """

    def __init__(self, tache_id: Any) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(f"Tâche introuvable : {tache_id}")
        self.tache_id = tache_id


class InvalidTransitionError(ValidationQueueError):
    """Transition de statut interdite pour une tâche de validation."""


class QueueBackendError(ValidationQueueError):
    """Le backend Redis est indisponible ou a rejeté l'opération."""

    def __init__(self, cause: str) -> None:  # noqa: D107 — constructeur documenté par la classe (§0.2)
        super().__init__(f"Validation échouée : {cause}")
        self.cause = cause


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditIntegrityError(RegulatoryAgentError):
    """La chaîne d'audit SHA-256 est rompue ou incohérente."""
