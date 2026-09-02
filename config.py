"""config.py — Configuration centralisée de Regulatory Agent V2
=============================================================

Toutes les valeurs sont lues depuis les variables d'environnement.
Un fichier .env à la racine du projet est chargé automatiquement.
Aucune valeur sensible ne doit être codée en dur ici.
"""  # noqa: D205, D415

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Parametres(BaseSettings):
    """Paramètres globaux du système, injectables via .env ou variables d'environnement.
    protected_namespaces=() supprime les warnings Pydantic sur les champs model_*.
    """  # noqa: D205

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
    orchestrateur_mode: str = Field(
        default="real",
        description=(
            "Mode d'exécution de l'orchestrateur : 'real' (agents MLX + Qdrant + "
            "Redis) ou 'mock' (réponses simulées, aucune dépendance externe)."
        ),
    )

    # ------------------------------------------------------------------
    # Serveur API — architecture unique m4pro2 (§3.1 CONTEXTE_PROJET)
    # ------------------------------------------------------------------
    api_host: str = Field(
        default="127.0.0.1",
        description="Bind. Utiliser 0.0.0.0 uniquement derrière un proxy.",
    )
    api_port: int = Field(default=8000)
    api_workers: int = Field(default=1)

    # ------------------------------------------------------------------
    # Sécurité API
    # ------------------------------------------------------------------
    api_key: str = Field(
        default="",
        description="Clé API partagée (en-tête X-API-Key). Vide = accès refusé (fail-closed).",  # noqa: E501
    )
    cors_origins_str: str = Field(
        default=(
            "http://localhost,http://127.0.0.1,"
            "http://localhost:8000,http://127.0.0.1:8000"
        ),
        alias="CORS_ORIGINS",
        description=(
            "Origines navigateur autorisées (CORS), séparées par des virgules. "
            "Un navigateur envoie `Origin: http(s)://host:port` (avec port) — "
            "chaque combinaison hôte:port doit être listée explicitement, la "
            "comparaison est exacte. En prod : restreindre au vrai domaine."
        ),
    )
    exposer_docs: bool = Field(
        default=False,
        description="Expose /docs et /redoc (Swagger). Désactivé par défaut.",
    )
    taille_max_requete_octets: int = Field(
        default=2_097_152, description="Taille maximale du corps de requête (2 Mo)."
    )
    taille_max_contenu_json: int = Field(
        default=1_000_000,
        description=(
            "Taille max (octets) du champ `contenu_json` d'un RequeteIngestion "
            "après sérialisation JSON (garde-fou anti-DoS applicatif)."
        ),
    )
    question_max_length: int = Field(
        default=4000, description="Longueur max d'une question."
    )

    # Rate limiting (par IP) — limiteur mémoire, fallback local mono-process.
    rate_limit_max_requetes: int = Field(default=30)
    rate_limit_fenetre_secondes: int = Field(default=60)

    # Rate limiting Redis — compteur partagé entre workers, clé {api_key}:{ip}.
    redis_rate_limit_max_requests: int = Field(
        default=30,
        description=(
            "Seuil du rate limiter Redis (src/rate_limit_redis.py), partagé "
            "entre workers. Distinct de `rate_limit_max_requetes` qui ne "
            "borne que le fallback mémoire local activé si Redis est KO."
        ),
    )
    redis_rate_limit_window_seconds: int = Field(
        default=60,
        description=(
            "Fenêtre (secondes) du rate limiter Redis. Le TTL est posé sur "
            "la clé `rl:{api_key}:{ip}` à sa première requête."
        ),
    )

    # ------------------------------------------------------------------
    # Modèles MLX — chargement local sur m4pro2, un seul actif à la fois
    # (§2.6 + §5 CONTEXTE_PROJET). Les *_host restent à 127.0.0.1 pour
    # rétrocompat des tests qui liraient encore ces champs.
    # ------------------------------------------------------------------
    modele_orchestrateur: str = Field(
        default="mlx-community/Llama-3.2-3B-Instruct-4bit",
        description="Modèle MLX de génération pour le routage.",
    )

    modele_embedding: str = Field(
        default="sentence-transformers/BAAI/bge-m3",
        description=(
            "Modèle d'embedding (requêtes + chunks). Deux backends dans "
            "MLXEmbedding : 'sentence-transformers/<id>' (torch, CPU/MPS, "
            "stable — défaut) ou un identifiant natif mlx-embeddings "
            "('models/bge-m3-mlx' local). La voie MLX crashe 'Stream(gpu, N)' "
            "sur certaines versions de mlx ; le repo HF nu 'BAAI/bge-m3' n'a "
            "pas de safetensors. DOIT produire `embedding_dimension`."
        ),
    )
    embedding_dimension: int = Field(
        default=1024,
        description=(
            "Dimension des vecteurs d'embedding. DOIT égaler "
            "`qdrant_vecteur_taille` (le boot refuse un écart) — sinon toute "
            "recherche vectorielle échoue silencieusement."
        ),
    )

    modele_retriever: str = Field(default="mlx-community/Mistral-7B-Instruct-v0.3-4bit")
    modele_temporal: str = Field(default="mlx-community/Qwen2.5-7B-Instruct-4bit")
    modele_explainer: str = Field(default="mlx-community/Qwen2.5-7B-Instruct-4bit")
    modele_citation: str = Field(default="mlx-community/Mistral-7B-Instruct-v0.3-4bit")
    modele_conflit: str = Field(
        default="mlx-community/DeepSeek-R1-Distill-Qwen-14B-4bit"
    )

    # ------------------------------------------------------------------
    # Génération MLX — paramètres par défaut
    # ------------------------------------------------------------------
    mlx_max_tokens: int = Field(default=1024)
    mlx_temperature: float = Field(default=0.1)
    mlx_top_p: float = Field(default=0.9)
    mlx_timeout_seconds: float = Field(
        default=60.0,
        description=(
            "Délai maximum (secondes) accordé à un appel MLX (generate / encode). "
            "0 ou négatif = pas de timeout. Empêche un modèle bloqué ou un "
            "prompt pathologique de figer l'API indéfiniment."
        ),
    )
    mlx_load_timeout_seconds: float = Field(
        default=180.0,
        description=(
            "Délai maximum (secondes) accordé au chargement d'un modèle MLX. "
            "Distinct de `mlx_timeout_seconds` (borne l'inférence) car un "
            "premier chargement légitime — mmap sur ~1 GB de poids, "
            "initialisation du tokenizer — peut dépasser la minute. "
            "0 ou négatif = pas de timeout. Empêche un fichier de poids "
            "corrompu (ex. safetensors sparse) de figer l'API au démarrage."
        ),
    )
    ingest_mode_sanitizer: str = Field(
        default="annoter",
        description=(
            "Politique du sanitizer d'ingestion contre le prompt injection "
            "persistant : 'off' (aucune action), 'annoter' (défaut : "
            "encapsule les chunks suspects entre marqueurs défensifs), "
            "'bloquer' (rejette les chunks DANGEREUX). Cf. "
            "src/ingest_sanitizer.py pour les patterns détectés."
        ),
    )
    mlx_max_swaps_par_minute: int = Field(
        default=3,
        description=(
            "Nombre maximum de swaps de modèle MLX (unload + load) tolérés "
            "par minute — anti-DoS. Un attaquant qui alterne rapidement des "
            "questions classées vers Qwen/Mistral/DeepSeek forcerait des "
            "swaps ~1 GB à la chaîne, gelant l'API. Au-delà de ce seuil, "
            "`_CacheGeneration.get()` lève `ModelSwapThrottledError` et le "
            "pipeline dégrade en réponse « service temporairement occupé »."
        ),
    )
    mlx_taille_max_texte_embedding: int = Field(
        default=8000,
        description=(
            "Longueur max (caractères) d'un texte envoyé à l'embedding. "
            "`max_length=512` et `truncation=True` passés à emb_generate() "
            "agissent sur les *tokens*, pas les caractères ; sur un texte "
            "très long, la tokenisation elle-même peut consommer une mémoire "
            "excessive avant que la troncature ne s'applique (crash observé "
            "en ingérant REACH sans chunking en amont). Filet de sécurité pour "
            "les appelants qui court-circuiteraient le chunking."
        ),
    )

    # ------------------------------------------------------------------
    # Qdrant — local sur m4pro2 (§3.1 CONTEXTE_PROJET)
    # ------------------------------------------------------------------
    qdrant_host: str = Field(default="127.0.0.1")
    qdrant_port: int = Field(default=6333)
    qdrant_https: bool = Field(default=False)
    qdrant_api_key: str = Field(default="")
    qdrant_collection: str = Field(default="regulatory_chunks")
    qdrant_vecteur_taille: int = Field(default=1024)
    qdrant_top_k: int = Field(default=15)

    # ------------------------------------------------------------------
    # Redis — local sur m4pro2 (§3.1 CONTEXTE_PROJET)
    # ------------------------------------------------------------------
    redis_host: str = Field(default="127.0.0.1")
    redis_port: int = Field(default=6379)
    redis_password: str = Field(default="")
    redis_db: int = Field(default=0)
    redis_ttl_cache: int = Field(default=3600)

    # ------------------------------------------------------------------
    # PostgreSQL — local sur m4pro2 (§3.1 CONTEXTE_PROJET). DSN via .env
    # uniquement, jamais de valeur en dur.
    # ------------------------------------------------------------------
    postgres_dsn: str = Field(
        default="",
        description="DSN PostgreSQL ex. postgresql://user:motdepasse@127.0.0.1:5432/base.",
    )
    postgres_pool_min_size: int = Field(default=1)
    postgres_pool_max_size: int = Field(default=5)
    postgres_command_timeout: float = Field(default=10.0)

    # ------------------------------------------------------------------
    # Human-in-the-loop
    # ------------------------------------------------------------------
    hitl_delai_escalade_heures: int = Field(default=72)
    hitl_seuil_confiance_validation: float = Field(default=0.6)

    # Seuils de confiance de l'Explainer, sur la similarité cosinus moyenne
    # des preuves. Calibrés pour le backend d'embedding courant
    # (sentence-transformers/BAAI/bge-m3) — à re-mesurer si `modele_embedding`
    # change (les distributions de score varient d'un modèle à l'autre).
    explainer_confiance_moyenne_elevee: float = Field(
        default=0.50,
        description=(
            "Moyenne des scores de similarité au-delà de laquelle la réponse "
            "Explainer est notée « élevé »."
        ),
    )
    explainer_confiance_moyenne_faible: float = Field(
        default=0.42,
        description=(
            "En dessous de cette moyenne, la réponse Explainer est notée "
            "« faible ». Entre les deux seuils : « moyen »."
        ),
    )
    citation_verifie_ancrage: bool = Field(
        default=True,
        description=(
            "Passe le texte de la réponse à l'agent Citation (Mistral 7B) "
            "pour vérifier que chaque affirmation est ancrée dans une "
            "preuve. Une citation non retrouvée abaisse `niveau_confiance` "
            "d'un cran (INCERTAIN si aucune n'est ancrée). Coût : un "
            "chargement modèle + un swap MLX par requête. `false` = "
            "vérification déterministe seule (rapide, sans effet confiance)."
        ),
    )

    # ------------------------------------------------------------------
    # Watcher
    # ------------------------------------------------------------------
    watcher_intervalle_heures: int = Field(
        default=48,
        description=(
            "Intervalle de veille en heures (défaut 48 h). Les textes de "
            "loi ne changent pas quotidiennement — une fréquence trop haute "
            "génère des alertes bruyantes (contenu HTML dynamique) sans "
            "bénéfice."
        ),
    )
    watcher_follow_redirects: bool = Field(
        default=False,
        description="Suivre les redirections HTTP (réduit la surface SSRF).",
    )
    watcher_max_essais: int = Field(
        default=3,
        description=(
            "Nombre de tentatives par URL en cas d'échec réseau ou d'erreur 5xx. "
            "1 = pas de reprise, comportement d'avant."
        ),
    )
    watcher_backoff_secondes: float = Field(
        default=2.0,
        description=(
            "Base du backoff exponentiel entre tentatives : "
            "attente = base * 2^(tentative-1) secondes."
        ),
    )
    watcher_actif: bool = Field(
        default=True,
        description=(
            "Active la boucle de surveillance dans le process API. "
            "Passer à false quand le Watcher tourne dans un process séparé "
            "(gunicorn multi-worker, ou déploiement avec supervisord/systemd) "
            "pour éviter les cycles concurrents et libérer la boucle asyncio "
            "de l'API des requêtes réseau du Watcher."
        ),
    )
    watcher_delai_demarrage_secondes: float = Field(
        default=30.0,
        description=(
            "Délai après le boot avant le premier cycle Watcher. Laisse à "
            "l'API le temps de terminer son startup et de servir /health "
            "sans concurrence I/O du Watcher."
        ),
    )

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    audit_local_path: Path = Field(default=Path("data/audit.jsonl"))
    feedback_local_path: Path = Field(
        default=Path("data/feedback.jsonl"),
        description=(
            "Fichier JSONL des signalements utilisateur (POST /feedback) — "
            "revue qualité et jeu de calibration des seuils de confiance."
        ),
    )

    # ------------------------------------------------------------------
    # Chemins locaux
    # ------------------------------------------------------------------
    @property
    def racine(self) -> Path:  # noqa: D102
        return Path(__file__).parent

    @property
    def dossier_data_raw(self) -> Path:  # noqa: D102
        return self.racine / "data" / "raw"

    @property
    def dossier_data_indexed(self) -> Path:  # noqa: D102
        return self.racine / "data" / "indexed"

    @property
    def dossier_data_pending(self) -> Path:  # noqa: D102
        return self.racine / "data" / "pending"

    @property
    def cors_origins(self) -> list[str]:
        """Origines CORS parsées depuis cors_origins_str."""
        return [o.strip() for o in self.cors_origins_str.split(",") if o.strip()]


cfg = Parametres()
