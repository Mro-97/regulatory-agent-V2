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
    environnement: str = Field(
        default="dev",
        alias="ENVIRONNEMENT",
        description=(
            "'dev' (poste local, tolérant) ou 'prod'. En 'prod' le démarrage "
            "REFUSE : debug=true, exposer_docs=true, une clé API absente / "
            "placeholder / < 32 car., et api_host=0.0.0.0 sans trusted_proxies. "
            "La validation tourne aussi bien sous `python3 main.py` que sous "
            "gunicorn/uvicorn (lifespan de src/api.py)."
        ),
    )
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
    # RBAC — clés API HACHÉES (SHA-256). Aucune clé en clair n'est stockée
    # côté serveur : voir src/auth.py + scripts/gerer_cles.py.
    api_keys_file: Path = Field(
        default=Path("data/api_keys.json"),
        alias="API_KEYS_FILE",
        description=(
            "Fichier JSON des clés API : [{hash, role, label, cree}]. `hash` = "
            "SHA-256 hex de la clé (jamais la clé). Rôles : user < validateur "
            "< admin. Géré par scripts/gerer_cles.py. Doit être en 0600."
        ),
    )
    api_keys_hachees_str: str = Field(
        default="",
        alias="API_KEYS_HACHEES",
        description=(
            "Clés hachées via variable d'env plutôt que fichier, format "
            "`sha256hex:role:label` séparés par des virgules. Cumulatif avec "
            "api_keys_file."
        ),
    )
    # --- Voie dépréciée : clés EN CLAIR dans l'environnement ---
    # Tolérée en dev (rôle admin, avec warning), REFUSÉE si environnement=prod.
    api_key: str = Field(
        default="",
        description="[DÉPRÉCIÉ] Clé API en clair. Utiliser api_keys_file. Refusée en prod.",  # noqa: E501
    )
    api_keys_str: str = Field(
        default="",
        alias="API_KEYS",
        description="[DÉPRÉCIÉ] Clés API en clair (virgules). Refusées en prod.",
    )
    trusted_proxies_str: str = Field(
        default="",
        alias="TRUSTED_PROXIES",
        description=(
            "IP (ou CIDR) des proxys inverses de confiance, séparées par des "
            "virgules. SEULES ces IP voient leurs en-têtes X-Forwarded-For / "
            "X-Real-IP / X-Forwarded-Proto pris en compte (IP client réelle, "
            "rate-limit, schéma). Vide = on ne fait jamais confiance à ces "
            "en-têtes (le pair TCP direct fait foi)."
        ),
    )
    forcer_https: bool = Field(
        default=False,
        description=(
            "Redirige toute requête http vers https (308), sauf /health. "
            "Le schéma d'origine est lu dans X-Forwarded-Proto UNIQUEMENT si "
            "le pair est un trusted_proxy. À activer en prod derrière un "
            "terminateur TLS."
        ),
    )
    ask_max_concurrent: int = Field(
        default=2,
        description=(
            "Nombre maximum de pipelines /ask (et /ask/stream) exécutés "
            "simultanément. Au-delà, l'API répond 503 immédiatement plutôt "
            "que d'empiler des générations MLX (un timeout MLX n'interrompt "
            "pas le thread : sans ce plafond, des générations abandonnées "
            "s'accumulent). 0 = pas de plafond."
        ),
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

    # Rate limiting (par IP d'origine) — limiteur mémoire, fallback mono-process.
    # 60/min : marge confortable pour l'UI (polling /pending + questions),
    # bloque net un attaquant (atteint en ~2 s). S'applique à TOUS les
    # endpoints sauf /health et l'interface.
    rate_limit_max_requetes: int = Field(default=60)
    rate_limit_fenetre_secondes: int = Field(default=60)

    # Rate limiting Redis — compteur partagé entre workers, clé {scope}:{ip}
    # où scope = empreinte de la clé API si valide, sinon un seau commun
    # `invalide` (empêche le contournement par rotation de l'en-tête X-API-Key).
    redis_rate_limit_max_requests: int = Field(
        default=60,
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
    #
    # Un modèle par ROLE, jamais de modèle d'aiguillage : le routage
    # (`src/classification.py`) est déterministe (deux regex + date de
    # contexte). Aucun LLM ne décide donc de la route suivie — un petit
    # modèle décisionnel serait à la fois une surface d'injection
    # (prompt injection orientant le routage) et une source de
    # non-déterminisme sur un chemin de sécurité.
    # ------------------------------------------------------------------
    modele_embedding: str = Field(
        default="models/bge-m3-mlx",
        description=(
            "Modèle d'embedding (requêtes + chunks). Deux backends dans "
            "Seule la voie MLX native subsiste (mlx-embeddings) : "
            "'models/bge-m3-mlx' (copie locale de BAAI/bge-m3) par défaut. La "
            "voie sentence-transformers a été retirée le 2026-09-16 — elle "
            "imposait torch/transformers/scipy/sklearn/opencv (~1 Go) pour des "
            "vecteurs non interchangeables avec MLX (~0,80 de similarité "
            "cosinus). DOIT produire `embedding_dimension`."
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
    # Pas de `mlx_temperature` : la température est un choix par RÔLE, posé
    # explicitement par chaque agent à son `get_model()` — 0.0 pour les agents
    # de raisonnement (conflit, citation, temporel), 0.1 pour la rédaction
    # Explainer. Un réglage global imposerait la même valeur aux deux, ce qui
    # est sémantiquement faux. `mlx_top_p` est commun à tous, donc configurable.
    mlx_top_p: float = Field(
        default=0.9,
        description=(
            "Seuil de nucleus sampling commun à tous les agents. Appliqué par "
            "`src/mlx_utils.py` quand l'appelant ne fixe pas de valeur."
        ),
    )
    mlx_timeout_seconds: float = Field(
        default=60.0,
        description=(
            "Délai maximum (secondes) accordé à un appel MLX (generate / encode). "
            "0 ou négatif = pas de timeout. Empêche un modèle bloqué ou un "
            "prompt pathologique de figer l'API indéfiniment."
        ),
    )
    ingest_taille_min_chunk: int = Field(
        default=80,
        description=(
            "Longueur minimale (caractères, après strip) d'un chunk indexé. "
            "En dessous, le chunk est écarté : les micro-fragments type "
            "« paragraphe 3 » ou « — Article 33 » polluent le retrieval "
            "sans apporter d'information. Sert aussi de critère à "
            "`scripts/dedup_qdrant.py --purger-micro`."
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
    mlx_modeles_residents_max: int = Field(
        default=2,
        description=(
            "Nombre de modèles de génération gardés en mémoire. Une question "
            "type en utilise DEUX — Qwen pour la synthèse (Explainer/Temporal), "
            "Mistral pour la vérification des citations : avec un seul slot, "
            "chaque question provoquait deux swaps et, au 4e swap en moins "
            "d'une minute, le throttle refusait le chargement et la synthèse "
            "échouait (mesuré le 2026-09-21 : 3e question consécutive servie "
            "en vidage de passages). 2 laisse cohabiter les deux modèles "
            "7B 4-bit (~9 Go) : confortable sur m4pro2 (24 Go), à baisser à 1 "
            "sur une machine à 16 Go. Au-delà du plafond, le modèle le plus "
            "ancien est déchargé (LRU) et cette éviction consomme le quota "
            "`mlx_max_swaps_par_minute`."
        ),
    )
    mlx_max_swaps_par_minute: int = Field(
        default=3,
        description=(
            "Nombre maximum d'ÉVICTIONS de modèle MLX (unload + reload) "
            "tolérées par minute — anti-DoS. Un attaquant qui alterne "
            "rapidement des questions classées vers Qwen/Mistral/DeepSeek "
            "forcerait des swaps de plusieurs Go à la chaîne, gelant l'API. "
            "Au-delà de ce seuil, `_CacheGeneration` lève "
            "`ModelSwapThrottledError` : l'Explainer bascule alors sur "
            "l'assemblage brut (`mode_reponse='assemblage'`), ce qui reste "
            "visible pour l'appelant. Ce quota ne s'applique plus au simple "
            "changement de modèle d'une même question, seulement aux "
            "évictions réelles une fois `mlx_modeles_residents_max` atteint."
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
    qdrant_score_min: float = Field(
        default=0.72,
        description=(
            "Seuil de pertinence (similarité cosinus) des passes sémantiques. "
            "En dessous, le passage est écarté ; si RIEN ne dépasse le seuil, "
            "le retrieval ne renvoie rien et l'API s'abstient au lieu de "
            "présenter 15 passages hors sujet comme pertinents. "
            "Calibré sur bge-m3 le 2026-09-21 : questions absurdes "
            "0,63-0,71 (« 123 789 33333 », « bonjour », « recette au "
            "chocolat »), questions réglementaires réelles 0,73-0,88 (16 "
            "mesurées). C'est un FILET, pas un séparateur : « réparer une "
            "fuite d'eau » atteint 0,77 par recouvrement lexical et passe. "
            "La passe « articles cités » est exemptée (cf. "
            "src/agents/retriever_helpers.py). À re-mesurer si "
            "`modele_embedding` change."
        ),
    )

    # ------------------------------------------------------------------
    # Redis — local sur m4pro2 (§3.1 CONTEXTE_PROJET)
    # ------------------------------------------------------------------
    redis_host: str = Field(default="127.0.0.1")
    redis_port: int = Field(default=6379)
    redis_password: str = Field(default="")
    redis_db: int = Field(default=0)
    redis_timeout_secondes: float = Field(
        default=1.0,
        description=(
            "Délai (s) des lectures/écritures Redis du rate-limiter. Un Redis "
            "plus lent fait basculer le comptage sur le repli mémoire (dont "
            "la portée est le processus, pas le couple clé/IP) : trop court, "
            "le quota change de sémantique au moindre à-coup de Redis ; trop "
            "long, une panne Redis ralentit chaque requête. `statut()` du "
            "limiteur expose le nombre de bascules."
        ),
    )

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

    # Seuils de confiance de l'Explainer, sur la similarité cosinus moyenne
    # des preuves. Calibrés pour le backend d'embedding courant
    # (bge-m3, voie MLX) — à re-mesurer si `modele_embedding`
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
    watcher_max_redirections: int = Field(
        default=5,
        description=(
            "Nombre maximal de redirections suivies par requête du Watcher. "
            "Chaque saut est validé (schéma, port, IP publique) avant d'être "
            "suivi ; au-delà, la chaîne est abandonnée."
        ),
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

    @property
    def cles_api_clair(self) -> list[str]:
        """Clés API en clair issues de l'env (voie dépréciée), dédupliquées."""
        brut = [self.api_key, *self.api_keys_str.split(",")]
        vues: list[str] = []
        for c in (x.strip() for x in brut):
            if c and c not in vues:
                vues.append(c)
        return vues

    @property
    def trusted_proxies(self) -> list[str]:
        """IP/CIDR des proxys de confiance, parsés depuis trusted_proxies_str."""
        return [p.strip() for p in self.trusted_proxies_str.split(",") if p.strip()]


cfg = Parametres()
