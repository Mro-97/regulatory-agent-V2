# Sauvegarde et restauration — Regulatory Agent V2

Procédure d'exploitation pour Qdrant (corpus vectoriel) et Redis (files de
validation et compteurs). Elle répond au point **O-4** de la revue : avant ce
document, la seule sauvegarde existante était celle créée incidemment par
`scripts/dedup_qdrant.py` avant une purge — donc ni planifiée, ni documentée,
et jamais pour Redis.

## Ce qu'il faut sauvegarder, et pourquoi

| Magasin | Contenu | Conséquence d'une perte |
|---|---|---|
| **Qdrant** | `regulatory_chunks` : le corpus vectorisé (21 467 points, 1024 dim) | Le RAG ne fonctionne plus. Les documents bruts sont dans `corpus/`, mais **réindexer prend des heures** (embedding de tout le corpus). |
| **Redis** | Files HITL (`pending_alerts`, `pending_responses`) et compteurs de rate-limit | Les tâches de validation en attente sont perdues. Les compteurs, eux, ne valent rien. |
| **PostgreSQL** | `audit_trail` : le journal d'audit chaîné | Repli automatique sur `data/audit.jsonl`. Perte limitée à la vérification d'intégrité SQL. |

## Commandes

Toutes s'exécutent **sur m4pro2**, depuis `~/regulatory-agent`.

### État des sauvegardes

```bash
venv/bin/python scripts/sauvegarde.py --etat
```

Affiche le nombre de points Qdrant, le nombre de snapshots côté serveur, le
volume du dossier `snapshots/` et la liste des dumps Redis.

### Sauvegarder

```bash
venv/bin/python scripts/sauvegarde.py --sauvegarder
```

Deux opérations, dans cet ordre :

1. **Qdrant** : l'API de snapshot produit une archive cohérente sans arrêter le
   service. Elle est écrite côté serveur, puis listée par le script.
   *Ne jamais copier `qdrant_storage/` à chaud* : l'image serait incohérente.
2. **Redis** : `SAVE` (synchrone), puis copie du RDB horodaté dans
   `snapshots/redis-<horodatage>.rdb`. Le chemin du RDB est **lu depuis Redis**
   (`CONFIG GET dir` / `dbfilename`) et non codé en dur.

### Purger les anciens snapshots

```bash
venv/bin/python scripts/sauvegarde.py --purger --garder 2
```

Destructif, donc **explicite** : rien n'est effacé sans `--purger`. Ne conserve
que les N snapshots les plus récents.

> **Pourquoi c'est nécessaire** : au 2026-09-17, `snapshots/` pesait **3,18 Go**
> pour 6 snapshots, dont des archives de l'ancien index torch devenu
> inutilisable. Sans ménage, le disque se remplit silencieusement.

### Restaurer

```bash
venv/bin/python scripts/sauvegarde.py --restaurer-chemin snapshots/<fichier>.snapshot
```

**Opération destructive : la collection est remplacée.** Utilise
`recover_snapshot` (le nom réel de l'API `qdrant_client`), avec `wait=True` pour
que la restauration soit terminée au retour.

Après restauration, toujours vérifier :

```bash
venv/bin/python scripts/sauvegarde.py --etat
curl -s http://127.0.0.1:8002/health
```

Le nombre de points doit correspondre à celui du snapshot restauré, et
l'`EMBEDDING_DIMENSION` doit égaler `QDRANT_VECTEUR_TAILLE` — sinon le boot
refuse de démarrer (garde-fou existant dans `main.py`).

## Rythme recommandé

| Fréquence | Action |
|---|---|
| Avant toute opération destructive (purge, réindexation, migration) | `--sauvegarder` |
| Après un réindexation complète | `--sauvegarder`, puis `--purger --garder 2` |
| Mensuel | `--etat` pour surveiller le volume |

## Points de vigilance

- **Un snapshot vit côté serveur Qdrant**, pas dans `snapshots/`. Le dossier
  local ne contient que les dumps Redis. Les snapshots Qdrant se consultent via
  `--etat` et se suppriment via `--purger`.
- **Le dump Redis est minuscule** (~165 Ko pour 4 clés) : sa copie est
  instantanée, il n'y a aucune raison de s'en priver avant un arrêt.
- **Redis a une persistance RDB configurée** (`save 3600 1 300 100 60 10000`),
  mais elle ne protège pas d'un arrêt juste après une écriture. Le `SAVE`
  explicite du script, lui, garantit que le fichier est à jour.
- **Aucune sauvegarde hors machine** : tout est sur m4pro2. Une panne disque
  emporterait corpus et sauvegardes. Une copie externe (autre machine du
  tailnet, disque USB) reste à mettre en place — c'est le principal manque
  résiduel de cette procédure.
