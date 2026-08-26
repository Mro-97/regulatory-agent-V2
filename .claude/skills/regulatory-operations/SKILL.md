---
name: regulatory-operations
description: Utiliser pour installer, démarrer, superviser, sauvegarder et restaurer Regulatory Agent V2 sur m4pro2 — services locaux, budget mémoire, sauvegarde hors machine, restauration testée, procédures d'incident et de mise à jour.
author: Regulatory Agent Team
version: 1.0.0
tags: [operations, runbook, backup, restore, monitoring, memory, incident]
---

# regulatory-operations

## Objectif de la compétence

Faire tourner Regulatory Agent V2 sur `m4pro2` et **y survivre**.

Le passage à une machine unique a supprimé une classe de problèmes — la communication réseau entre machines — et en a créé une autre, plus grave : il n'y a plus de redondance d'aucune sorte. Cette compétence couvre l'installation, le démarrage, la supervision, la sauvegarde, la restauration, les incidents et les mises à jour.

Elle ne couvre ni le durcissement de la surface HTTP (voir `regulatory-api-security`), ni les migrations de schéma et les réindexations (voir `regulatory-data-migrations`), ni la conformité (voir `regulatory-compliance-eu`).

## Principe fondamental

> **La machine unique est un point de défaillance unique. La sauvegarde hors machine n'est pas une bonne pratique : c'est la seule chose qui sépare un incident d'une perte définitive.**

Un disque qui lâche sur `m4pro2` emporte le corpus, l'index, l'historique des versions réglementaires, la chaîne d'audit et les tâches de validation en cours. Une sauvegarde qui reste sur la machine ne sauvegarde rien.

Corollaire, exigé par NIS2 (voir `regulatory-compliance-eu`) : **une sauvegarde jamais restaurée n'est pas une sauvegarde, c'est une hypothèse.**

---

## 1. Inventaire d'exploitation

| Service | Écoute | Démarrage | Nature de l'état |
|:---|:---|:---|:---|
| PostgreSQL | `127.0.0.1:5432` | Service système au démarrage | **Critique, non reconstructible** — audit, historique |
| Qdrant | `127.0.0.1:6333` | Service système au démarrage | Dérivé — reconstructible depuis le JSON canonique |
| Redis | `127.0.0.1:6379` | Service système au démarrage | Mixte — cache jetable, **files de validation métier** |
| API FastAPI | `127.0.0.1:8000` | Après les trois précédents | Sans état |
| Watcher | — | Après l'API | Ordonnancement, dernier passage par source |

**Ordre de démarrage** : PostgreSQL → Qdrant → Redis → API → Watcher. L'API doit échouer au démarrage, bruyamment, si l'une des trois dépendances est absente. Un démarrage « dégradé » silencieux produit des réponses sans audit.

**Vérification de santé** : un point d'entrée qui contrôle réellement les trois dépendances et retourne un état par service — pas un `200 OK` inconditionnel. Il indique aussi le modèle actuellement chargé et la mémoire disponible.

Le fichier `data/raw/`, `data/indexed/` et `data/pending/` du dépôt ne sont pas des répertoires temporaires : `data/indexed/` contient le **JSON canonique**, qui est la source de vérité du projet.

---

## 2. Budget mémoire

24 Go de mémoire unifiée, partagés. C'est la contrainte structurante de l'exploitation.

| Poste | Ordre de grandeur | Remarque |
|:---|:---|:---|
| Système et divers | 3 – 4 Go | Incompressible |
| PostgreSQL | 0,5 – 1 Go | Selon la taille de l'audit |
| Redis | 0,5 – 2 Go | À plafonner par `maxmemory` |
| Qdrant | 1 – 4 Go | Croît avec le nombre de vecteurs |
| Modèle 3B (4-bit) | ~2 Go | Routage |
| Modèle 7B (4-bit) | ~4 – 5 Go | Retriever, Temporal, Explainer, Citation |
| Modèle 14B (4-bit) | **8 – 10 Go** | Conflict, occasionnel |
| Embeddings `bge-m3` | 1 – 2 Go | Concurrence l'inférence |

> Ces valeurs sont des ordres de grandeur à **mesurer** sur la machine avant d'en dépendre. Le principe qui en découle, lui, ne dépend pas de la mesure : avec un 14B chargé et les services actifs, la marge est mince. **Un seul modèle résident à la fois**, et jamais d'indexation en masse pendant le service de requêtes.

### Seuils d'exploitation

| Mémoire libre | État | Conduite |
|:---|:---|:---|
| > 6 Go | Nominal | — |
| 3 – 6 Go | Vigilance | Refuser le chargement du 14B, alerter |
| 1,5 – 3 Go | Dégradé | Décharger le modèle courant, refuser les nouvelles requêtes coûteuses avec un message explicite |
| < 1,5 Go | Critique | Refus général, alerte, journal `CRITICAL` |

**Refuser proprement avant la saturation vaut mieux que planter pendant.** Un OOM emporte le processus au milieu d'une écriture d'audit.

---

## 3. Supervision

Ce qu'il faut mesurer, et pourquoi.

| Indicateur | Signal |
|:---|:---|
| Mémoire libre, mémoire résidente par service | Marge réelle avant saturation |
| Modèle actuellement chargé, nombre de bascules par heure | Un routage instable effondre la latence |
| Latence p50 / p95 par parcours | Dérive de performance |
| Profondeur des files Redis | Engorgement de la validation humaine |
| **Âge de la plus ancienne tâche en attente** | **Approche du seuil d'escalade de 72 heures** |
| Dernier passage réussi du Watcher, par source | Une source silencieuse depuis 24 h est une panne, pas un calme |
| Nombre de points Qdrant, taille sur disque | Croissance de l'index |
| Taille de la table d'audit, **dernier contrôle d'intégrité de la chaîne** | Détection d'altération |
| Espace disque libre | Sauvegardes et index en dépendent |
| Âge et statut de la dernière sauvegarde réussie | L'indicateur le plus important de cette liste |

L'âge de la plus ancienne tâche en attente mérite une alerte à 48 heures : l'escalade automatique à 72 heures est un filet de sécurité, pas un mode de fonctionnement normal.

---

## 4. Sauvegarde

### Quoi sauvegarder

| Donnée | Priorité | Reconstructible ? |
|:---|:---|:---|
| **PostgreSQL** — audit, métadonnées, historique | **Maximale** | **Non.** Perte définitive |
| **JSON canonique** (`data/indexed/`) | **Maximale** | Non — seule source de vérité du corpus |
| Documents source originaux (`data/raw/`) | Haute | Re-téléchargeables, mais la version précise peut avoir disparu de la source |
| **Files Redis de validation** | Haute | Non — travail humain en cours |
| Configuration, `.env`, prompts versionnés | Haute | Non si non versionnés |
| Collections Qdrant | Basse | **Oui**, depuis le JSON canonique — mais la reconstruction coûte du temps machine |
| Cache Redis | Nulle | Jetable |

Le tableau se lit dans un sens précis : **Qdrant est le seul gros volume qu'on peut se permettre de perdre.** Sauvegarder l'index à chaque cycle au détriment de la fréquence de sauvegarde de PostgreSQL est une erreur d'arbitrage courante.

### Comment

- **Destination hors machine**, obligatoirement. Un second disque branché en permanence sur `m4pro2` protège d'une panne de disque, pas d'un vol, d'un dégât des eaux ni d'un chiffrement malveillant. Prévoir au moins une copie déconnectée ou distante.
- **Chiffrement au repos** des sauvegardes : elles contiennent l'audit, donc potentiellement des questions d'utilisateurs (voir l'article 32 traité par `regulatory-compliance-eu`).
- **Cohérence** : un `pg_dump` et une copie de fichiers pris à des instants différents peuvent se contredire. Prendre le dump PostgreSQL et l'instantané du JSON canonique dans la même fenêtre, Watcher et ingestion à l'arrêt.
- **Rétention** : conserver plusieurs générations. Une sauvegarde unique écrasée chaque nuit propage une corruption en une nuit.
- Les sauvegardes ne contiennent aucun secret en clair ; les secrets se gèrent séparément.

### Restauration

**La procédure de restauration se teste, à intervalle défini, sur un jeu complet.** Le test consiste à repartir de zéro et à obtenir un système qui répond correctement — pas à vérifier que les fichiers existent.

Ordre de restauration :

1. Services de base installés et démarrés, vides.
2. Restauration PostgreSQL. **Vérifier l'intégrité de la chaîne SHA-256 avant toute autre chose** : une chaîne rompue après restauration est un incident majeur qui doit être connu immédiatement.
3. Restauration du JSON canonique et des sources originales.
4. Restauration des files Redis de validation.
5. **Reconstruction de Qdrant depuis le JSON canonique** — l'index n'a pas besoin d'être sauvegardé, mais la reconstruction doit être une procédure éprouvée et chronométrée.
6. Campagne du jeu de référence (voir `regulatory-rag-evaluation`) avant remise en service. C'est le seul contrôle qui prouve que le système restauré répond juste.

Le temps mesuré à l'étape 5 est le vrai délai de reprise. Il se connaît à l'avance, pas le jour de l'incident.

---

## 5. Procédures d'incident

| Incident | Diagnostic | Conduite |
|:---|:---|:---|
| **Saturation mémoire / OOM** | Journal `CRITICAL`, modèle chargé au moment de l'échec | Décharger le modèle, appliquer les seuils du § 2, vérifier qu'aucune écriture d'audit n'est restée incomplète |
| **Bascules de modèle incessantes** | Compteur de chargements par heure | Examiner le routage : l'orchestrateur invoque probablement Conflict au-delà de la cible de ~20 % |
| **Source injoignable** | Dernier passage du Watcher | Panne côté source ou blocage réseau. Alerte d'exploitation, **jamais un silence**. Ne pas créer d'alerte réglementaire fictive |
| **Chaîne d'audit rompue** | Contrôle d'intégrité en échec | **Incident majeur.** Ne rien réécrire, ne rien « réparer ». Isoler, dater la rupture, remonter à l'administrateur. Un audit réparé n'est plus un audit |
| **Index Qdrant incohérent** | Écart entre points indexés et JSON canonique | Réindexer depuis le JSON, jamais l'inverse (voir `regulatory-data-migrations`) |
| **File de validation engorgée** | Profondeur et âge | Problème organisationnel avant d'être technique. Vérifier que l'escalade à 72 h fonctionne réellement |
| **Réponses sans citation** | Taux d'affirmations non citées | Arrêter le service. C'est un défaut réglementaire, pas une dégradation |
| **Disque plein** | Espace libre | Purger le cache, jamais l'audit ni le JSON canonique |

---

## 6. Mise à jour

1. Sauvegarde complète et vérifiée **avant** toute mise à jour.
2. Campagne du jeu de référence : établir l'état avant.
3. Appliquer la mise à jour. Une seule chose à la fois — code, ou modèle, ou service de données. Jamais les trois.
4. Migrations éventuelles selon `regulatory-data-migrations`.
5. Campagne du jeu de référence : comparer.
6. Contrôle d'intégrité de la chaîne d'audit.
7. Décision de conservation ou de retour arrière, sur les résultats du § 5 — pas sur l'impression que « ça a l'air de marcher ».

Le retour arrière doit être connu et écrit **avant** l'étape 3.

---

## 7. Checklists

### Mise en service

- [ ] Les quatre services écoutent sur `127.0.0.1`, aucun sur `0.0.0.0`.
- [ ] Démarrage automatique configuré, dans l'ordre du § 1.
- [ ] L'API échoue bruyamment si une dépendance manque.
- [ ] Contrôle de santé effectif sur les trois dépendances.
- [ ] `maxmemory` fixé sur Redis, persistance activée pour les files métier.
- [ ] Utilisateur PostgreSQL applicatif dédié, sans privilèges superflus.
- [ ] Chiffrement du disque activé.
- [ ] Sauvegarde planifiée, destination hors machine, chiffrée.
- [ ] **Restauration complète testée au moins une fois**, délai de reprise mesuré.
- [ ] Supervision en place sur les indicateurs du § 3.
- [ ] Journal d'exploitation ouvert.

### Quotidien

- [ ] Dernière sauvegarde réussie et datée.
- [ ] Mémoire libre au-dessus du seuil de vigilance.
- [ ] Chaque source vue par le Watcher dans les dernières 24 h.
- [ ] Aucune tâche de validation au-delà de 48 h.
- [ ] Aucune entrée `CRITICAL` au journal.

### Mensuel

- [ ] Restauration testée sur un environnement séparé, délai de reprise relevé.
- [ ] Contrôle d'intégrité de la chaîne d'audit.
- [ ] Campagne du jeu de référence, comparaison à la précédente.
- [ ] Revue de la croissance disque et de l'index.
- [ ] Revue des dérogations de norme et des dépendances vulnérables.

---

## Exemples de prompts

> « Rédige le runbook de mise en service de `m4pro2` : installation des quatre services, ordre et supervision du démarrage, contrôle de santé réel, et checklist du § 7 renseignée à partir de l'état actuel du dépôt. »

> « Conçois la stratégie de sauvegarde : quoi, à quelle fréquence, vers où, avec quelle rétention et quel chiffrement, en respectant l'arbitrage du § 4 — et écris la procédure de restauration complète avec le contrôle d'intégrité de la chaîne d'audit à l'étape 2. »

> « Implémente la supervision mémoire des seuils du § 2 : mesure, seuils, refus propre avant saturation, alerte, et refus de chargement de DeepSeek-R1 en état de vigilance. »

> « Le contrôle d'intégrité de la chaîne d'audit échoue à partir du 12 mars. Établis la procédure d'investigation : périmètre de la rupture, ce qui reste exploitable, ce qui doit être déclaré, et ce qu'il ne faut surtout pas faire. »

> « Chronomètre et documente la reconstruction complète de l'index Qdrant depuis le JSON canonique : c'est notre délai de reprise réel, il doit être connu avant l'incident. »

## Références

- PostgreSQL — sauvegarde et restauration — <https://www.postgresql.org/docs/current/backup.html>
- Qdrant — instantanés et récupération — <https://qdrant.tech/documentation/concepts/snapshots/>
- Redis — persistance — <https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/>
- ANSSI — guide d'hygiène informatique — <https://cyber.gouv.fr/publications/guide-dhygiene-informatique>
- Google SRE Book — <https://sre.google/books/>

---

## Contexte projet

Cette compétence s'applique à **Regulatory Agent V2**, un système local de veille réglementaire et d'assistance IA pour l'industrie. L'ensemble du système tourne sur une **machine unique** : `m4pro2` — Mac Mini M4 Pro, 24 Go de mémoire unifiée. L'architecture distribuée sur trois machines est abandonnée.

Tous les services écoutent exclusivement sur `127.0.0.1` :

- **FastAPI** `:8000` — point d'entrée unique ; sert aussi l'interface web (chat + panneaux de validation).
- **Qdrant** `:6333` — base vectorielle.
- **Redis** `:6379` — cache et files de validation.
- **PostgreSQL** `:5432` — audit, métadonnées, historique.
- **Orchestrateur, agents, Watcher et audit** — modules locaux sur la même machine.

Les modèles sont chargés à la demande, **un seul à la fois** : Llama 3.2 3B (routage), Mistral 7B (Retriever / Citation), Qwen 2.5 7B (Temporel / Explainer), DeepSeek-R1 14B (Conflit, ~20 % des requêtes, 8-10 Go en 4-bit — à ne charger que si c'est réellement nécessaire, puis à décharger).

Le projet impose l'inférence locale avec MLX et s'organise autour des documents réglementaires, de l'historique des versions, des citations exactes, de la validation humaine et de l'auditabilité.

## Contraintes non négociables

1. Ne jamais introduire d'API d'inférence IA externe.
2. Ne pas remplacer silencieusement MLX par une autre pile d'inférence.
3. Préserver la discipline mémoire de la machine unique : un seul modèle résident à la fois, chargement à la demande, déchargement explicite. Ne jamais supposer qu'une seconde machine est disponible, ni lier un service à `0.0.0.0`.
4. Préserver le modèle JSON canonique et la sémantique temporelle.
5. Traiter les réponses réglementaires comme des sorties adossées à des preuves, pas comme des conclusions juridiques.
6. Privilégier des composants déterministes et testables autour des appels LLM.
7. Ne pas inventer de faits, versions, dates, articles ou citations réglementaires.
8. En cas d'ambiguïté, signaler l'ambiguïté plutôt que de modifier silencieusement un invariant du projet.

## Règles de travail

- Inspecter le dépôt existant avant de proposer des changements structurels.
- Réutiliser les abstractions compatibles du projet.
- Garder les changements modulaires et réversibles.
- Expliquer les conséquences inter-composants avant un changement d'architecture.
- Ajouter ou mettre à jour les tests pour tout comportement pouvant affecter la correction réglementaire.
- Garder les secrets et identifiants hors du code source et des journaux.
