"""src/demarrage.py — Validation de la configuration au démarrage.

Extrait de `main.py` pour casser un import circulaire : `src/api.py` importait
`main` AU NIVEAU MODULE pour appeler ce validateur dans son lifespan, alors que
`main` importe `src.api` (paresseusement, via `__getattr__`) pour lancer le
serveur. Le validateur vit désormais dans un module feuille ; `main` le
ré-exporte pour ses appelants historiques (tests, `scripts/launcher.py`).

Rien ici ne dépend de FastAPI, de MLX ni de l'orchestrateur : ce sont des
invariants de configuration, vérifiés avant tout démarrage de service.
"""

from __future__ import annotations

from urllib.parse import urlparse

from config import cfg


def valider_configuration_demarrage() -> list[str]:
    """Vérifie au démarrage les invariants critiques (retourne les erreurs).

    Appelée par `main.py` (lancement direct) ET par le lifespan de
    `src/api.py` — donc effective aussi sous `gunicorn main:app` /
    `uvicorn main:app`, où `__main__` ne s'exécute pas.
    """
    erreurs: list[str] = []
    _erreur_api_key_manquante(erreurs)
    _erreur_cors_origines_invalides(erreurs)
    _erreur_rate_limiter_multi_worker(erreurs)
    _erreur_debug_et_docs_exposes(erreurs)
    _erreur_dimension_embedding_incoherente(erreurs)
    _erreurs_mode_production(erreurs)
    return erreurs


def _erreur_cors_origines_invalides(erreurs: list[str]) -> None:
    """Refuse une origine CORS wildcard ou malformée.

    `CORS_ORIGINS=*` ferait accepter par le navigateur toute origine tierce :
    combiné au cookie de session (envoyé automatiquement), cela rouvrirait la
    porte que la vérification CSRF et SameSite ferment. Une entrée sans schéma
    (`monsite.fr`) n'est jamais renvoyée par un navigateur, qui envoie
    toujours `scheme://hote[:port]` — c'est donc une erreur de configuration
    silencieuse, qui se traduit par un CORS refusé sans explication.
    """
    for origine in cfg.cors_origins:
        if "*" in origine:
            erreurs.append(
                f"CORS_ORIGINS contient '{origine}' — le joker '*' est interdit : "
                "il autoriserait toute origine tierce à porter le cookie de session. "
                "Lister explicitement les origines (scheme://hote[:port])."
            )
            continue
        analyse = urlparse(origine)
        if analyse.scheme not in {"http", "https"} or not analyse.netloc:
            erreurs.append(
                f"CORS_ORIGINS contient '{origine}' — attendu "
                "'scheme://hote[:port]' (ex. https://monsite.fr). Un navigateur "
                "n'envoie jamais d'origine sans schéma : cette entrée ne "
                "correspondra à rien."
            )


def _erreurs_mode_production(erreurs: list[str]) -> None:
    """Invariants supplémentaires quand `ENVIRONNEMENT=prod` (durcissement).

    En prod on refuse tout ce qui, en dev, n'est qu'un avertissement :
    verbosité de debug, docs exposées, et un bind public sans proxy de
    confiance déclaré (sinon rate-limit et journal d'accès sont aveugles
    à l'IP réelle des clients).
    """
    if cfg.environnement != "prod":
        return
    if cfg.debug:
        erreurs.append(
            "ENVIRONNEMENT=prod avec DEBUG=true — interdit (tracebacks/verbosité)."
        )
    if cfg.exposer_docs:
        erreurs.append(
            "ENVIRONNEMENT=prod avec EXPOSER_DOCS=true — interdit (fuite de schémas)."
        )
    if cfg.api_host == "0.0.0.0" and not cfg.trusted_proxies:  # noqa: S104
        erreurs.append(
            "ENVIRONNEMENT=prod, bind 0.0.0.0 et TRUSTED_PROXIES vide — l'IP "
            "client vue serait celle du proxy (rate-limit et logs cassés). "
            "Déclarer l'IP du proxy dans TRUSTED_PROXIES."
        )
    if cfg.cles_api_clair:
        erreurs.append(
            "ENVIRONNEMENT=prod avec API_KEY/API_KEYS en clair dans "
            "l'environnement — interdit. Migrer vers data/api_keys.json "
            "(scripts/gerer_cles.py)."
        )
    _erreur_permissions_fichier_cles(erreurs)


def _erreur_permissions_fichier_cles(erreurs: list[str]) -> None:
    """Refuse en prod un `api_keys_file` lisible par le groupe ou tout le monde."""
    import stat

    chemin = cfg.api_keys_file
    if not chemin.exists():
        return
    mode = chemin.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        erreurs.append(
            f"{chemin} accessible au groupe/autres (mode {oct(mode & 0o777)}) — "
            "chmod 600 requis (contient des hashes de clés)."
        )


def _erreur_dimension_embedding_incoherente(erreurs: list[str]) -> None:
    """Refuse le boot si `embedding_dimension` != `qdrant_vecteur_taille`.

    Un modèle d'embedding qui ne produit pas la dimension de la collection
    Qdrant fait échouer toutes les recherches silencieusement (0 evidence,
    réponses non ancrées) — observé quand `.env` pointait `BAAI/bge-m3`
    (KO) au lieu de `sentence-transformers/BAAI/bge-m3`.
    """
    if cfg.embedding_dimension != cfg.qdrant_vecteur_taille:
        erreurs.append(
            f"EMBEDDING_DIMENSION ({cfg.embedding_dimension}) != "
            f"QDRANT_VECTEUR_TAILLE ({cfg.qdrant_vecteur_taille}) — le modèle "
            f"'{cfg.modele_embedding}' et la collection Qdrant sont "
            "incompatibles, toute recherche renverra 0 résultat."
        )


def _erreur_debug_et_docs_exposes(erreurs: list[str]) -> None:
    """Refuse la combinaison `DEBUG=true` + `EXPOSER_DOCS=true` (fuite prod).

    Debug tolère les logs verbeux en dev, docs exposées tolère Swagger.
    Les deux ensemble = fuite d'info assurée en prod (schémas + verbosité
    des tracebacks). Un déploiement doit passer au moins l'un des deux
    à false.
    """
    if cfg.debug and cfg.exposer_docs:
        erreurs.append(
            "DEBUG=true et EXPOSER_DOCS=true simultanément — combinaison "
            "interdite en dehors du poste de développement (fuite de schémas "
            "et de tracebacks). Passer au moins l'un des deux à false."
        )


_API_KEY_PLACEHOLDER = "remplacez-par-une-cle-longue-et-aleatoire"
_API_KEY_LONGUEUR_MIN = 32
_COMMANDE_GEN_CLE = "python3 scripts/gerer_cles.py generer --role admin --label <nom>"


def _erreur_api_key_manquante(erreurs: list[str]) -> None:
    """Refuse le boot si le magasin de clés API est vide ou sans admin.

    Le magasin (`src.auth`) agrège : `data/api_keys.json`, `API_KEYS_HACHEES`,
    et — voie dépréciée — les clés en clair `API_KEY`/`API_KEYS` (ignorées si
    `environnement=prod`). Cas fail-closed :
    - magasin vide → aucun endpoint métier ne répond (503) ;
    - aucune clé `admin` → `/ingest` et la gestion deviennent inatteignables ;
    - clé en clair < 32 caractères (voie dépréciée) → bruteforce trop accessible ;
    - placeholder de `.env.example`.
    """
    from src.auth import Role, compte_par_role, magasin_configure

    if not magasin_configure():
        erreurs.append(
            "Magasin de clés API vide (ni data/api_keys.json, ni "
            "API_KEYS_HACHEES, ni API_KEY) — générer une clé : " + _COMMANDE_GEN_CLE
        )
        return
    if compte_par_role().get(Role.ADMIN, 0) == 0:
        erreurs.append(
            "Aucune clé de rôle 'admin' — /ingest et la gestion seraient "
            "inatteignables. " + _COMMANDE_GEN_CLE
        )
    clair = cfg.cles_api_clair
    if any(c == _API_KEY_PLACEHOLDER for c in clair):
        erreurs.append(
            "API_KEY = valeur placeholder de .env.example — remplacer. "
            + _COMMANDE_GEN_CLE
        )
    courtes = [c for c in clair if len(c) < _API_KEY_LONGUEUR_MIN]
    if courtes:
        erreurs.append(
            f"{len(courtes)} clé(s) API en clair trop courte(s) "
            f"(< {_API_KEY_LONGUEUR_MIN} caractères) — risque de bruteforce."
        )


def _erreur_rate_limiter_multi_worker(erreurs: list[str]) -> None:
    """Signale que le rate limiter en mémoire n'est pas partagé entre workers."""
    if cfg.api_workers > 1 and cfg.rate_limit_max_requetes > 0:
        erreurs.append(
            f"api_workers={cfg.api_workers} > 1 avec rate_limit_max_requetes>0 : "
            "le rate limiter en mémoire n'est pas partagé entre workers, "
            "la limite effective est multipliée par le nombre de workers. "
            "Utiliser un backend Redis partagé ou fixer api_workers=1."
        )
