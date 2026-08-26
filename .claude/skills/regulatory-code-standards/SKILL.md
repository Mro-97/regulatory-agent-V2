---
name: regulatory-code-standards
description: Utiliser pour définir, appliquer ou vérifier les normes de code de Regulatory Agent V2 — style Python, typage, docstrings, conventions FastAPI et Pydantic, journalisation, taxonomie d'erreurs, configuration, outillage et seuils bloquants.
author: Regulatory Agent Team
version: 1.0.0
tags: [standards, python, typing, fastapi, pydantic, linting, quality-gates]
---

# regulatory-code-standards

## Objectif de la compétence

Définir **à quoi doit ressembler un fichier de code acceptable** dans Regulatory Agent V2, et rendre cette définition vérifiable par un outil.

Position par rapport aux compétences voisines :

| Compétence | Question à laquelle elle répond |
|:---|:---|
| `regulatory-refactoring` | Comment changer la structure sans changer le comportement ? |
| `regulatory-testing-code-review` | Comment vérifier que le code fait ce qu'il prétend ? |
| **`regulatory-code-standards`** | **À quoi le code doit-il ressembler avant même d'être proposé ?** |

Elle ne couvre ni la sécurité (voir `regulatory-api-security` et `regulatory-rag-mlx-security`), ni la stratégie de test, ni les décisions d'architecture (voir `regulatory-agent-architecture`).

## Principe fondamental

> **Une règle qu'aucun outil ne vérifie n'est pas une norme, c'est une préférence.**

Toute règle de cette compétence est soit contrôlée automatiquement, soit explicitement marquée comme relevant de la revue humaine. Il n'y a pas de troisième catégorie. Les débats de style se règlent dans `pyproject.toml`, pas en revue.

## Posture d'application : stricte

Tous les seuils sont **bloquants sur l'ensemble du dépôt** dès l'adoption de cette compétence. Conséquence assumée : une campagne de mise en conformité précède la reprise des livraisons fonctionnelles. La séquence de cette campagne est décrite au § 12.

Unique échappatoire : une dérogation ponctuelle, sous trois conditions cumulatives.

1. Code de règle explicite — `# noqa: DTZ005`, `# type: ignore[arg-type]`. Une dérogation nue (`# noqa`, `# type: ignore`) est refusée.
2. Commentaire sur la ligne précédente expliquant pourquoi la règle ne s'applique pas ici.
3. La dérogation est inventoriée. `ruff check --statistics` et un `grep` sur `type: ignore` donnent l'état ; il ne doit pas croître d'une revue à l'autre.

Désactiver une règle globalement dans `pyproject.toml` est une décision de projet, pas un contournement individuel : elle se discute et se documente.

---

## 1. Outillage et seuils bloquants

| Outil | Rôle | Commande | Seuil |
|:---|:---|:---|:---|
| `ruff format` | Formatage | `ruff format --check .` | Aucun écart |
| `ruff check` | Lint, tri des imports, modernisation | `ruff check .` | 0 diagnostic |
| `mypy` | Typage | `mypy --strict src/` | 0 erreur |
| `radon cc` | Complexité cyclomatique | `radon cc -s -n C src/` | Aucune fonction au-dessus de B |
| `radon mi` | Indice de maintenabilité | `radon mi -n B src/` | Aucun module sous B |
| `import-linter` | Respect des couches | `lint-imports` | 0 violation |
| `interrogate` | Présence des docstrings | `interrogate -f 95 src/` | ≥ 95 % |
| `vulture` | Code mort | `vulture src/ --min-confidence 80` | 0 constat non justifié |
| `pytest --cov` | Couverture | `pytest --cov=src --cov-fail-under=…` | Domaine ≥ 90 %, global ≥ 70 % |
| `pip-audit` | Vulnérabilités des dépendances | `pip-audit` | 0 vulnérabilité haute ou critique |
| `gitleaks` | Secrets | `gitleaks detect` | 0 constat |

Ces commandes tournent en pre-commit **et** en intégration continue. Le pre-commit accélère la boucle ; il ne fait pas autorité, la CI oui.

### Configuration de référence

```toml
[tool.ruff]
line-length = 100
target-version = "py311"
src = ["src", "tests"]

[tool.ruff.lint]
select = [
  "E", "W",      # pycodestyle
  "F",           # pyflakes
  "I",           # isort
  "N",           # pep8-naming
  "UP",          # pyupgrade
  "B",           # bugbear
  "A",           # builtins masqués
  "C4",          # comprehensions
  "DTZ",         # datetimes naïfs        <- critique pour ce projet
  "T20",         # print / pprint
  "LOG", "G",    # journalisation
  "TRY",         # anti-patterns d'exception
  "PTH",         # pathlib
  "SIM",         # simplifications
  "ARG",         # arguments inutilisés
  "ERA",         # code commenté
  "ANN",         # annotations manquantes
  "S",           # bandit
  "RUF",
]
ignore = ["ANN401"]  # Any explicitement justifié, voir § 3

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S101"]  # assert autorisé dans les tests

[tool.mypy]
strict = true
warn_unreachable = true
disallow_any_explicit = false
plugins = ["pydantic.mypy"]
```

Deux familles méritent une attention particulière dans ce projet :

- **`DTZ`** interdit les `datetime` naïfs. Sur un système dont la correction repose sur les dates, un horodatage sans fuseau est un défaut réglementaire, pas un détail de style.
- **`T20`** interdit `print`. Toute sortie passe par la journalisation structurée (§ 7).

---

## 2. Nommage

| Élément | Convention | Exemple |
|:---|:---|:---|
| Module, paquet | `snake_case` | `temporal_resolution.py` |
| Classe, modèle Pydantic | `PascalCase` | `RegulatoryDocument` |
| Fonction, méthode, variable | `snake_case` | `resolve_version_at` |
| Constante | `UPPER_SNAKE_CASE` + `Final` | `DEFAULT_CHUNK_COUNT: Final = 15` |
| Privé au module ou à la classe | Préfixe `_` | `_normalise_whitespace` |
| Protocol (port) | Nom du rôle, sans suffixe `Interface` | `VectorStore`, `SourceFetcher` |
| Test | `test_<sujet>_<condition>_<attendu>` | `test_resolve_version_at_lower_bound_returns_version_a` |

### Règles propres au projet

**Les noms du domaine réglementaire sont ceux du JSON canonique.** `valid_from`, `valid_to`, `entry_into_force`, `publication_date`, `document_id`, `article_id`, `version`. Le même nom traverse le JSON, le modèle Pydantic, le payload Qdrant et la colonne PostgreSQL. Renommer en `start_date` / `end_date` dans une couche casse la traçabilité et rend l'audit illisible. C'est la règle de nommage la plus importante de cette compétence.

- Pas d'abréviations maison : `doc`, `req`, `val`, `ver`, `tmp` sont refusés au profit de `document`, `request`, `validation`, `version`. Les abréviations universelles (`id`, `url`, `json`, `db`) sont admises.
- Booléens préfixés : `is_`, `has_`, `should_`, `can_`.
- Une fonction qui peut ne rien trouver le dit dans son nom : `resolve_version_at` plutôt que `get_version` ; `find_document` retourne `X | None`, `load_document` lève.
- **Pas de `utils.py`, `helpers.py`, `common.py`, `misc.py`, `tools.py`.** Un module porte un sujet. Ces noms sont l'endroit où la dette s'accumule sans être vue.
- Le vocabulaire métier reste en anglais dans le code (`document`, `article`, `version`), le vocabulaire réglementaire français conserve son terme d'origine dans les données (`legifrance`, `ineris`), jamais traduit.

---

## 3. Typage

`mypy --strict` sur tout `src/`, sans exception de module dans la configuration.

- `from __future__ import annotations` en tête de chaque module.
- `X | None` et non `Optional[X]` ; `list[X]`, `dict[K, V]` et non `List`, `Dict`.
- **`Any` est interdit sauf justification en commentaire.** `dict[str, Any]` comme contrat entre deux couches est un défaut : si la donnée traverse une frontière, elle a un modèle.
- `Protocol` pour les ports de `application/ports/`. `ABC` seulement quand une implémentation partagée le justifie.
- `Final` pour les constantes de module, `Literal` pour un petit ensemble fermé de chaînes, `Enum` pour les états métier (cycle de vie de validation, sévérité d'alerte).
- `TypedDict` uniquement pour la forme d'un JSON externe non maîtrisé, jamais pour un objet du domaine.

### Dates — la règle la plus sensible du projet

```python
# Dates réglementaires : jour, sans heure, sans fuseau.
publication_date: date
entry_into_force: date
valid_from: date
valid_to: date | None          # None = intervalle ouvert. JAMAIS 9999-12-31.

# Horodatages techniques : toujours conscients du fuseau.
detected_at: datetime          # construit avec datetime.now(timezone.utc)
```

- Une date réglementaire est un `datetime.date`. Lui attacher une heure introduit une question de fuseau qui n'a pas de réponse juridique.
- Un horodatage technique (audit, détection Watcher, entrée en file) est un `datetime` **avec fuseau**, en UTC. `datetime.now()` sans argument est refusé par `DTZ`.
- `valid_to = None` signifie « toujours en vigueur ». Une date sentinelle rendrait vraie toute comparaison naïve et masquerait les trous de couverture.
- Toute fonction manipulant un intervalle documente sa **convention de bornes** (voir § 4).

---

## 4. Docstrings

Style **Google**. Obligatoires sur les modules, les classes publiques, les fonctions publiques et **toute fonction du domaine réglementaire**, y compris privée.

Une docstring ne paraphrase pas la signature. `Args: document_id: The document id` est du bruit et doit être supprimé.

Pour une fonction du domaine, trois éléments sont obligatoires quand ils s'appliquent : la **convention de bornes**, l'**unité et le fuseau** des dates, et **ce qui est levé**.

```python
def resolve_version_at(versions: Sequence[ArticleVersion], target: date) -> ArticleVersion:
    """Retourne la version d'article applicable à une date donnée.

    L'intervalle de validité est considéré comme fermé aux deux bornes :
    une version dont ``valid_from`` ou ``valid_to`` est égal à ``target``
    est applicable. ``valid_to`` à ``None`` signifie un intervalle ouvert.

    Args:
        versions: Versions candidates du même article, dans un ordre quelconque.
        target: Date réglementaire visée, sans composante horaire.

    Returns:
        L'unique version applicable à ``target``.

    Raises:
        NoApplicableVersionError: Aucune version ne couvre ``target``.
        OverlappingVersionsError: Plusieurs versions couvrent ``target``.
            C'est une anomalie de données : elle est signalée, jamais arbitrée.
    """
```

`interrogate` vérifie la présence. L'utilité relève de la revue : une docstring présente mais vide de contenu est un constat de revue au même titre qu'une docstring absente.

---

## 5. Conventions FastAPI

Périmètre : `interfaces/api/`.

- **Une route valide, délègue, formate.** Aucune logique métier, aucun accès direct à Qdrant, Redis, PostgreSQL ou MLX dans un gestionnaire de route.
- Un `APIRouter` par domaine fonctionnel, avec `prefix` et `tags` explicites.
- `response_model` sur toute route. `status_code` explicite dès qu'il diffère de 200.
- **Trois familles de modèles distinctes** : schéma d'API (`AnswerResponse`), entité de domaine (`RegulatoryAnswer`), modèle de persistance. Un changement de contrat HTTP ne doit jamais forcer une migration de base.
- Toute dépendance passe par `Depends` : authentification, session, adaptateurs. Aucun client instancié au niveau module.
- `async def` seulement si la fonction attend réellement. Tout appel bloquant — inférence MLX, extraction PDF, lecture de fichier — passe par un exécuteur dédié avec plafond de concurrence. Un appel bloquant dans une coroutine gèle la boucle d'événements et, sur une machine unique, gèle tout le système.
- Le domaine ne lève jamais de `HTTPException`. Il lève ses erreurs métier ; un gestionnaire d'exceptions les traduit en réponses HTTP à la frontière.

---

## 6. Conventions Pydantic

Pydantic v2.

```python
class ArticleVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
```

- **`extra="forbid"` sur tous les modèles du domaine réglementaire.** Un champ inattendu dans un JSON de source est une anomalie d'ingestion à signaler, pas un champ à ignorer silencieusement.
- `frozen=True` sur les entités du domaine : une version réglementaire ne se modifie pas, elle est remplacée.
- Les validateurs vérifient les **invariants** — `valid_from <= valid_to`, identifiant non vide, date analysable — et rien d'autre. Aucune logique métier, aucun accès réseau ou base dans un validateur.
- **Un seul point d'entrée de désérialisation validée** du JSON canonique. Tout parsing dupliqué est un défaut.
- `Field(..., description=...)` sur tout champ exposé dans OpenAPI : la documentation d'API est générée, pas rédigée à part.

---

## 7. Journalisation

- Bibliothèque standard `logging`, configurée en sortie **JSON structurée**. `print` est refusé par `T20`.
- Un logger par module : `logger = logging.getLogger(__name__)`.
- Les paramètres passent par `extra`, jamais par f-string — règle `G` :

```python
logger.info("version resolved", extra={"document_id": doc.id, "version": v.version})
```

| Niveau | Usage |
|:---|:---|
| `DEBUG` | Diagnostic de développement |
| `INFO` | Événement métier notable : version résolue, alerte créée, tâche validée |
| `WARNING` | Dégradation gérée : source injoignable, reprise après échec |
| `ERROR` | Échec d'une opération demandée |
| `CRITICAL` | Indisponibilité : saturation mémoire, base inaccessible |

Champs à propager systématiquement quand ils existent : `request_id`, `document_id`, `version`, `agent`, `duration_ms`.

**Interdits dans les journaux** : secrets et clés d'API, contenu intégral d'une question utilisateur susceptible de contenir des données personnelles, texte intégral d'un document.

Les journaux ne remplacent pas la chaîne d'audit. **L'audit est une donnée, le journal est une trace d'exploitation.** Ne jamais reconstruire une réponse depuis les logs.

---

## 8. Taxonomie d'erreurs

Une hiérarchie unique dans `domain/errors.py`, racine `RegulatoryAgentError`.

```
RegulatoryAgentError
├── ConfigurationError
├── IngestionError            (ExtractionFailedError, MissingMetadataError, …)
├── TemporalError             (NoApplicableVersionError, OverlappingVersionsError,
│                              ValidityGapError)
├── EvidenceError             (InsufficientEvidenceError, CitationNotVerifiedError)
├── InferenceError            (ModelLoadError, GenerationTimeoutError,
│                              StructuredOutputError)
├── ValidationQueueError      (TaskNotFoundError, InvalidTransitionError)
└── AuditIntegrityError
```

- Chaque erreur porte de quoi diagnostiquer — identifiant de document, date visée, identifiant de requête — et **jamais un secret**.
- `except Exception:` est interdit sauf à la frontière la plus externe, et alors avec journalisation complète puis re-levée ou réponse d'erreur explicite.
- `except: pass` est interdit sans exception. Dans le Watcher en particulier : une source indisponible est un `WARNING` tracé et une alerte d'exploitation, jamais un silence.
- `raise NewError(...) from err` obligatoire lors d'une re-levée — règle `B904`. Perdre la cause d'une erreur réglementaire coûte des heures d'enquête.

---

## 9. Configuration

- Un seul objet `Settings` (`pydantic-settings`), typé, injecté par dépendance. **Aucun `os.getenv` ailleurs dans le dépôt.**
- Toute valeur d'exploitation est un champ nommé : hôtes et ports, nombre de chunks récupérés, TTL de cache, seuils de similarité, noms de collections Qdrant, cadence du Watcher, délai d'escalade.
- **Aucun littéral de configuration dans le code.** Le nombre de chunks, le délai de 72 heures et la cadence de 6 heures sont des champs, pas des constantes disséminées.
- Valeurs par défaut sûres : tout hôte par défaut à `127.0.0.1`. Une valeur par défaut à `0.0.0.0` est un défaut de sécurité, pas un choix de commodité.
- `.env` hors dépôt, vérifié par un scan de l'historique Git. Aucun secret en dur, y compris dans les tests et les notebooks.

---

## 10. Organisation d'un module

Ordre canonique du contenu d'un fichier :

1. Docstring de module
2. `from __future__ import annotations`
3. Imports — standard, tiers, projet (tri géré par `ruff`)
4. Constantes `Final`
5. Types et alias
6. Exceptions propres au module
7. Classes
8. Fonctions publiques
9. Fonctions privées

### Seuils bloquants

| Mesure | Seuil |
|:---|:---|
| Lignes par fichier | ≤ 400 |
| Lignes par fonction | ≤ 50 |
| Complexité cyclomatique | ≤ 10 (note B de `radon`) |
| Paramètres par fonction | ≤ 5 — au-delà, un objet |
| Profondeur d'imbrication | ≤ 3 |
| Longueur de ligne | 100 |

Un seuil se franchit en extrayant, jamais en désactivant la règle.

---

## 11. Prompts et ressources

- **Aucun prompt en dur dans le code.** Un prompt est une ressource versionnée dans `prompts/`, chargée par identifiant et version.
- Nommage : `prompts/<agent>/<tâche>.v<N>.md`.
- Le code référence un couple `("citation.verify", 3)`, jamais un chemin de fichier.
- L'identifiant et la version du prompt figurent dans l'enregistrement d'audit de chaque appel.

Le détail de l'écriture, du versionnement et de l'évaluation des gabarits relève de `regulatory-prompt-templates`.

---

## 12. Séquence d'adoption

La posture stricte impose une campagne de mise en conformité. Elle se déroule dans cet ordre, **un lot de commits par étape**, sans aucun changement de comportement — c'est la règle de `regulatory-refactoring`, et elle n'a pas d'exception ici.

| № | Étape | Vérification |
|:--|:---|:---|
| 1 | `ruff format` sur tout le dépôt | Tests verts avant et après, diff purement cosmétique |
| 2 | `ruff check --fix` — imports et corrections automatiques | Idem |
| 3 | Corrections manuelles de lint, **une famille de règles par commit** | Idem |
| 4 | Annotations de types, module par module, en partant de `domain/` | `mypy` progresse module par module |
| 5 | `mypy --strict` activé sans exception | 0 erreur |
| 6 | Seuils de taille et de complexité — par extraction | `radon` sous les seuils |
| 7 | Activation des portes en CI et du pre-commit | La CI refuse une régression |

Tant que l'étape 7 n'est pas franchie, la norme n'existe pas : elle est déclarative.

---

## 13. Checklist de conformité d'un fichier

- [ ] `ruff format --check` et `ruff check` ne signalent rien.
- [ ] `mypy --strict` ne signale rien ; aucun `Any` non justifié, aucun `type: ignore` nu.
- [ ] Docstring de module ; docstrings sur tout ce qui est public et sur tout le domaine.
- [ ] Toute fonction d'intervalle documente sa convention de bornes.
- [ ] Dates réglementaires en `date`, horodatages en `datetime` conscient du fuseau.
- [ ] Noms du domaine identiques à ceux du JSON canonique.
- [ ] Aucun client instancié au niveau module ; tout passe par injection.
- [ ] Aucune logique métier dans un gestionnaire de route.
- [ ] Aucun appel bloquant dans une coroutine.
- [ ] Aucun `except Exception:` hors frontière externe ; `raise ... from` partout.
- [ ] Aucun `print` ; journalisation structurée avec `extra`.
- [ ] Aucun secret, aucune donnée personnelle, aucun texte intégral dans les logs.
- [ ] Aucun littéral de configuration ; tout est dans `Settings`.
- [ ] Aucun prompt en dur.
- [ ] Fichier ≤ 400 lignes, fonctions ≤ 50 lignes, complexité ≤ 10.
- [ ] Tests présents pour tout comportement affectant la correction réglementaire.

---

## Exemples de prompts

> « Établis l'état de conformité du dépôt à `regulatory-code-standards` : sortie de chaque outil du § 1, nombre de constats par famille de règles, et plan de campagne suivant la séquence du § 12 avec une estimation par étape. »

> « `src/api.py` mélange routes, logique RAG et accès Qdrant. Applique les conventions du § 5 : extraction des cas d'usage, injection des adaptateurs, séparation des trois familles de modèles. Un commit par étape, comportement inchangé. »

> « Audite l'usage des dates dans le dépôt : repère les `datetime` naïfs, les dates réglementaires typées `datetime` au lieu de `date`, les sentinelles à la place de `valid_to = None`, et les fonctions d'intervalle sans convention de bornes documentée. »

> « Génère le `pyproject.toml` complet et la configuration `pre-commit` correspondant au § 1, ainsi que le fichier de contrats `import-linter` traduisant la règle de dépendance vers l'intérieur. »

> « Propose la taxonomie d'erreurs complète de `domain/errors.py` à partir des cas d'échec réellement présents dans le code, et la table de correspondance vers les codes de statut HTTP. »

## Références

- PEP 8 — style — <https://peps.python.org/pep-0008/>
- PEP 257 — docstrings — <https://peps.python.org/pep-0257/>
- PEP 484 / PEP 604 — annotations de types — <https://peps.python.org/pep-0484/>
- Ruff — <https://docs.astral.sh/ruff/>
- mypy, mode strict — <https://mypy.readthedocs.io/en/stable/command_line.html#cmdoption-mypy-strict>
- Pydantic v2 — <https://docs.pydantic.dev/latest/>
- FastAPI, bonnes pratiques — <https://fastapi.tiangolo.com/tutorial/bigger-applications/>
- Google Python Style Guide — <https://google.github.io/styleguide/pyguide.html>
- import-linter — <https://import-linter.readthedocs.io/>

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
