 
# Regulatory Agent V2

**Système local de veille réglementaire et d'assistance IA pour l'industrie.**

[![License: Proprietary](https://img.shields.io/badge/License-Proprietary-red.svg)](https://github.com/Mro-97/regulatory-agent-V2)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/)
[![MLX](https://img.shields.io/badge/MLX-Apple_Silicon-purple.svg)](https://github.com/ml-explore/mlx)

---

## 📌 Présentation

Regulatory Agent V2 est un système **100 % local** de veille réglementaire et d'assistance IA destiné aux ingénieurs, techniciens, responsables QHSE, DPO et RSSI.

Il permet de :
- **Rechercher** des informations réglementaires en langage naturel.
- **Identifier** la version applicable d’un texte à une date donnée.
- **Détecter** les modifications réglementaires (Watcher).
- **Valider** les décisions critiques avec un humain dans la boucle.
- **Auditer** l’intégralité des requêtes (traçabilité SHA-256).

**Architecture** : Le système est conçu pour fonctionner sur une **seule machine** (Mac Mini `m4pro2` ou MacBook M4 Pro). L’ancienne architecture distribuée (3 Mac Mini) est abandonnée.

---

## ⚙️ Prérequis matériels

- **Mac Apple Silicon** (M4 Pro recommandé) — 24 Go de RAM minimum.
- **Environnement** : Python 3.13 (via `uv`), Git, Homebrew (optionnel).
- **Stockage** : ~20 Go pour les modèles MLX et le corpus réglementaire.

---

## 🛠️ Stack technique

| Composant | Technologie |
| :--- | :--- |
| **Langage** | Python 3.13 |
| **API** | FastAPI + Uvicorn |
| **Inférence** | MLX (Apple Silicon) |
| **Base vectorielle** | Qdrant |
| **Cache / Files d’attente** | Redis |
| **Base de données (audit)** | PostgreSQL (en cours) |
| **Embeddings** | bge-m3 (dim 1024) |
| **Modèles LLM** | Llama 3.2 3B, Mistral 7B, Qwen 2.5 7B, DeepSeek-R1 14B |

---

## 🧠 Agents

| Agent | Modèle | Rôle |
| :--- | :--- | :--- |
| **Orchestrateur** | Llama 3.2 3B | Routage des requêtes |
| **Retriever** | Mistral 7B | Recherche vectorielle dans Qdrant |
| **Temporal** | Qwen 2.5 7B | Filtrage par date de validité |
| **Explainer** | Qwen 2.5 7B | Synthèse en langage clair |
| **Citation** | Mistral 7B | Génération des références exactes |
| **Conflit** | DeepSeek-R1 14B | Détection de contradictions (appelé sur ~20 % des requêtes) |

---

## 🔐 Sécurité

- **Authentification** : Clé API (`X-API-Key`) requise sur tous les endpoints métier.
- **Rate limiting** : Limitation des requêtes par IP sur `/ask` et `/ingest`.
- **Sanitisation** : Nettoyage des prompts pour éviter les injections.
- **CORS** : Restreint à l’origine de l’interface web.
- **Audit trail** : Chaînage SHA-256 pour chaque requête.
- **Swagger désactivé** par défaut en production.

---

## 👁️ Watcher (veille réglementaire)

Le Watcher surveille les sources réglementaires :
- EUR-Lex
- Légifrance
- ANSSI
- CNIL
- INERIS

Il détecte les modifications (via hash/date), génère une alerte et la soumet à validation humaine avant mise à jour de la base.

---

## 🚀 Installation

```bash
# 1. Cloner le dépôt
git clone https://github.com/Mro-97/regulatory-agent-V2.git
cd regulatory-agent-V2

# 2. Créer l’environnement virtuel (avec uv)
uv venv --python 3.13
source venv/bin/activate

# 3. Installer les dépendances
uv pip install -r requirements.txt
```

---

## 🔧 Configuration

1. **Créer le fichier `.env`** à partir de l’exemple :

```bash
cp .env.example .env
```

2. **Générer une clé API** :

```bash
openssl rand -hex 32
```

3. **Remplir le fichier `.env`** avec les valeurs suivantes :

```ini
REGULATORY_API_KEY=<ta_clé_hex>

# Services (tout en local)
QDRANT_HOST=127.0.0.1
QDRANT_PORT=6333
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
API_HOST=127.0.0.1
API_PORT=8000
```

---

## 🧪 Lancer les services

### Démarrer Qdrant (base vectorielle)

```bash
./qdrant --port 6333 > /tmp/qdrant.log 2>&1 &
```

### Démarrer Redis (cache & files d’attente)

```bash
redis-server --port 6379 &
```

### Démarrer l’API

```bash
python main.py
```

L’API est accessible sur `http://127.0.0.1:8000`.

---

## 📦 Ingestion des données

### Convertir un PDF en JSON

```bash
python3 scripts/pdf_to_json.py \
  --fichier data/raw/mon_document.pdf \
  --id MON_DOC_ID \
  --titre "Titre du document" \
  --source "EUR-Lex" \
  --publication 2024-01-01 \
  --vigueur 2024-01-01 \
  --themes "securite_machines,environnement"
```

### Indexer un JSON dans Qdrant

```bash
python3 scripts/ingest.py --json data/raw/mon_document.json
```

---

## 🧠 API Endpoints

Tous les endpoints nécessitent un header `X-API-Key`.

| Méthode | Endpoint | Description |
| :--- | :--- | :--- |
| **POST** | `/ask` | Pose une question réglementaire |
| **POST** | `/ingest` | Ingère un document JSON |
| **GET** | `/pending` | Liste des tâches en attente de validation humaine |
| **POST** | `/approve` | Approuve une tâche |
| **POST** | `/reject` | Rejette une tâche |

**Exemple de requête** :

```bash
curl -X POST http://127.0.0.1:8000/ask \
  -H "X-API-Key: ta_clé" \
  -H "Content-Type: application/json" \
  -d '{"question": "Quelles sont les obligations de sécurité pour une machine neuve en 2026 ?"}'
```

---

## 🧪 Tests

```bash
pytest -q
```

Les tests couvrent :
- ✅ Sécurité (81 tests)
- ✅ Attribution des chapitres (B1)
- ✅ Audit chaîné (B2)
- ✅ Modèles Pydantic
- ✅ Temporalité (y compris les scénarios de versionnement B9)
- ✅ Agents (Retriever, Temporal, Explainer, Citation, Conflit)

---

## 📌 État actuel des corrections

| Bug | Statut | Détail |
| :--- | :--- | :--- |
| Attribution des chapitres (B1) | ✅ **Corrigé** | Les articles sont maintenant assignés au bon chapitre |
| Audit chaîné (B2) | ✅ **Corrigé** | Vérification de la liaison SHA‑256 entre enregistrements |
| Risque de famine temporelle (B3) | ✅ **Corrigé** | Répartition équilibrée (`top_k/2`) entre dispositions transitoires et permanentes |
| Clé API au démarrage (B4) | ✅ **Corrigé** | Le serveur refuse de démarrer si la clé API est absente |
| Validation des dates (B5) | ✅ **Corrigé** | L’agent temporel valide la date de contexte fournie |
| Timeout MLX (B6) | ✅ **Corrigé** | Les appels d’inférence ont un timeout borné |
| Watcher avec retry (B7) | ✅ **Corrigé** | Reprise avec backoff sur échec réseau ou 5xx |
| Configuration locale (B8) | ✅ **Corrigé** | Tous les services pointent vers `127.0.0.1` |
| Tests temporels (B9) | ✅ **Corrigé** | Ajout de tests pour les scénarios de versionnement temporel |

---

## 🗂️ Structure du projet

regulatory-agent-V2/
├── .env.example          # Variables d’environnement
├── .gitignore
├── README.md
├── requirements.txt
├── config.py             # Configuration centralisée (Pydantic-settings)
├── main.py               # Point d’entrée FastAPI
├── src/
│   ├── api.py            # Routes FastAPI
│   ├── orchestrator.py   # Orchestrateur des agents
│   ├── models.py         # Modèles Pydantic (DocumentReglementaire, etc.)
│   ├── mlx_utils.py      # Wrapper MLX (embedding + LLM)
│   ├── audit.py          # Audit trail SHA-256
│   ├── watcher.py        # Surveillance des sources réglementaires
│   └── agents/
│       ├── retriever.py      # Recherche vectorielle (Qdrant)
│       ├── temporal.py       # Filtrage temporel
│       ├── explainer.py      # Synthèse
│       ├── citation.py       # Génération de citations
│       └── conflit.py        # Détection de contradictions
├── scripts/
│   ├── ingest.py          # Pipeline JSON → Qdrant
│   ├── pdf_to_json.py     # Conversion PDF → JSON
│   └── setup_qdrant.py    # Initialisation de la collection Qdrant
├── data/
│   └── raw/               # Fichiers JSON et PDF (non versionnés)
├── tests/                 # Tests unitaires et d’intégration
└── web/                   # Interface Citadel (static + templates)
