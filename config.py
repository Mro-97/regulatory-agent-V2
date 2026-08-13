"""
config.py — Configuration centralisée de Regulatory Agent V2
=============================================================

Toutes les valeurs sont lues depuis les variables d'environnement.
Un fichier .env à la racine du projet est chargé automatiquement.
Aucune valeur sensible ne doit être codée en dur ici.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Parametres(BaseSettings):
    """
    Paramètres globaux du système, injectables via .env ou variables d'environnement.
    protected_namespaces=() supprime les warnings Pydantic sur les champs model_*.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        protected_namespaces=(),
    )

    # ------------------------------------------------------------------
    # Identité
    # ------------------------------------------------------------------
    app_nom: str = Field(default="Regulatory Agent V2")
    app_version: str = Field(default="0.1.0")
    debug: bool = Field(default=False)

    # ------------------------------------------------------------------
    # Serveur API (Mac A)
    # ------------------------------------------------------------------
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    api_workers: int = Field(default=1)

    # ------------------------------------------------------------------
    # Modèles MLX — Mac A (orchestrateur)
    # ------------------------------------------------------------------
    modele_orchestrateur: str = Field(
        default="mlx-community/Llama-3.2-3B-Instruct-4bit",
        description="Modèle MLX de génération pour l'orchestrateur (Mac A).",
    )

    # ------------------------------------------------------------------
    # Modèle d'embedding dédié — Mac B
    # Utilisé par le Retriever ET le pipeline d'ingestion.
    # DOIT être identique dans les deux pour que les vecteurs soient compatibles.
    # ------------------------------------------------------------------
    modele_embedding: str = Field(
        default="bge-m3",
        description=(
            "Nom du modèle d'embedding dans le registre mlx-embedding-models. "
            "bge-m3 : multilingue, dimension 1024, compatible French regulatory text."
        ),
    )
    embedding_dimension: int = Field(
        default=1024,
        description="Dimension des vecteurs produits par modele_embedding.",
    )

    # ------------------------------------------------------------------
    # Modèles MLX de génération — Mac B
    # ------------------------------------------------------------------
    mac_b_host: str = Field(default="192.168.1.11")
    mac_b_port: int = Field(default=8001)

    modele_retriever: str = Field(
        default="mlx-community/Mistral-7B-Instruct-v0.3-4bit",
        description="Modèle de génération pour le Retriever (Mac B).",
    )
    modele_temporal: str = Field(
        default="mlx-community/Qwen2.5-7B-Instruct-4bit",
        description="Modèle de génération pour l'agent Temporal (Mac B).",
    )
    modele_explainer: str = Field(
        default="mlx-community/Qwen2.5-7B-Instruct-4bit",
        description="Modèle de génération pour l'Explainer (Mac B).",
    )
    modele_citation: str = Field(
        default="mlx-community/Mistral-7B-Instruct-v0.3-4bit",
        description="Modèle de génération pour l'agent Citation (Mac B).",
    )

    # ------------------------------------------------------------------
    # Modèles MLX de génération — Mac C
    # ------------------------------------------------------------------
    mac_c_host: str = Field(default="192.168.1.12")
    mac_c_port: int = Field(default=8002)

    modele_conflit: str = Field(
        default="mlx-community/DeepSeek-R1-Distill-Qwen-14B-4bit",
        description="Modèle de génération pour l'agent Conflict (Mac C).",
    )

    # ------------------------------------------------------------------
    # Génération MLX — paramètres par défaut
    # ------------------------------------------------------------------
    mlx_max_tokens: int = Field(default=1024)
    mlx_temperature: float = Field(default=0.1)
    mlx_top_p: float = Field(default=0.9)

    # ------------------------------------------------------------------
    # Qdrant (Mac B)
    # ------------------------------------------------------------------
    qdrant_host: str = Field(default="192.168.1.11")
    qdrant_port: int = Field(default=6333)
    qdrant_collection: str = Field(default="regulatory_chunks")
    qdrant_vecteur_taille: int = Field(default=1024)
    qdrant_top_k: int = Field(default=15)

    # ------------------------------------------------------------------
    # Redis (Mac A)
    # ------------------------------------------------------------------
    redis_host: str = Field(default="127.0.0.1")
    redis_port: int = Field(default=6379)
    redis_db: int = Field(default=0)
    redis_ttl_cache: int = Field(default=3600)

    # ------------------------------------------------------------------
    # PostgreSQL (Mac C)
    # ------------------------------------------------------------------
    postgres_dsn: str = Field(
        default="postgresql://raguser:ragpass@192.168.1.12:5432/regulatory",
    )

    # ------------------------------------------------------------------
    # Human-in-the-loop
    # ------------------------------------------------------------------
    hitl_delai_escalade_heures: int = Field(default=72)
    hitl_seuil_confiance_validation: float = Field(default=0.6)

    # ------------------------------------------------------------------
    # Watcher
    # ------------------------------------------------------------------
    watcher_intervalle_heures: int = Field(default=6)

    # ------------------------------------------------------------------
    # Chemins locaux
    # ------------------------------------------------------------------
    @property
    def racine(self):
        return Path(__file__).parent

    @property
    def dossier_data_raw(self):
        return self.racine / "data" / "raw"

    @property
    def dossier_data_indexed(self):
        return self.racine / "data" / "indexed"

    @property
    def dossier_data_pending(self):
        return self.racine / "data" / "pending"


cfg = Parametres()
