# Regulatory Agent V2

Système local de veille réglementaire et d'assistance IA — 100 % local, sans API externe.

## Architecture

| Machine | Rôle | Port | LLM |
|---|---|---|---|
| mini-1 (M4 16 Go) | Hub / API / Redis / Interface | 8000 | — |
| m4pro1 (M4 Pro 24 Go) | Moteur / Retrieval | 8001 | Mistral 7B + Qwen 2.5 7B |
| m4pro2 (M4 Pro 24 Go) | Expert / Conflit | 8002 | Mistral 7B + Qwen 2.5 7B + DeepSeek-R1 14B |

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

## Démarrage des services

    # Qdrant (mini-1 et m4pro1 — port 6335)
    cd ~/regulatory-agent && ./qdrant --config-path /tmp/qdrant_config.yaml > /tmp/qdrant.log 2>&1 &

    # Qdrant (m4pro2 — port 6333)
    cd ~/regulatory-agent && ./qdrant > /tmp/qdrant.log 2>&1 &

    # Redis (tous)
    ~/redis-stable/src/redis-server --daemonize yes --port 6379

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
