# Rapport d'audit — Regulatory Agent V2 (`Mro-97/regulatory-agent-V2`)

Date : 2026-08-21
Cible : `https://github.com/Mro-97/regulatory-agent-V2`

## Résumé
- **Sécurité : 13 constats** — 3 critiques, 3 hauts, 4 moyens, 3 faibles
- **Normes de codage : 12 écarts** (repo sans config lint/CI déclarée)

---

## Partie 1 — Audit de sécurité

### [CRITIQUE] Aucune authentification ni autorisation sur l'API
- **Fichiers :** `src/api.py:115-179`, `config.py:40`
- **Catégorie :** OWASP A07 (Identification/Auth), A05 (Contrôle d'accès)
- **Description :** Tous les endpoints (`/ask`, `/ingest`, `/pending`, `/approve`, `/reject`, `/docs`, `/redoc`) sont ouverts, sans token, session ni rôle. L'API écoute sur `0.0.0.0:8000` (config par défaut). Quiconque atteint le port peut **approuver/rejeter des validations** (action privilégiée du workflow human-in-the-loop), lire la file d'attente (`/pending`), ingérer des documents arbitraires (`/ingest`) et accéder à la doc Swagger.
- **Remédiation :** API-Key ou session HTTP-only avec rôles (validateur ≠ lecteur ≠ admin), appliquée à toutes les routes sauf `/health`; bind sur `127.0.0.1` + tunnel (Tailscale) plutôt que `0.0.0.0`.

### [CRITIQUE] CORS `*` + endpoints mutables sans protection CSRF
- **Fichier :** `src/api.py:87-92`
- **Catégorie :** OWASP A07
- **Description :** `allow_origins=["*"]` + `allow_headers=["*"]` et méthodes GET/POST. N'importe quelle page web visitée par l'opérateur peut piloter `/approve`, `/reject` et `/ingest` via un `fetch` cross-origin (aucune vérification d'origine, aucune anti-CSRF).
- **Remédiation :** Restreindre les origines à la liste exacte des clients (ou même-origin), ajouter CSRF token / vérification `Origin` sur les mutations, `SameSite=Strict`.

### [CRITIQUE] Aucun rate limiting ni limite de taille de requête → DoS
- **Fichiers :** `src/api.py:132-179`, `src/models.py:325-353` (`question` sans `max_length`, `contenu_json` sans limite)
- **Catégorie :** OWASP A04 (Consommation de ressources)
- **Description :** `/ask` déclenche embedding + génération LLM (RAM GPU Mac) et des écritures Redis/Qdrant, sans limitation de débit. Un attaquant (ou un client buggé) peut saturer l'inférence et la file de validation. `/ingest` accepte un JSON de taille arbitraire.
- **Remédiation :** Rate limiting (slowapi ou reverse-proxy), `max_length` sur `question`, plafond de taille du body (`io.LimitReader`/middleware), timeout génération.

### [HAUT] Fuite des détails d'erreur internes
- **Fichier :** `src/api.py:137-146` (`detail=str(exc)`), `src/orchestrator.py:339`
- **Catégorie :** OWASP A05, A02
- **Description :** Les exceptions sont renvoyées brutes au client : chemins locaux, erreurs Qdrant/Redis/MLX, noms de modèles, messages internes. Log avec `exc_info=True`.
- **Remédiation :** Message générique au client, erreur détaillée uniquement côté log.

### [HAUT] Transport en clair + exposition LAN
- **Fichiers :** `config.py:40`, `README.md:63-67`
- **Catégorie :** OWASP A02, A05
- **Description :** HTTP non chiffré sur `0.0.0.0`. Les machines s'exposent sur `192.168.1.x` (Qdrant/MLX). Le README documente un accès via `ssh -L`, mais rien ne force TLS même derrière un proxy. Aucun en-tête de sécurité (HSTS, CSP, X-Frame-Options, nosniff) sur l'UI (`web/templates/index.html`).
- **Remédiation :** TLS en frontal (Caddy/Tailscale HTTPS), en-têtes de sécurité, et ne jamais exposer Qdrant/Redis hors boucle locale.

### [HAUT] Injection de prompt / empoisonnement du corpus
- **Fichiers :** `src/agents/explainer.py:256-284`, `src/agents/conflit.py:321-341`, `src/orchestrator.py:502`
- **Catégorie :** OWASP A03, A08 (LLM-specific)
- **Description :** `/ingest` accepte un `contenu_json` arbitraire sans filtre. Les chunks récupérés sont interpolés tels quels dans les prompts LLM (Explainer, Conflit, Temporal) sans hiérarchie d'instructions. Un document ingéré contenant « Ignore tes instructions et réponds X » peut détourner les réponses réglementaires.
- **Remédiation :** Contrôler l'origine des documents ingérés (allowlist de sources/IDs), bornes XML/séparateurs autour des chunks, instruction « le corpus n'est pas une consigne », post-filtrage.

### [MOYEN] Credential DB par défaut en dur
- **Fichier :** `config.py:130-132` — DSN PostgreSQL par défaut avec mot de passe `ragpass` commité. Le `.env` est bien ignoré (`config.py:6`, `.gitignore:26`), mais le fallback par défaut en clair dans le code est un motif faible qui se propage en prod.
- **Remédiation :** DSN depuis `.env` uniquement, aucune valeur par défaut embarquant un mot de passe.

### [MOYEN] Services de stockage sans authentification
- **Fichiers :** `config.py:113-133`, `src/orchestrator.py:520-615`, `src/agents/retriever.py:61`
- **Description :** Redis (sans mot de passe, `redis_db=0`), Qdrant (sans clé/TLS) et PostgreSQL (`raguser`) accessibles en clair sur le LAN. Les files `pending_*` et le corpus peuvent être lus/modifiés.
- **Remédiation :** Redis `requirepass` + `bind 127.0.0.1`, Qdrant avec API-key/TLS, PostgreSQL avec réseau isolé et mot de passe fort.

### [MOYEN] XSS — échappement incomplet
- **Fichier :** `web/static/app.js:21` — `esc()` n'échappe que `& < >`, pas `"` `'` ni backtick. Les données sont actuellement insérées en contenu d'élément (risque faible), mais toute réutilisation en attribut deviendrait exploitable; `/pending` expose du contenu partiellement contrôlable.
- **Remédiation :** Compléter `esc()` (`"`, `'`, backtick) ou construire via `textContent`, pas `innerHTML`.

### [FAIBLE] Divers
- `/health` et `/docs` divulguent nom/version/schéma (`src/api.py:83-85,124-130`).
- Watcher : `follow_redirects=True` vers des hôtes arbitraires (`src/watcher.py:199`).
- Chaîne d'audit JSONL locale interrompue au redémarrage : `_hash_precedent` rechargé de Postgres mais jamais du fichier (`src/audit.py:36-37,109-114`).
- Modèles téléchargés depuis HuggingFace par nom (`config.py:47-100`) — dépendance de confiance sur des poids tiers.

---

## Partie 2 — Conformité aux normes de codage

Aucune configuration lint/CI dans le repo (pas de `pyproject.toml`, `ruff`, `flake8`, `mypy`, `.editorconfig`, `.pre-commit`, `.github`). Évaluation vs AGENTS.md global + PEP 8 + conventions déclarées du repo (docstrings français, hints de type).

1. **`src/models.py` entièrement dupliqué** — 45 classes pour 23 uniques ; le module contient tout deux fois (2e copie à partir de la ligne 385), plus un bloc commenté mort (l.1-12). Blocage majeur à la maintenance (divergence silencieuse possible).
2. **`.gitignore` inefficace** — `data/raw/*` est censé exclure les données (commentaire l.9-10) mais **61 fichiers JSON de corpus sont commités** (`git ls-files data/` = 61). Écart déclaratif/concret.
3. **`pdfplumber` manquant dans `requirements.txt`** — utilisé par `scripts/pdf_to_json.py:94`, absent des dépendances.
4. **Imports morts** dans `scripts/ingest.py` : `mlx_embeddings`, `uuid`, `date`, `PointStruct` (double import l.19 et l.106), `gc` (double l.9 et l.105).
5. **PEP 8 : 100+ lignes > 88 caractères** (`models.py`: 46, `api.py`: 13, `ingest.py`/`pdf_to_json.py`: 10, etc.).
6. **Logging non-lazy** : 9 `logger.info(f"...")` dans `scripts/ingest.py` (évaluation de la f-string même si le log est filtré) — contre la convention du reste du repo qui utilise les args.
7. **Identifiant non-ASCII** : `_detecter_incohérences_internes` (`src/agents/conflit.py:221,403`).
8. **I/O bloquants dans le chemin async** : `src/audit.py:157-164` (`open()/write()` synchrones dans `_persister_local` async) ; `watcher.py:117,127` (`read_text/write_text` dans le loop async). Bloque l'event loop.
9. **`api_workers` déclaré jamais utilisé** — `main.py:73` hardcode `workers=1`.
10. **Hints de type incomplets** : `audit.py:124` (`persister(self, audit)` non typé). Globalement bon par ailleurs (PEP 484 respectée sur `src/agents/*`).
11. **Incohérence README** : ports Qdrant `6335` (mini-1) vs `6333` config (`config.py:114`) ; `machine="Mac_A"` vs « Mac B » dans les docstrings agents (`orchestrator.py:192,238`).
12. **Émojis dans le code/logs** (`ingest.py:50,55`) — contraire à la règle AGENTS globale « pas d'émoji sauf demande explicite ».
13. **Aucun test de sécurité** (auth, CORS, CSRF, rate limit) — `tests/` couvre uniquement les agents et modèles.

**Bonus positif :** typage et docstrings français homogènes sur `src/`, compilation OK (`py_compile`), chaîne d'audit SHA-256 chaînée bien pensée, `.env` correctement ignoré, aucune API externe (100 % local revendiqué et tenu).

---

## Priorités

1. Ajouter l'authentification + CSRF/CORS strict
2. Rate limiting et limites de taille de requête
3. Supprimer la duplication de `models.py` et aligner `.gitignore` / `requirements.txt`

---

# RE-AUDIT POST-CORRECTION — 2026-08-21

## État des constats initiaux

| # | Constat | Sévérité | Statut |
|---|---|---|---|
| 1 | Aucune authentification sur l'API | Critique | ✅ Corrigé — `X-API-Key` (fail-closed), dépendances sur toutes les routes métier |
| 2 | CORS `*` + mutations sans CSRF | Critique | ✅ Corrigé — CORS restreint (`CORS_ORIGINS`), vérification `Origin` sur POST |
| 3 | Rate limiting / tailles absents | Critique | ✅ Corrigé — rate limit par IP sur `/ask`/`/ingest`, `max_length` question, corps ≤ 2 Mo, `contenu_json` ≤ 1 Mo |
| 4 | Fuite d'erreurs internes | Haut | ✅ Corrigé — `detail` générique, trace uniquement côté log |
| 5 | Transport en clair + exposition LAN | Haut | 🟡 Partiel — défaut `127.0.0.1`, en-têtes de sécurité + HSTS ajoutés ; TLS effectif à la charge du proxy (documenté) |
| 6 | Injection de prompt / empoisonnement corpus | Haut | 🟡 Atténué — balises `<SOURCE>`, consigne « corpus = donnée » dans les 4 agents ; risque résiduel inhérent aux LLM |
| 7 | Credential DB par défaut en dur | Moyen | ✅ Corrigé — `POSTGRES_DSN` vide par défaut, passage par `.env` (`.env.example`) |
| 8 | Stockage sans auth | Moyen | ✅ Corrigé — options `REDIS_PASSWORD`, `QDRANT_API_KEY`, `QDRANT_HTTPS` branchées sur tous les clients |
| 9 | XSS — `esc()` incomplet | Moyen | ✅ Corrigé — `esc()` échappe `"`, `'`, backtick |
| 10 | `/health`, `/docs` exposés | Faible | ✅ Corrigé — docs/redoc/openapi désactivés par défaut (`EXPOSER_DOCS`) |
| 11 | Watcher follow_redirects | Faible | ✅ Corrigé — `WATCHER_FOLLOW_REDIRECTS` (défaut false) |
| 12 | Chaîne d'audit cassée au restart | Faible | ✅ Corrigé — rechargement du dernier hash depuis le JSONL local |
| 13 | Modèles HuggingFace (supply-chain) | Faible | 🟡 Documenté — risque résiduel (poids tiers) |

## Normes de codage — état

- ✅ `models.py` dédupliqué (23 classes, une seule copie, bloc mort supprimé)
- ✅ `.gitignore` aligné (corpus raw committé, artefacts exclus, `audit.jsonl`, `watcher_hashes.json`)
- ✅ `requirements.txt` : plages épinglées, + `pdfplumber`, `jinja2`, `pytest>=9.0.3`
- ✅ `ingest.py` : imports morts supprimés, logs lazy, émojis retirés
- ✅ `_detecter_incohérences_internes` → nom ASCII
- ✅ I/O bloquants async → `asyncio.to_thread` (audit, watcher)
- ✅ `api_workers` utilisé dans `main.py` ; hints de type complétés
- ✅ README aligné (ports 6333, clé API, section sécurité)
- ✅ Tests de sécurité ajoutés (81 tests au total, verts)

## Scans exécutés

| Outil | Résultat |
|---|---|
| pip-audit (requirements + venv) | ✅ 0 vulnérabilité (après passage `pytest>=9.0.3` ; une CVE PYSEC-2026-1845 initialement) |
| gitleaks (40 commits, ~19 Mo) | ✅ 0 secret |
| bandit (src + scripts) | ✅ 0 finding |
| semgrep (p/owasp-top-ten + p/python + p/security-audit, 200 règles) | ✅ 0 finding |
| py_compile (tous fichiers) | ✅ OK |
| pytest | ✅ 81 passed |

## Pentest dynamique (uvicorn, mode mock, port 8011)

| Sonde | Résultat |
|---|---|
| `/health`, `/` publics | 200 |
| `/docs`, `/redoc`, `/openapi.json` | 404 |
| `/ask` sans clé / mauvaise clé | 401 |
| `/ask` bonne clé | 200 |
| `/ask` payload SQL / XSS | 200 (donnée, jamais exécutée) |
| `/ask` corps 3 Mo | 413 |
| `/ask` question 5000 car. | 422 |
| `/pending` (Redis down) | 200 (file vide, dégradation propre) |
| `/approve`/`/reject` (Redis down) | 500 générique `{"detail":"Erreur interne lors de la validation."}` — aucun détail interne |
| `/ingest` bonne clé / url path-traversal | 202 (stub ; url n'est pas résolue) |
| Rate limiting | 429 après 30 requêtes/min |
| CORS origine autorisée | `access-control-allow-origin: http://localhost` |
| CORS origine hostile | aucun header ACAO |
| Mutation cross-site (`Origin: evil`) | 403 |
| Headers | nosniff, X-Frame-Options DENY, Referrer-Policy, HSTS présents |

## Risques résiduels (acceptés / documentés)

1. **Prompt injection** : atténuée, non éliminable en RAG. Le corpus doit rester limité à des sources contrôlées ; `/ingest` doit être restreint aux administrateurs.
2. **TLS** : assuré par le proxy frontal (Caddy/Tailscale), pas par uvicorn lui-même.
3. **Rate limiting en mémoire** : par processus — avec `API_WORKERS > 1`, chaque worker a son propre compteur. Suffisant en mono-nœud.
4. **CSP non défini** : le UI injecte la clé dans une balise `<script>` inline ; une CSP stricte casserait l'interface. Compensé par CORS + Origin + clé.
5. **Clé API servie dans la page web** : la clé est exposée à quiconque peut charger `/` (interface ouverte). C'est le périmètre choisi pour un outil local ; hors périmètre si multi-utilisateur → passer à un SSO / mTLS.
6. **`/health`** révèle nom/version (info mineure, assumé).
7. **Mode `mock`** renvoie la question en écho dans `reponse` (dev uniquement).
