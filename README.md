# Regulatory Agent V2

Système local de veille réglementaire et d'assistance IA — 100 % local, sans API externe.

## Architecture

| Machine | Rôle | Port | LLM |
|---|---|---|---|
| mini-1 (M4 16 Go) | Hub / API / Redis / Interface (Mac A) | 8000 | — |
| m4pro1 (M4 Pro 24 Go) | Moteur / Retrieval (Mac B) | 8001 | Mistral 7B + Qwen 2.5 7B |
| m4pro2 (M4 Pro 24 Go) | Expert / Conflit (Mac C) | 8002 | Mistral 7B + Qwen 2.5 7B + DeepSeek-R1 14B |

Qdrant est exposé sur le port **6333** (config `QDRANT_PORT`), Redis sur **6379** (config `REDIS_PORT`).

## Prérequis

- macOS Apple Silicon (M4 / M4 Pro)
- Python 3.13 via uv
- Qdrant (binaire standalone)
- Redis (compilé depuis les sources)
- Tailscale (accès distant)

## Installation rapide

    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.local/bin/env
    uv python install 3.13
    git clone https://github.com/Mro-97/regulatory-agent-V2 ~/regulatory-agent
    cd ~/regulatory-agent
    uv venv --python 3.13 venv && source venv/bin/activate
    uv pip install -r requirements.txt
    mkdir -p data/{raw,indexed,pending,qdrant_storage} logs models/bge-m3-mlx
    cp .env.example .env   # puis renseigner API_KEY, POSTGRES_DSN…

## Sécurité — à lire avant de démarrer

- **Authentification :** tous les endpoints (sauf `/health` et l'interface web) exigent
  l'en-tête `X-API-Key`. La clé est définie par `API_KEY` dans `.env`.
  Sans clé configurée, l'API refuse tout accès (fail-closed).
- **Exposition :** par défaut l'API écoute sur `127.0.0.1`. Utiliser `0.0.0.0`
  uniquement derrière un proxy TLS (Caddy/Tailscale HTTPS). Les ports Qdrant/Redis
  ne doivent jamais être exposés hors boucle locale.
- **Credentials :** `POSTGRES_DSN`, `REDIS_PASSWORD`, `QDRANT_API_KEY` passent par
  `.env` (jamais dans le code). Aucune valeur sensible n'est codée en dur.
- **Swagger :** `/docs` et `/redoc` sont désactivés par défaut (`EXPOSER_DOCS=false`).
- **CORS :** restreint aux origines de `CORS_ORIGINS`; les mutations vérifient l'en-tête
  `Origin`. Rate limiting (par IP) sur `/ask` et `/ingest`.
- **Générer une clé :** `openssl rand -hex 32`

## Démarrage des services

    # Qdrant (toutes les machines — port 6333)
    cd ~/regulatory-agent && ./qdrant > /tmp/qdrant.log 2>&1 &

    # Redis (tous)
    ~/redis-stable/src/redis-server --daemonize yes --port 6379 --requirepass <mot-de-passe>

    # API
    cd ~/regulatory-agent && source venv/bin/activate && python3 main.py

## Ingestion du corpus

    # Initialiser Qdrant
    python3 scripts/setup_qdrant.py

    # Ingérer un JSON
    python3 scripts/ingest.py --json data/raw/mon_document.json

    # Ingérer un PDF
    python3 scripts/pdf_to_json.py --fichier doc.pdf --id MON_ID --source EUR-Lex --publication 2024-01-01 --vigueur 2024-06-01
    python3 scripts/ingest.py --json data/raw/MON_ID.json

    # Corpus complet
    bash scripts/ingerer_datasets.sh

## Accès via Tailscale

    ssh -L 9001:127.0.0.1:8000 mro@mini-1 -N &
    ssh -L 9002:127.0.0.1:8001 mro@m4pro1 -N &
    ssh -L 9003:127.0.0.1:8002 mro@m4pro2 -N &

    open http://127.0.0.1:9001

    curl -s -X POST http://127.0.0.1:9001/ask \
      -H "Content-Type: application/json" \
      -H "X-API-Key: $API_KEY" \
      -d '{"question":"Obligations de sécurité RGPD ?"}' | python3 -m json.tool

## Endpoints API

| Endpoint | Méthode | Description |
|---|---|---|
| /health | GET | État du système |
| /ask | POST | Poser une question réglementaire |
| /pending | GET | Tâches en attente de validation |
| /approve | POST | Approuver une tâche |
| /reject | POST | Rejeter une tâche |
| /docs | GET | Documentation Swagger |

## Tests

    python3 -m pytest tests/ -q --tb=short
    bash scripts/tests_rag.sh

Les tests de sécurité (`tests/test_api_security.py`) couvrent : authentification,
CORS, anti-CSRF, rate limiting, limites de taille, masquage des erreurs.

## Modèles LLM

| Agent | Modèle | Machine |
|---|---|---|
| Embedding | bge-m3 (safetensors MLX) | Tous |
| Retriever | Mistral 7B Instruct v0.3 4-bit | m4pro1, m4pro2 |
| Temporal | Qwen 2.5 7B Instruct 4-bit | m4pro1, m4pro2 |
| Explainer | Qwen 2.5 7B Instruct 4-bit | m4pro1, m4pro2 |
| Citation | Mistral 7B Instruct v0.3 4-bit | m4pro1, m4pro2 |
| Conflit | DeepSeek-R1-Distill-Qwen-14B 4-bit | m4pro2 |

## Sources surveillées

- EUR-Lex, Légifrance, ANSSI, CNIL, INERIS

## Structure

    regulatory-agent-V2/
    ├── config.py
    ├── main.py
    ├── requirements.txt
    ├── src/
    │   ├── models.py
    │   ├── mlx_utils.py
    │   ├── orchestrator.py
    │   ├── api.py
    │   ├── audit.py
    │   ├── watcher.py
    │   └── agents/
    │       ├── retriever.py
    │       ├── temporal.py
    │       ├── explainer.py
    │       ├── citation.py
    │       └── conflit.py
    ├── scripts/
    ├── tests/
    ├── web/
    └── data/

## Licence

Propriétaire — Usage interne uniquement.
