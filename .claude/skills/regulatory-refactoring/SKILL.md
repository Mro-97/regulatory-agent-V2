---
name: regulatory-refactoring
description: Utiliser pour restructurer le code de Regulatory Agent V2 — architecture modulaire, séparation des responsabilités, réduction de la dette technique et critères de maintenabilité.
author: Regulatory Agent Team
version: 2.0.0
tags: [refactoring, architecture, quality, technical-debt, python]
---

# regulatory-refactoring

## Objectif de la compétence

Transformer une base de code longue, monolithique et faiblement structurée en une architecture **modulaire, testable et réversible**, sans casser le comportement réglementaire du système.

Cette compétence couvre :

- l'établissement d'une base de mesure objective de la dette technique ;
- la mise en place de tests de caractérisation avant toute modification ;
- la découpe en couches (domaine / application / infrastructure / interfaces) ;
- l'inversion de dépendance vis-à-vis de Qdrant, Redis, PostgreSQL et MLX ;
- la migration progressive par étranglement, jamais par réécriture massive ;
- des seuils de qualité vérifiables automatiquement.

Elle ne couvre pas la sécurité (voir `regulatory-api-security` et `regulatory-rag-mlx-security`), ni la stratégie de test globale (voir `regulatory-testing-code-review`), ni les décisions d'architecture (voir `regulatory-agent-architecture`).

## Principe fondamental

> **Refactoriser, c'est changer la structure sans changer le comportement.**

Un commit qui modifie à la fois la structure et le comportement n'est pas un refactoring : c'est une réécriture non vérifiable. Cette règle n'a pas d'exception dans ce projet, parce que le comportement inclut la correction temporelle, l'exactitude des citations et l'intégrité de la chaîne d'audit.

## Méthodologie

### Étape 0 — Refuser de commencer sans filet

Avant la première modification structurelle :

1. Le dépôt est sous contrôle de version, la branche de travail est dédiée, l'état de départ est propre.
2. Les tests existants passent. Si aucun test n'existe pour la zone visée, on écrit d'abord des **tests de caractérisation** : ils décrivent le comportement actuel, y compris ses bizarreries, sans jugement.
3. On sait comment revenir en arrière.

Si ces trois conditions ne sont pas réunies, la première tâche du plan de refactoring est de les réunir — pas de déplacer du code.

### Étape 1 — Établir la base de mesure

Produire un état des lieux chiffré, pas une impression. Outils de référence :

| Mesure | Outil | Ce qu'on cherche |
|---|---|---|
| Lignes par fichier / par fonction | `cloc`, script simple | Modules-dieux, fonctions fleuves |
| Complexité cyclomatique | `radon cc -s -a` | Fonctions à branches multiples |
| Indice de maintenabilité | `radon mi` | Modules les plus coûteux à modifier |
| Erreurs de style et anti-patterns | `ruff check` | Dette diffuse, imports inutilisés |
| Typage | `mypy --strict` (progressif) | Contrats implicites |
| Code mort | `vulture`, couverture | Fonctions jamais appelées |
| Duplication | `pylint --disable=all --enable=duplicate-code` ou `jscpd` | Logique copiée-collée |
| Couplage / cycles | `pydeps`, `import-linter` | Dépendances circulaires, couches franchies |
| Couverture | `pytest --cov` | Zones intouchables sans risque |

Livrer un **tableau des dix pires modules**, classés par (taille × complexité × fréquence de modification dans l'historique Git). Le croisement avec `git log` est important : un module horrible mais jamais modifié est moins urgent qu'un module moyen touché toutes les semaines.

### Étape 2 — Nommer les défauts observés

Les symptômes récurrents à chercher explicitement dans ce projet :

- **Logique métier dans les gestionnaires de routes FastAPI** — la route doit valider, déléguer, formater ; rien d'autre.
- **Clients instanciés au niveau module** (`qdrant = QdrantClient(...)` en haut d'un fichier) — empêche tout test unitaire et toute configuration par environnement.
- **Prompts en dur au milieu du code** — ils doivent être des ressources versionnées, avec un identifiant et une version, pour être auditables.
- **Absence de séparation entre modèle d'API, modèle de domaine et modèle de persistance** — un changement de schéma HTTP casse alors la base.
- **Appels bloquants dans des fonctions `async`** — inférence MLX, extraction PDF, I/O fichiers : cause directe de blocage de la boucle d'événements.
- **`except Exception:` large, voire silencieux** — masque les erreurs réglementaires.
- **Nombres et chaînes magiques** — le seuil de 15 chunks, les TTL, les seuils de similarité, les noms de collections doivent être de la configuration nommée.
- **Parsing JSON dupliqué** — le modèle canonique doit avoir un seul point d'entrée de désérialisation validée.
- **Configuration lue par `os.getenv` dispersée** — un seul objet de configuration typé.
- **Dépendance circulaire entre agents** — signe que la frontière de responsabilité est mal placée.

### Étape 3 — Cible architecturale

Structure en couches, avec une règle de dépendance unique : **les dépendances pointent vers l'intérieur.** Le domaine ne connaît ni FastAPI, ni Qdrant, ni Redis, ni MLX.

```
src/regulatory_agent/
├── domain/               # Entités, valeurs, règles. Zéro import d'infrastructure.
│   ├── documents/        # Document réglementaire, article, version, intervalle de validité
│   ├── evidence/         # Preuve, citation, chaîne d'audit
│   ├── temporal/         # Sémantique temporelle, résolution de version
│   └── errors.py         # Taxonomie d'erreurs métier
├── application/          # Cas d'usage. Orchestre le domaine via des ports.
│   ├── use_cases/        # answer_question, ingest_document, detect_change, validate_answer
│   └── ports/            # Interfaces : VectorStore, DocumentRepository, Cache,
│                         #   Queue, InferenceEngine, EmbeddingModel, SourceFetcher
├── infrastructure/       # Adaptateurs. Implémentent les ports.
│   ├── qdrant/
│   ├── postgres/
│   ├── redis/
│   ├── mlx/
│   ├── embeddings/
│   └── sources/          # EUR-Lex, Légifrance, ANSSI, CNIL
├── interfaces/           # Points d'entrée
│   ├── api/              # Routes FastAPI, schémas d'entrée/sortie, dépendances
│   ├── web/
│   └── cli/
├── agents/               # Orchestrateur et agents spécialisés, construits sur application/
├── prompts/              # Gabarits versionnés, chargés comme ressources
└── config/               # Configuration typée unique
```

Correspondance avec l'architecture physique : cette découpe est **orthogonale** au déploiement. Tout tourne sur `m4pro2`, mais la structure ne doit pas encoder cette hypothèse. Ne pas créer d'arborescence par machine ni par processus — cela figerait le déploiement dans le code source.

### Étape 4 — Séquence de refactoring

Suivre l'ordre. Chaque étape est un lot de commits livrable et réversible.

1. **Geler le comportement** — tests de caractérisation sur les parcours critiques : réponse à une question courante, réponse à une question historique, détection de changement, validation humaine, vérification de citation.
2. **Centraliser la configuration** — un objet `Settings` typé, injecté ; suppression de tous les `os.getenv` dispersés. Aucun changement de comportement.
3. **Extraire les constantes magiques** vers la configuration nommée, avec les valeurs actuelles à l'identique.
4. **Définir les ports** — écrire les interfaces (`Protocol` ou ABC) qui décrivent ce dont l'application a besoin, à partir de l'usage réel du code existant.
5. **Encapsuler les clients** — déplacer chaque instanciation de client vers un adaptateur d'infrastructure construit par injection de dépendance ; supprimer les singletons de module.
6. **Extraire le domaine** — sortir les entités et règles réglementaires (versions, intervalles de validité, résolution temporelle, structure de citation) dans `domain/`, sans dépendance externe. C'est l'étape qui rend le cœur testable en millisecondes.
7. **Extraire les cas d'usage** — vider les gestionnaires de routes vers `application/use_cases/`. La route ne fait plus que valider, appeler, formater.
8. **Séparer les modèles** — schéma d'API ≠ entité de domaine ≠ enregistrement de persistance, avec des convertisseurs explicites.
9. **Corriger l'asynchronisme** — déplacer toute opération bloquante hors de la boucle d'événements (exécuteur dédié ou travailleur), avec des plafonds de concurrence.
10. **Établir la taxonomie d'erreurs** — erreurs de domaine typées, converties en réponses HTTP à la frontière uniquement ; suppression des `except` génériques.
11. **Externaliser les prompts** — fichiers versionnés avec identifiant et version, chargés par un registre ; l'identifiant du prompt est journalisé dans l'audit.
12. **Faire respecter les couches** — configurer `import-linter` avec les contrats de dépendance et l'exécuter en CI. C'est ce qui empêche la dette de revenir.
13. **Supprimer le code mort** — après, jamais avant : on ne supprime que ce que la couverture et l'analyse confirment inutilisé.

### Étape 5 — Technique d'étranglement (strangler fig)

Pour un module trop gros pour être découpé d'un bloc :

1. Créer la nouvelle implémentation à côté de l'ancienne.
2. Introduire une façade qui route vers l'ancienne implémentation par défaut.
3. Basculer un chemin d'appel à la fois derrière un indicateur de configuration.
4. Comparer les sorties ancienne/nouvelle sur des données réelles (exécution en parallèle avec journalisation des écarts) sur les chemins critiques.
5. Retirer l'ancien code quand tous les appels sont basculés et que la période d'observation est close.
6. Retirer l'indicateur de configuration.

Ne jamais laisser deux implémentations actives durablement : c'est de la dette qui double.

### Étape 6 — Seuils de qualité (définition de « terminé »)

Ces seuils sont vérifiés automatiquement, pas à l'œil. Ce sont des cibles de sortie de refactoring, pas des règles absolues — une dérogation doit être explicite et motivée dans le code.

| Critère | Seuil |
|---|---|
| Lignes par module | ≤ 400 |
| Lignes par fonction | ≤ 50 |
| Paramètres par fonction | ≤ 5 (au-delà : objet de paramètres) |
| Complexité cyclomatique | ≤ 10 par fonction |
| Profondeur d'imbrication | ≤ 3 |
| Dépendances circulaires | 0 |
| Violations de couche (`import-linter`) | 0 |
| `ruff check` | 0 erreur |
| `mypy` sur `domain/` et `application/` | strict, 0 erreur |
| Couverture `domain/` | ≥ 90 % |
| Couverture `application/` | ≥ 80 % |
| `except Exception` sans re-levée ni journalisation | 0 |
| Secrets en dur | 0 |
| Nombres magiques hors `config/` | 0 sur les chemins critiques |
| Temps d'exécution des tests unitaires du domaine | < 10 s |

### Étape 7 — Vérifier et rendre compte

Avant de déclarer un lot terminé :

- rejouer la suite complète, y compris les tests temporels, de citation, d'idempotence du Watcher et de file de validation ;
- comparer les mesures de l'étape 1 avant/après et les présenter ;
- lister ce qui a été **réellement exécuté**, ce qui reste non vérifié, et les implications de migration ;
- ne jamais annoncer comme passant un test qui n'a pas été lancé.

## Exemples d'utilisation

**Diagnostic initial**
> « Établis la base de mesure de la dette technique du dépôt : tableau des dix pires modules croisé avec la fréquence de modification Git, et plan de refactoring séquencé. »

**Découpe d'un module-dieu**
> « `api/main.py` fait 2 100 lignes et contient les routes, la logique RAG et l'accès Qdrant. Propose la découpe en domaine/application/infrastructure/interfaces, avec les tests de caractérisation à écrire d'abord et l'ordre des commits. »

**Inversion de dépendance**
> « Extrais les ports `VectorStore`, `Cache` et `InferenceEngine` à partir de l'usage réel du code, puis déplace les clients Qdrant, Redis et MLX vers des adaptateurs injectés. »

**Correction de l'asynchronisme**
> « Identifie tous les appels bloquants dans des fonctions `async` (MLX, extraction PDF, lecture de fichiers) et propose la migration vers un exécuteur dédié avec plafond de concurrence global à la machine. »

**Verrouillage des couches**
> « Configure `import-linter` avec les contrats de dépendance de l'architecture cible et intègre-le à la CI, en listant les violations actuelles à traiter. »

**Migration progressive**
> « Le pipeline de récupération doit être réécrit sans interrompre le service. Applique la technique d'étranglement avec exécution en parallèle et journalisation des écarts. »

## Critères de succès

1. **Comportement préservé** — la suite de tests, notamment temporelle et de citation, donne des résultats identiques avant et après. Toute différence est intentionnelle, documentée et validée.
2. **Mesures améliorées** — les indicateurs de l'étape 1 progressent, chiffres à l'appui.
3. **Seuils atteints** — les critères de l'étape 6 sont respectés ou les dérogations sont explicites et motivées.
4. **Couches respectées** — `import-linter` passe en CI ; le domaine n'importe aucune infrastructure.
5. **Réversibilité** — chaque lot est un ensemble de commits atomiques ; le retour arrière est possible à tout moment.
6. **Testabilité réelle** — les cas d'usage sont testables sans Qdrant, Redis, PostgreSQL ni MLX en fonctionnement.
7. **Invariants du projet intacts** — pas d'inférence distante, pas de déplacement de service entre machines, modèle JSON canonique et sémantique temporelle préservés, chaîne d'audit continue.
8. **Aucune dette cachée introduite** — pas de double implémentation laissée active, pas d'indicateur de configuration abandonné, pas de code mort conservé « au cas où ».
9. **Compte rendu honnête** — ce qui a été testé, ce qui ne l'a pas été, et ce qui reste à faire.

## Liens et références

- Martin Fowler — *Refactoring* et catalogue en ligne — <https://refactoring.com/catalog/>
- Refactoring Guru — catalogue de code smells et de techniques — <https://refactoring.guru/refactoring>
- Michael Feathers — *Working Effectively with Legacy Code* (tests de caractérisation, seams)
- Architecture hexagonale / ports et adaptateurs — <https://alistair.cockburn.us/hexagonal-architecture/>
- Strangler Fig Application — <https://martinfowler.com/bliki/StranglerFigApplication.html>
- `import-linter` — contrats d'architecture en Python — <https://import-linter.readthedocs.io/>
- `ruff` — <https://docs.astral.sh/ruff/>
- `mypy` — <https://mypy.readthedocs.io/>
- `radon` — métriques de complexité — <https://radon.readthedocs.io/>
- FastAPI — organisation en gros projets et injection de dépendances — <https://fastapi.tiangolo.com/tutorial/bigger-applications/>
- Architecture Decision Records — <https://adr.github.io/>

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
