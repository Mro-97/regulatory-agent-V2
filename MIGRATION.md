# MIGRATION — Regulatory Agent V2 vers architecture unique m4pro2

Journal de la migration. Chaque bloc au format § 4 de la mission (`docs/MISSION_migration.md`).

Machine de développement : MacBook Air (matiss@MacBook-Air-de-matiss.local).
Machine cible d'exécution : m4pro2 (mro@100.119.44.117, via Tailscale).
Anciennes machines (lecture seule, non touchées ce jour) : mini-1, m4pro1.

---

## § 0 — État des lieux de départ                                 [TERMINÉ]

Fait
  - `docs/CONTEXTE_PROJET.md` lu en entier (28085 octets, 1133 lignes) —
    fait autorité (commande : `wc -c docs/CONTEXTE_PROJET.md`)
  - 20 compétences installées sous `.claude/skills/` (commande : `ls .claude/skills/`)
  - Accès SSH lecture seule vérifié sur `mini-1`, `m4pro1`, `m4pro2`
    (commande : `ssh <host> 'echo OK && hostname'`)
  - `~/Downloads/regulatory-agent-skills/` (versions périmées) : absent
    (commande : `ls -la ~/Downloads/regulatory-agent-skills` → No such file)

Non vérifié
  - État interne de mini-1 et m4pro1 au-delà du hostname (hors périmètre §0)

Bloqué
  - Néant

Conséquence pour la suite
  - Blocs 4/5/6/8 disposent des références (§3.1 ports, §3.3 config,
    §17 orchestration, §25 tests, §29 état actuel)

---

## Bloc 1 — Figer la référence                                    [PARTIEL]

Fait
  - Arbre de travail nettoyé en 3 commits, un par sujet :
    - `589de0e` docs (CLAUDE.md, docs/, .claude/skills/)
    - `dfd7490` .gitignore : `data/raw/*.pdf` et `data/raw/*.hash` ignorés
    - `dcbcb7a` 6 JSON à `id` divergent des `*_FULL.json` — divergence
      documentée dans le message ; dédoublonnage `id` reste à traiter
      **avant réindexation Qdrant** (Bloc 6)
  - Tag annoté posé : `v1-architecture-3-machines` sur `dcbcb7a`
    (commande : `git tag -a v1-architecture-3-machines -m "..." HEAD`)
  - Branche créée et checkoutée : `arch/machine-unique`
    (commande : `git checkout -b arch/machine-unique`)
  - Tests référence : **142 passed, 0 failed en 1.14s**
    (commande : `venv/bin/python -m pytest -q --no-header --tb=no`)

Non vérifié
  - Tag `v1-architecture-3-machines` non poussé sur `origin` :
    remote GitHub porte encore un token en clair (utilisateur en cours
    de bascule SSH)

Bloqué
  - Push vers `origin` : suspendu tant que le token dans
    `.git/config` local n'est pas remplacé — action utilisateur.

Conséquence pour la suite
  - Une fois le remote propre : `git push --tags && git push -u origin
    arch/machine-unique` en une passe.

---

## Sécurisation du non-commité m4pro2                              [TERMINÉ]

Priorité absolue, préalable à toute autre action sur m4pro2.

Fait
  - 4 fichiers non suivis copiés depuis
    `mro@m4pro2:/Users/mro/regulatory-agent/` vers
    `~/regulatory-agent-backups/2026-08-26-m4pro2-uncommitted/` (sur l'Air)
    (commande : `scp -q m4pro2:<path> $DEST/`)

    | Fichier                          | Taille (o) | sha256 (préfixe) |
    |----------------------------------|-----------:|:-----------------|
    | dump.rdb                         |      5502  | 5b29aba26f6e2c24 |
    | .env.bak                         |       661  | a30f6367b2faafc1 |
    | CNIL_GUIDE_SECU_2023B.json       |     91844  | c22d556c4575e967 |
    | CNIL_GUIDE_SECU_2023_PDF.json    |     91895  | 50e2d33c51d1d8f8 |

  - Intégrité vérifiée : sha256 identiques source (m4pro2) et destination (Air)
    (commande : `shasum -a 256 <fichier>` de chaque côté, LC_ALL=C sur m4pro2)
  - `.env.bak` : `chmod 600` sur la copie ; contenu jamais lu.

Non vérifié
  - Contenu détaillé de `dump.rdb` (binaire Redis) et `.env.bak` (secrets)
    — non ouverts par principe.

Bloqué
  - Néant.

Conséquence pour la suite
  - Bloc 3 (récupération) pourra référencer ces 4 fichiers déjà en sécurité.
  - `dump.rdb` = files Redis d'un travail précédent : à examiner au Bloc 4
    (restauration des files `pending_*`).

---

## Sync m4pro2 (push de la branche par SSH)                       [TERMINÉ]

Fait
  - Push machine-à-machine réussi :
    `* [new branch] arch/machine-unique -> arch/machine-unique`
    (commande : `git push
    ssh://mro@m4pro2/Users/mro/regulatory-agent arch/machine-unique`)
  - AVANT : m4pro2 sur `main` @ `195f69a8`, 4 non-suivis
  - APRÈS : m4pro2 sur `main` @ `195f69a8` (INCHANGÉ),
    branche `arch/machine-unique` présente (non checkoutée), 4 non-suivis
    inchangés
    (commande : `ssh m4pro2 "git branch --show-current && git rev-parse HEAD
    && git branch --list arch/machine-unique && git status --short"`)

Non vérifié
  - Néant.

Bloqué
  - Basculement sur `arch/machine-unique` côté m4pro2 : suspendu jusqu'à
    feu vert explicite utilisateur (comme convenu).

Conséquence pour la suite
  - m4pro2 dispose de la branche cible ; checkout et pull des futurs
    correctifs devient une opération SSH ordinaire.

---

## Correctifs bugs — état                                         [SANS OBJET AUJOURD'HUI]

Vérifications objectives menées :
  - Tests : 142 passed / 0 failed
    (commande : `venv/bin/python -m pytest -q`)
  - Issues GitHub ouvertes : 0
    (commande : `gh issue list --repo Mro-97/regulatory-agent-V2 --state open`)
  - PRs ouvertes : 0
    (commande : `gh pr list --repo Mro-97/regulatory-agent-V2 --state open`)
  - `FIXME|XXX|HACK` dans `src/` : 0
    (commande : `grep -rniE "FIXME|XXX|HACK" src/ --include="*.py"`)
  - `TODO` dans `src/` : 1 (docstring de `src/orchestrator.py:26`,
    note de conception, non actionable)
    (commande : `grep -rnE "TODO" src/ --include="*.py"`)

Bloqué
  - Utilisateur a demandé "corriger tous les bugs qui restent" mais n'a
    nommé aucun bug spécifique. Aucun bug objectivement détectable.
    → Attente d'une liste explicite avant tout correctif ;
    interdit de deviner (consigne utilisateur précédente).

---

## Audit sécurité — C2 non résolu côté code                       [BLOQUÉ hors code]

Symptôme
  - Redis lie `*:6379` sur m4pro2 sans mot de passe. Confirmé
    (commande : `lsof -nP -iTCP:6379 -sTCP:LISTEN`).
  - Le fichier `redis.conf` n'est pas versionné (config OS locale).
  - `cfg.redis_password` est vide dans `.env`.

Impact
  - Tout client atteignant m4pro2 sur Tailscale/LAN peut se connecter à
    Redis, lire/modifier les files `pending_alerts`, `pending_links`,
    `pending_responses`, `pending_weights`. Contournement complet de la
    validation humaine.

Correction requise (action utilisateur, exige sudo ou édition redis.conf)
  1. Éditer `~/redis-stable/redis.conf` (ou l'équivalent) sur m4pro2 :
     - `bind 127.0.0.1 ::1`
     - `requirepass <MOT-DE-PASSE-FORT>`
  2. Relancer Redis (`redis-cli shutdown` puis redémarrage) après avoir
     copié le mot de passe dans `.env` sous `REDIS_PASSWORD=…`.
  3. Vérifier : `lsof -nP -iTCP:6379 -sTCP:LISTEN` ne montre plus `*:6379`.

## Reste à faire (état 2026-08-26)

- Bloc 2 — INVENTAIRE.md des 3 machines (dont vérif token `ghp_` dans
  `.git/config` sur mini-1/m4pro1/m4pro2 et `git log -p -S 'ghp_' --all`).
- Bloc 3 — Récupération vers destination à préciser (pg_dump, data/indexed,
  data/raw, data/pending, .env, modèles MLX).
- Bloc 4 — Services sur m4pro2 (constat + commandes à exécuter par
  l'utilisateur pour ce qui exige sudo).
- Bloc 5 — Modif `config.py` §3.3, purge orchestration distribuée §17,
  purge tests inter-machines §25. Sur l'Air, un commit par sujet.
- Bloc 6 — Démarrage services + réindexation Qdrant depuis JSON canonique
  (nom de collection incluant modèle+révision). **Avant** : trancher les
  6 doublons d'`id` du commit `dcbcb7a`.
- Bloc 7 — Sauvegarde initiale hors machine + runbook `docs/runbook.md`.
- Bloc 8 — Clôture : § 29 du contexte, date d'effacement anciennes machines.
