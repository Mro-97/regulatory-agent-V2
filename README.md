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
- **Identifier** la version applicable d'un texte à une date donnée.
- **Détecter** les modifications réglementaires (Watcher).
- **Valider** les décisions critiques avec un humain dans la boucle.
- **Auditer** l'intégralité des requêtes (traçabilité SHA-256).

**Architecture** : Le système est conçu pour fonctionner sur une **seule machine** (Mac Mini `m4pro2` ou MacBook M4 Pro). L'ancienne architecture distribuée (3 Mac Mini) est abandonnée.

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
| **Cache / Files d'attente** | Redis |
| **Base de données (audit)** | PostgreSQL (en cours) |
| **Embeddings** | bge-m3 (dim 1024) |
| **Modèles LLM** | Llama 3.2 3B, Mistral 7B, Qwen 2.5 7B, DeepSeek-R1 14B |

---

## 🔐 Sécurité

- **Authentification** : Clé API (`X-API-Key`) requise sur tous les endpoints métier.
- **Rate limiting** : Limitation des requêtes par IP sur `/ask` et `/ingest`.
- **Sanitisation** : Nettoyage des prompts pour éviter les injections.
- **CORS** : Restreint à l'origine de l'interface web.
- **Audit trail** : Chaînage SHA-256 pour chaque requête.
- **Swagger désactivé** par défaut en production.

---

## 🚀 Installation

```bash
# 1. Cloner le dépôt
git clone https://github.com/Mro-97/regulatory-agent-V2.git
cd regulatory-agent-V2

# 2. Créer l'environnement virtuel (avec uv)
uv venv --python 3.13
source venv/bin/activate

# 3. Installer les dépendances
uv pip install -r requirements.txt
