---
name: regulatory-data-migrations
description: Utiliser pour faire évoluer les schémas et les index de Regulatory Agent V2 sans casser l'historique réglementaire ni la chaîne d'audit — migrations PostgreSQL, réindexation Qdrant, évolution du JSON canonique, cohérence entre les dépôts de données.
author: Regulatory Agent Team
version: 1.0.0
tags: [migration, alembic, qdrant, reindexing, schema, data-integrity]
---

# regulatory-data-migrations

## Objectif de la compétence

Faire évoluer les structures de données **sans casser l'historique réglementaire ni la chaîne d'audit**.

Sur un système ordinaire, une migration ratée se rejoue. Ici, deux choses ne se rejouent jamais : l'**audit**, qui est une preuve, et l'**historique des versions réglementaires**, dont l'original a pu disparaître de la source. Cette compétence existe pour éviter de les perdre.

Elle ne couvre ni l'exploitation courante (voir `regulatory-operations`), ni la qualité des réponses (voir `regulatory-rag-evaluation`), ni le nommage des champs (voir `regulatory-code-standards`).

## Principe fondamental

> **Le JSON canonique est la seule source de vérité du corpus. L'audit est la seule donnée qui ne se reconstruit jamais.**

Tout le reste en découle.

| Dépôt | Nature | Reconstructible ? | Conséquence pour une migration |
|:---|:---|:---|:---|
| **JSON canonique** (`data/indexed/`) | Source de vérité du corpus | Non | Migration versionnée, originaux conservés |
| **PostgreSQL** | Audit, métadonnées, historique | **Non** | Additif uniquement, jamais de réécriture |
| **Qdrant** | Index dérivé | **Oui**, depuis le JSON | Se reconstruit ; ne jamais l'écrire comme source |
| **Redis** | Cache + files de validation | Cache oui, **files non** | Drainer ou versionner les clés |

La règle de dépendance qui en résulte : **une donnée dérivée ne remonte jamais vers sa source.** Si l'index Qdrant et le JSON canonique divergent, c'est l'index qu'on refait.

---

## 1. Migrations PostgreSQL

Outil : **Alembic**, migrations versionnées dans le dépôt, jamais de modification manuelle du schéma en exploitation.

### Méthode : expand / contract

Une évolution se fait en trois temps séparés, sur trois livraisons distinctes.

| Temps | Action | Le code |
|:---|:---|:---|
| **Expand** | Ajouter la nouvelle structure. Rien n'est supprimé. Colonne ajoutée en `NULL` ou avec défaut. | Écrit dans l'ancienne **et** la nouvelle |
| **Migrate** | Remplir la nouvelle structure. Script idempotent, par lots, reprenable. | Lit l'ancienne, écrit les deux |
| **Contract** | Supprimer l'ancienne structure. | Lit et écrit la nouvelle seule |

Interdits absolus dans une même version de migration : ajouter et supprimer ; renommer une colonne portant une donnée réglementaire ; ajouter une colonne `NOT NULL` sans défaut sur une table volumineuse.

### La chaîne d'audit

C'est le point le plus délicat du projet.

La chaîne SHA-256 couvre un **périmètre de champs défini**. Ce périmètre doit être documenté explicitement, quelque part de stable, parce que toute la logique de migration en dépend :

- **Ajouter une colonne hors périmètre** — un index, un champ d'exploitation : sans effet sur la chaîne. Autorisé.
- **Modifier le périmètre** — ajouter un champ à ce qui est haché : **rompt la chaîne pour tous les enregistrements existants**. La seule conduite acceptable est de clore l'ancienne chaîne, de la conserver intacte et vérifiable, et d'en démarrer une nouvelle à partir d'un enregistrement de transition qui référence le dernier hachage de l'ancienne. On ne recalcule jamais les hachages passés : un audit recalculé ne prouve plus rien.
- **Modifier une ligne d'audit existante** : interdit, quelle que soit la raison. Une donnée d'audit erronée se corrige par un enregistrement correctif chaîné, jamais par un `UPDATE`.
- **Supprimer des lignes d'audit** : uniquement dans le cadre d'une politique de rétention écrite, par purge datée des plus anciennes en bloc, en conservant la trace de la purge. Jamais de suppression sélective.

Une migration qui touche à l'audit est une décision de projet, pas une tâche technique. Elle se documente comme une décision d'architecture.

### Checklist d'une migration PostgreSQL

- [ ] Sauvegarde vérifiée immédiatement avant.
- [ ] Migration réversible, ou irréversibilité explicitement assumée et écrite.
- [ ] Testée sur une copie restaurée de la base réelle, pas sur une base vide.
- [ ] Durée mesurée sur volume réel ; verrous longs identifiés.
- [ ] Idempotente et reprenable après interruption.
- [ ] Aucune réécriture d'enregistrement d'audit.
- [ ] Intégrité de la chaîne vérifiée **avant et après**.
- [ ] Retour arrière écrit avant l'exécution.

---

## 2. Réindexation Qdrant

### Quand elle est obligatoire

| Changement | Réindexation | Pourquoi |
|:---|:---|:---|
| Modèle d'embedding, ou **sa révision** | **Complète** | Les vecteurs de deux modèles ne sont pas comparables |
| Dimension des vecteurs | **Complète** | Collection incompatible |
| Métrique de distance | **Complète** | Scores non comparables |
| Stratégie de chunking | **Complète** | Les unités indexées changent |
| Champ de payload utilisé par un filtre | Partielle ou complète | Un filtre temporel sur un champ absent ne filtre rien |
| Ajout d'un champ de payload non filtrant | Mise à jour ciblée | — |
| Nouveaux documents | Incrémentale | Fonctionnement normal |

La première ligne mérite d'être comprise plutôt que retenue : **deux modèles d'embedding produisent des espaces vectoriels sans relation entre eux.** Mélanger dans une collection des vecteurs issus de deux modèles ne provoque aucune erreur — les recherches continuent de retourner des résultats, silencieusement faux. C'est le mode de défaillance le plus dangereux de tout le pipeline, parce qu'il est invisible.

D'où la règle : **le modèle d'embedding et sa révision sont épinglés, et enregistrés comme métadonnée de la collection.** Un démarrage doit refuser de servir si le modèle configuré ne correspond pas à celui qui a produit l'index.

### Procédure — bascule par alias

1. **Campagne de référence avant** (voir `regulatory-rag-evaluation`) : sans elle, aucun moyen de savoir si la réindexation a amélioré ou dégradé.
2. Créer une **nouvelle collection** portant le modèle et sa révision dans son nom. Ne jamais réindexer en place.
3. Indexer depuis le **JSON canonique**, jamais depuis l'ancienne collection.
4. Contrôles de complétude : nombre de points attendu contre obtenu, absence de document sans chunk, présence des champs de filtre sur tous les points, cohérence des intervalles de validité.
5. **Campagne de référence sur la nouvelle collection**, comparaison aux seuils.
6. Bascule de l'alias applicatif — instantanée et réversible.
7. **Conserver l'ancienne collection** au moins une période d'observation définie. C'est le retour arrière.
8. Suppression de l'ancienne collection, décidée, datée, journalisée.

### Contrainte propre à la machine unique

Une réindexation complète charge le modèle d'embedding, écrit massivement dans Qdrant et sature la mémoire. **Elle ne cohabite pas avec le service de requêtes sur 24 Go.** Elle se planifie dans une fenêtre de maintenance, Watcher à l'arrêt, avec un temps mesuré à l'avance (voir `regulatory-operations` § 4, étape 5 : c'est le même chiffre que le délai de reprise).

Le doublement temporaire de l'espace disque pendant la coexistence des deux collections se vérifie **avant** de commencer.

---

## 3. Évolution du modèle JSON canonique

- Le schéma porte un **numéro de version**, présent dans chaque document.
- La migration est un **script idempotent** qui lit une version et écrit la suivante, sans perte.
- **Les originaux sont conservés.** Une migration de schéma ne réécrit pas `data/raw/`.
- **L'historique des versions réglementaires ne se compacte jamais.** Supprimer une version de texte abrogée parce qu'elle « ne sert plus » détruit la capacité à répondre aux questions historiques, qui est la raison d'être du projet.
- Ajout de champ : optionnel, avec défaut explicite. Un champ nouvellement obligatoire impose de traiter les documents antérieurs, ou de rester optionnel.
- **Renommage de champ : à éviter absolument.** Les noms du JSON canonique traversent le modèle Pydantic, le payload Qdrant et les colonnes PostgreSQL (voir `regulatory-code-standards` § 2). Un renommage est une migration à quatre endroits simultanés.
- Après toute migration du JSON : réindexation Qdrant si un champ de filtre est touché, et campagne de référence.

---

## 4. Redis

- **Le cache est jetable** : le vider est une opération sans conséquence, et c'est la conduite normale après tout changement de modèle, de prompt ou d'index. Un cache conservé après réindexation sert des réponses issues de l'ancien index.
- **Les files de validation ne sont pas jetables.** `pending_links`, `pending_alerts`, `pending_responses`, `pending_weights` contiennent du travail humain en cours et des délais d'escalade qui courent.
- Changer la structure d'une file : soit **drainer** — arrêter l'alimentation, laisser les tâches se terminer, migrer à vide — soit **versionner les clés** et faire cohabiter les deux formats le temps de la transition. Jamais de transformation en place sur une file active.
- Le compteur des 72 heures se raisonne à partir d'une date portée par la tâche, jamais de son ancienneté dans la file : une migration qui recrée les entrées remettrait tous les compteurs à zéro et ferait disparaître les escalades dues.

---

## 5. Ordre général d'une migration transverse

Quand un changement touche plusieurs dépôts, l'ordre n'est pas indifférent.

```
1. Sauvegarde complète vérifiée
2. Campagne de référence — état avant
3. Fenêtre de maintenance : arrêt du Watcher et de l'ingestion
4. Drainage ou versionnement des files Redis
5. Migration PostgreSQL (expand)
6. Migration du JSON canonique
7. Réindexation Qdrant dans une nouvelle collection
8. Contrôles de complétude et de cohérence
9. Campagne de référence — état après
10. Bascule d'alias
11. Purge du cache Redis
12. Reprise du Watcher
13. Contrôle d'intégrité de la chaîne d'audit
14. Période d'observation, puis contract PostgreSQL et suppression de l'ancienne collection
```

Les étapes 2 et 9 sont celles qu'on est tenté de sauter, et ce sont les seules qui prouvent que la migration n'a rien cassé.

---

## 6. Erreurs classiques

- **Réindexer en place.** Aucun retour arrière possible, et le service répond faux pendant l'opération.
- **Réindexer depuis l'ancienne collection** plutôt que depuis le JSON canonique : les erreurs de l'ancien index sont recopiées.
- **Changer de modèle d'embedding sans réindexer.** Aucune erreur levée, résultats silencieusement faux.
- **Recalculer les hachages d'audit** pour « réparer » une chaîne. Un audit recalculé ne prouve plus rien ; la rupture, elle, était l'information utile.
- **Migrer sans campagne de référence** : la dégradation est découverte par un utilisateur, des semaines plus tard.
- **Purger l'historique des versions réglementaires** pour gagner de la place. C'est le produit qu'on supprime.
- **Oublier le cache Redis** après une réindexation.
- **Tester la migration sur une base vide.** La durée, les verrous et les cas de données réels n'apparaissent que sur volume réel.

---

## Exemples de prompts

> « Nous passons `bge-m3` à une nouvelle révision. Écris le plan complet : contrôle du besoin de réindexation, nouvelle collection nommée avec modèle et révision, procédure d'indexation depuis le JSON canonique, contrôles de complétude, campagnes avant et après, bascule d'alias et critère de retour arrière. »

> « Nous devons ajouter un champ au périmètre haché de l'audit. Explique les conséquences sur la chaîne existante et propose la conduite : clôture de l'ancienne chaîne, enregistrement de transition, démarrage de la nouvelle. Ne propose aucune solution impliquant un recalcul des hachages passés. »

> « Génère la migration Alembic en expand/contract pour ajouter le suivi de version de prompt aux enregistrements d'audit, avec le script de remplissage idempotent par lots et la procédure de retour arrière. »

> « Vérifie la cohérence entre le JSON canonique et l'index Qdrant : documents sans chunk, points orphelins, champs de filtre manquants, intervalles de validité incohérents. Produis un rapport d'écarts avant de proposer quoi que ce soit. »

> « Le schéma JSON canonique passe en v2 avec un champ de source enrichi. Écris le script de migration idempotent, la conservation des originaux, et détermine si une réindexation Qdrant est nécessaire en justifiant par les champs de filtre touchés. »

## Références

- Alembic — <https://alembic.sqlalchemy.org/>
- Expand / contract pattern — <https://martinfowler.com/bliki/ParallelChange.html>
- Qdrant — collections, alias et instantanés — <https://qdrant.tech/documentation/concepts/collections/>
- PostgreSQL — DDL et verrous — <https://www.postgresql.org/docs/current/explicit-locking.html>
- BGE-M3 — <https://huggingface.co/BAAI/bge-m3>

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
