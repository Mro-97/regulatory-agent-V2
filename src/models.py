"""src/models.py — Modèles de données Pydantic pour Regulatory Agent V2
=====================================================================

Modèle de données pivot du projet. Toute la chaîne
(ingestion → indexation → retrieval → réponse → audit)
manipule ce modèle ou des projections de celui-ci.

Dépendances : pydantic >= 2.0, stdlib uniquement.
Licence : propriétaire — Regulatory Agent V2.
"""

import hashlib
import json
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from config import cfg
from pydantic import BaseModel, Field, model_validator

# Taille maximale d'un contenu JSON soumis à /ingest (octets sérialisés).
TAILLE_MAX_CONTENU_JSON = 1_000_000


# ---------------------------------------------------------------------------
# Énumérations
# ---------------------------------------------------------------------------


class RelationType(str, Enum):
    """Type de relation entre deux textes réglementaires."""

    SE_CHEVAUCHE = "se_chevauche"
    ABROGE = "abroge"
    COMPLETE = "complete"
    MODIFIE = "modifie"
    TRANSPOSE = "transpose"
    REFERENCE = "reference"


class SourceReglementaire(str, Enum):
    """Sources réglementaires reconnues par le système."""

    EUR_LEX = "EUR-Lex"
    LEGIFRANCE = "Légifrance"
    ANSSI = "ANSSI"
    CNIL = "CNIL"
    INERIS = "INERIS"
    AUTRE = "Autre"


class StatutValidation(str, Enum):
    """Statut d'une tâche soumise à validation humaine."""

    EN_ATTENTE = "en_attente"
    APPROUVE = "approuvé"
    REJETE = "rejeté"
    ESCALADE = "escaladé"


class TypeFilePendante(str, Enum):
    """Files Redis utilisées pour le human-in-the-loop."""

    LIENS = "pending_links"
    ALERTES = "pending_alerts"
    REPONSES = "pending_responses"
    POIDS = "pending_weights"


class NiveauConfiance(str, Enum):
    """Niveau de confiance associé à une réponse générée."""

    ELEVE = "élevé"
    MOYEN = "moyen"
    FAIBLE = "faible"
    INCERTAIN = "incertain"


# ---------------------------------------------------------------------------
# Domaine réglementaire
# ---------------------------------------------------------------------------


class IntervalleValidite(BaseModel):
    """Fenêtre temporelle de validité d'un article ou d'un texte.
    Un intervalle ouvert (valid_to = None) signifie que la version est en vigueur.
    Invariant : si valid_to est renseigné, il doit être >= valid_from.
    """

    valid_from: date = Field(
        ...,
        description="Date à partir de laquelle cette version est applicable (incluse).",
    )
    valid_to: date | None = Field(
        default=None,
        description="Date jusqu'à laquelle cette version est applicable (incluse). None = en vigueur.",  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
    )

    @model_validator(mode="after")
    def verifier_coherence_dates(self) -> "IntervalleValidite":
        """Vérifie que valid_to >= valid_from lorsque les deux sont renseignés."""
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError(
                f"valid_to ({self.valid_to}) ne peut pas être antérieur à valid_from ({self.valid_from})."  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
            )
        return self

    def est_applicable_a(self, date_cible: date) -> bool:
        """Retourne True si valid_from <= date_cible <= valid_to (ou valid_to absent)."""  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
        if date_cible < self.valid_from:
            return False
        if self.valid_to is not None and date_cible > self.valid_to:
            return False
        return True

    def est_ouvert(self) -> bool:
        """Indique si l'intervalle n'a pas de date de fin."""
        return self.valid_to is None


class VersionArticle(BaseModel):
    """Version spécifique d'un article réglementaire.
    Un même article peut avoir plusieurs versions successives dans le temps.
    """

    id: str = Field(..., description="Identifiant unique de cette version d'article.")
    titre: str = Field(
        ..., max_length=500, description="Titre ou intitulé de l'article."
    )
    texte: str = Field(..., description="Contenu textuel complet de l'article.")
    validite: IntervalleValidite = Field(
        ..., description="Fenêtre temporelle de validité."
    )
    citations: list[str] = Field(
        default_factory=list,
        description="Identifiants des articles référencés par cet article.",
    )
    hash_contenu: str | None = Field(
        default=None,
        description="Empreinte SHA-256 du texte, calculée automatiquement.",
    )

    @model_validator(mode="after")
    def calculer_hash_si_absent(self) -> "VersionArticle":
        """Calcule l'empreinte SHA-256 du texte si absente."""
        if not self.hash_contenu:
            self.hash_contenu = hashlib.sha256(self.texte.encode("utf-8")).hexdigest()
        return self

    def est_applicable_a(self, date_cible: date) -> bool:
        """Délègue la vérification à l'intervalle de validité."""
        return self.validite.est_applicable_a(date_cible)


class Chapitre(BaseModel):
    """Subdivision d'un texte réglementaire (chapitre, section, titre, annexe)."""

    id: str = Field(..., description="Identifiant unique du chapitre.")
    titre: str | None = Field(
        default=None, max_length=500, description="Intitulé du chapitre."
    )
    articles: list[VersionArticle] = Field(
        default_factory=list,
        description="Versions d'articles contenues dans ce chapitre.",
    )

    def articles_applicables_a(self, date_cible: date) -> list[VersionArticle]:
        """Retourne les articles applicables à la date donnée."""
        return [a for a in self.articles if a.est_applicable_a(date_cible)]


class TexteLie(BaseModel):
    """Relation entre le document courant et un autre texte réglementaire."""

    ref: str = Field(..., description="Identifiant du texte cible dans le corpus.")
    relation: RelationType = Field(..., description="Nature de la relation.")
    commentaire: str | None = Field(
        default=None, max_length=2000, description="Précision libre."
    )


class DocumentReglementaire(BaseModel):
    """Représentation canonique d'un texte réglementaire.
    Modèle pivot manipulé par toute la chaîne du système.
    """

    id: str = Field(
        ...,
        description="Identifiant unique du document. Convention : SIGLE_ANNEE_NUMERO.",
        examples=["RGPD_2016_679"],
    )
    titre: str = Field(
        ..., max_length=500, description="Titre officiel complet du texte."
    )
    source: SourceReglementaire = Field(..., description="Source institutionnelle.")
    url_source: str | None = Field(
        default=None, description="URL canonique sur le site source."
    )
    publication_date: date = Field(..., description="Date de publication officielle.")
    entry_into_force: date = Field(..., description="Date d'entrée en vigueur.")
    repeal_date: date | None = Field(
        default=None, description="Date d'abrogation. None = en vigueur."
    )
    version: str = Field(..., description="Étiquette de version ISO 8601 (YYYY-MM-DD).")
    themes: list[str] = Field(default_factory=list, description="Tags thématiques.")
    chapitres: list[Chapitre] = Field(
        default_factory=list, description="Structure du document."
    )
    textes_lies: list[TexteLie] = Field(
        default_factory=list, description="Relations avec d'autres textes."
    )
    hash_document: str | None = Field(
        default=None, description="Hash SHA-256 du document."
    )
    date_indexation: datetime | None = Field(
        default=None, description="Horodatage UTC d'indexation Qdrant."
    )
    metadonnees_supplementaires: dict[str, Any] = Field(
        default_factory=dict,
        description="Métadonnées libres spécifiques à la source.",
    )

    def calculer_hash(self) -> str:
        """Hash SHA-256 du contenu réglementaire (hors hash_document et date_indexation)."""  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
        donnees = self.model_dump(
            exclude={"hash_document", "date_indexation"}, mode="json"
        )
        payload = json.dumps(donnees, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def articles_applicables_a(self, date_cible: date) -> list[VersionArticle]:
        """Retourne tous les articles applicables à la date donnée, tous chapitres confondus."""  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
        resultat: list[VersionArticle] = []
        for chapitre in self.chapitres:
            resultat.extend(chapitre.articles_applicables_a(date_cible))
        return resultat

    def est_en_vigueur_a(self, date_cible: date) -> bool:
        """Indique si le document est en vigueur à la date donnée."""
        if date_cible < self.entry_into_force:
            return False
        if self.repeal_date is not None and date_cible >= self.repeal_date:
            return False
        return True


# ---------------------------------------------------------------------------
# Chunks Qdrant
# ---------------------------------------------------------------------------


class MetadonneesChunk(BaseModel):
    """Payload Qdrant attaché à chaque vecteur indexé."""

    chunk_id: str = Field(..., description="Identifiant unique du chunk.")
    document_id: str = Field(..., description="Document source.")
    chapitre_id: str | None = Field(default=None, description="Chapitre source.")
    article_id: str = Field(..., description="Version d'article source.")
    source: SourceReglementaire = Field(..., description="Source institutionnelle.")
    themes: list[str] = Field(
        default_factory=list, description="Thèmes hérités du document."
    )
    valid_from: date = Field(..., description="Début de validité.")
    valid_to: date | None = Field(default=None, description="Fin de validité.")
    texte_chunk: str = Field(..., description="Contenu textuel du chunk.")
    position_dans_article: int = Field(
        default=0, ge=0, description="Indice du chunk dans l'article."
    )

    def est_applicable_a(self, date_cible: date) -> bool:
        """Indique si ce chunk est issu d'une version applicable à la date donnée."""
        if date_cible < self.valid_from:
            return False
        if self.valid_to is not None and date_cible > self.valid_to:
            return False
        return True


# ---------------------------------------------------------------------------
# Audit et traçabilité
# ---------------------------------------------------------------------------


class EvidenceRecuperee(BaseModel):
    """Passage réglementaire récupéré et utilisé comme preuve dans une réponse."""

    chunk_id: str = Field(..., description="Identifiant Qdrant du chunk.")
    document_id: str = Field(..., description="Document source.")
    article_id: str = Field(..., description="Version d'article source.")
    texte_extrait: str = Field(..., description="Contenu textuel exact du chunk.")
    score_similarite: float | None = Field(default=None, ge=0.0, le=1.0)
    valid_from: date = Field(..., description="Début de validité.")
    valid_to: date | None = Field(default=None, description="Fin de validité.")


class SortieAgent(BaseModel):
    """Sortie d'un agent spécialisé enregistrée pour l'audit."""

    nom_agent: str = Field(..., description="Nom de l'agent.")
    machine: str = Field(..., description="Machine d'exécution.")
    horodatage: datetime = Field(default_factory=lambda: datetime.now(UTC))
    contenu: dict[str, Any] = Field(default_factory=dict)
    duree_ms: int | None = Field(default=None, ge=0)


class EnregistrementAudit(BaseModel):
    """Trace complète d'une requête. Chaînage SHA-256 pour détecter toute altération.
    Chaîne : requête → documents → chunks → agents → réponse → citations → validation.
    """

    request_id: UUID = Field(default_factory=uuid4)
    horodatage: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_query: str = Field(..., description="Question brute de l'utilisateur.")
    date_contexte: date | None = Field(
        default=None, description="Date réglementaire de contexte."
    )
    documents_recuperes: list[str] = Field(default_factory=list)
    evidences: list[EvidenceRecuperee] = Field(default_factory=list)
    agents_executes: list[SortieAgent] = Field(default_factory=list)
    reponse_finale: str = Field(default="")
    niveau_confiance: NiveauConfiance = Field(default=NiveauConfiance.INCERTAIN)
    necessite_validation_humaine: bool = Field(default=False)
    hash_precedent: str | None = Field(
        default=None, description="Hash du record précédent."
    )
    hash_courant: str | None = Field(default=None, description="Hash de ce record.")

    def calculer_hash(self) -> str:
        """Hash SHA-256 de cet enregistrement (hors hash_courant)."""
        donnees = self.model_dump(exclude={"hash_courant"}, mode="json")
        payload = json.dumps(donnees, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Human-in-the-loop
# ---------------------------------------------------------------------------


class TacheValidation(BaseModel):
    """Tâche en attente de validation humaine dans une file Redis."""

    tache_id: UUID = Field(default_factory=uuid4)
    type_file: TypeFilePendante = Field(..., description="File Redis de destination.")
    statut: StatutValidation = Field(default=StatutValidation.EN_ATTENTE)
    horodatage_creation: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    horodatage_traitement: datetime | None = Field(default=None)
    request_id: UUID | None = Field(default=None)
    contenu: dict[str, Any] = Field(default_factory=dict)
    commentaire_validateur: str | None = Field(default=None, max_length=2000)
    escaladee: bool = Field(default=False)


class AlerteWatcher(BaseModel):
    """Alerte générée par le Watcher lors de la détection d'une modification."""

    alerte_id: UUID = Field(default_factory=uuid4)
    source: SourceReglementaire = Field(
        ..., description="Source où la modification a été détectée."
    )
    url_detectee: str = Field(..., description="URL du document modifié.")
    document_id_concerne: str | None = Field(default=None)
    hash_precedent: str = Field(..., description="Hash SHA-256 avant modification.")
    hash_nouveau: str = Field(..., description="Hash SHA-256 après modification.")
    horodatage_detection: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    description_modification: str | None = Field(default=None, max_length=2000)
    tache_validation_id: UUID | None = Field(default=None)


# ---------------------------------------------------------------------------
# Schémas API
# ---------------------------------------------------------------------------


class RequeteQuestion(BaseModel):
    """Corps de la requête POST /ask."""

    question: str = Field(
        ...,
        min_length=3,
        max_length=cfg.question_max_length,
        description="Question réglementaire.",
    )
    date_contexte: date | None = Field(
        default=None, description="Date de contexte réglementaire."
    )
    filtres_themes: list[str] = Field(default_factory=list)
    filtres_sources: list[SourceReglementaire] = Field(default_factory=list)
    demander_validation_humaine: bool = Field(default=False)


class ReponseQuestion(BaseModel):
    """Corps de la réponse POST /ask."""

    request_id: UUID
    reponse: str
    evidences: list[EvidenceRecuperee] = Field(default_factory=list)
    niveau_confiance: NiveauConfiance
    en_attente_validation: bool = Field(default=False)
    tache_validation_id: UUID | None = Field(default=None)


class RequeteIngestion(BaseModel):
    """Corps de la requête POST /ingest."""

    source: SourceReglementaire
    url: str | None = Field(default=None, max_length=2048)
    contenu_json: dict[str, Any] | None = Field(default=None)
    forcer_reindexation: bool = Field(default=False)

    @model_validator(mode="after")
    def verifier_taille_contenu(self) -> "RequeteIngestion":
        """Refuse un contenu JSON trop volumineux (anti-DoS)."""
        if self.contenu_json is not None:
            taille = len(json.dumps(self.contenu_json, ensure_ascii=False))
            if taille > TAILLE_MAX_CONTENU_JSON:
                raise ValueError(
                    f"contenu_json trop volumineux ({taille} octets > {TAILLE_MAX_CONTENU_JSON})"  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
                )
        return self


class ReponseIngestion(BaseModel):
    """Corps de la réponse POST /ingest."""

    document_id: str
    chunks_indexes: int
    hash_document: str
    nouvelle_version: bool


class ReponseTachesPendantes(BaseModel):
    """Corps de la réponse GET /pending."""

    total: int
    par_file: dict[str, int] = Field(default_factory=dict)
    taches: list[TacheValidation] = Field(default_factory=list)


class RequeteDecisionValidation(BaseModel):
    """Corps des requêtes POST /approve et POST /reject."""

    tache_id: UUID
    commentaire: str | None = Field(default=None, max_length=2000)


class ReponseDecisionValidation(BaseModel):
    """Corps de la réponse /approve et /reject."""

    tache_id: UUID
    nouveau_statut: StatutValidation
    horodatage_traitement: datetime
