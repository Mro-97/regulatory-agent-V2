# INVENTAIRE — Bloc 2 mission migration

Date : 2026-08-28
Machines auditées : mini-1, m4pro1, m4pro2 (via SSH lecture seule).

## Vue synthétique

|                       | mini-1                       | m4pro1                       | m4pro2                       |
|-----------------------|------------------------------|------------------------------|------------------------------|
| Hostname              | mini-1                       | m4pro1                       | m4pro2                       |
| macOS                 | 26.5.2                       | 26.2                         | 15.1                         |
| Repo                  | `/Users/mro/regulatory-agent`| `/Users/mro/regulatory-agent`| `/Users/mro/regulatory-agent`|
| `data/raw/` (JSON)    | 19 M, 66 fichiers            | 18 M, 63 fichiers            | 16 M, 52 fichiers            |
| `data/indexed/`       | vide                         | vide                         | vide                         |
| `data/pending/`       | vide                         | vide                         | vide                         |
| `models/` (MLX)       | 1.1 G                        | 1.1 G                        | 1.1 G                        |
| `~/.cache/huggingface`| 4.0 G                        | **21 G**                     | 16 G                         |
| PostgreSQL            | `psql` présent, aucune base  | non installé                 | 127.0.0.1:5432 (bases : à préciser) |
| Redis                 | `redis-cli` absent — service inactif | `redis-cli` absent — inactif | 127.0.0.1:6379 (actif, requirepass) |
| Qdrant                | 127.0.0.1:6333 protégé API-key inconnue | 127.0.0.1:6333 ouvert, coll. `deepseek_rag` | 127.0.0.1:6333 ouvert, coll. `regulatory_chunks` |
| `.env` (taille / champs) | 586 o / 22 champs         | 419 o / 20 champs            | 971 o / 24 champs            |
| Token `ghp_` dans `.git/config` | 0                  | 0                            | 0                            |

Toutes valeurs mesurées via SSH `LC_ALL=C bash -c '…'` — aucune valeur inventée.

## Qdrant : détail des collections

| Machine | Collection          | Points   | Statut                                  |
|---------|---------------------|---------:|-----------------------------------------|
| mini-1  | inconnues           |     ?    | Endpoint protégé, clé locale ne matche pas — accès en attente |
| m4pro1  | `deepseek_rag`      | 106 617  | Snapshot 1.13 GB créé, transfert en cours vers Air |
| m4pro2  | `regulatory_chunks` |  24 105  | dim 1024, distance Cosine, statut green, **collection cible** |

- La collection `deepseek_rag` de m4pro1 est un corpus indexé pour DeepSeek RAG (pipeline distinct), inutile en l'état pour Regulatory Agent V2 (bge-m3 vs DeepSeek). Backup à titre conservatoire.
- La collection Qdrant de mini-1 reste **inconnue** — la clé API dans `/Users/mro/regulatory-agent/.env` ne fait pas passer les headers `api-key:` ni `Authorization: Bearer`. Nécessite vérification manuelle.

## PostgreSQL

- **mini-1** : `psql` installé mais aucune base listée via `psql -l` (rôle par défaut absent ou service inactif).
- **m4pro1** : ni `psql` ni `postgres` dans PATH.
- **m4pro2** : PostgreSQL actif sur 127.0.0.1:5432 (audit trail Regulatory Agent V2).

→ **Aucun `pg_dump` à récupérer** depuis mini-1 ou m4pro1. Le seul PG en usage productif est celui de m4pro2 (déjà en place).

## Redis

- mini-1 et m4pro1 : Redis non installé (aucun `redis-cli` dans PATH, aucun listener sur :6379).
- m4pro2 : Redis actif (bind 127.0.0.1 + `[::1]`, `requirepass` en place).

→ Aucun dump Redis à sauvegarder depuis mini-1/m4pro1.

## Diff `data/raw/` — les 12 fichiers absents de m4pro2

Fichiers présents sur mini-1 et m4pro1 mais pas sur m4pro2 (correspondance à 100 %) :

- `CNIL_GUIDE_SECU_2023B.json`
- `CNIL_GUIDE_SECU_2023_PDF.json`
- `INRS_SECURITE_MACHINES.json`
- `corpus_energie.json`, `corpus_environnement.json`, `corpus_reglementaire_150docs.json`,
  `corpus_rgpd_nis2.json`, `corpus_sante_travail.json`, `corpus_securite_machines.json`
- `nis2_2022_2555.json`, `rgpd_complet.json`, `test_rgpd.json`

Ces 12 fichiers correspondent exactement aux **doublons de contenu / subsets / agrégats non ingérables** supprimés dans le commit `e0aba94` (session du 26/08). **Aucun fichier unique en danger** — le contenu utile est déjà présent sur m4pro2.

## Modèles MLX / cache Hugging Face

- `models/` (1.1 G identique sur les 3) : `bge-m3` local + tokenizers, redondant, non nécessaire à récupérer.
- `~/.cache/huggingface/` :
  - mini-1 4 G — surtout embeddings + petits modèles.
  - **m4pro1 21 G** — probablement DeepSeek-R1 Distill Qwen 14B (utilisé par `deepseek_rag`).
  - m4pro2 16 G — Mistral + Qwen + bge-m3.

Les modèles Hugging Face étant **retéléchargeables depuis HF sur demande**, aucun backup n'est nécessaire à ce stade.

## Sécurité — inventaire des secrets

- `ghp_` (token GitHub) : **absent** dans `.git/config` sur les 3 machines. Confirmé par `grep -c ghp_`.
- `.env` de mini-1 et m4pro1 : **récupérés** vers `~/regulatory-agent-backups/2026-08-28-migration/{mini-1,m4pro1}/.env`, chmod 600, sha256 vérifiés source ↔ destination.

## Commandes utilisées

Reproductibles pour audit :

```
ssh -o ConnectTimeout=5 <host> 'LC_ALL=C bash -c "…"'
```

Blocs de mesure lancés par machine : `hostname`, `sw_vers -productVersion`, `find ~ -maxdepth 3 -iname regulatory-agent*`, `lsof -nP -iTCP -sTCP:LISTEN`, `du -sh <dir>`, `find <dir> -type f | wc -l`, `psql -tA -c "SELECT …"`, `curl -s http://127.0.0.1:6333/collections`, `grep -c ghp_ .git/config`, `stat -f %z .env`.
