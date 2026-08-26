# Regulatory Agent V2 — instructions permanentes

Système **local** de veille réglementaire et d'assistance IA pour l'industrie. Réponses adossées à des preuves citables, correction temporelle, validation humaine, auditabilité.

## Document de référence

**`docs/CONTEXTE_PROJET.md` fait autorité sur tous les invariants du projet.**

Lis-le en entier avant toute intervention non triviale. En cas de contradiction entre une consigne, une compétence et ce document, **le document l'emporte** — et tu signales la contradiction.

## Machine

Tout tourne sur **`m4pro2`** — Mac Mini M4 Pro, 24 Go de mémoire unifiée. L'architecture distribuée est abandonnée.

| Service | Adresse |
|:---|:---|
| API FastAPI + interface web | `127.0.0.1:8000` |
| Qdrant | `127.0.0.1:6333` |
| Redis | `127.0.0.1:6379` |
| PostgreSQL | `127.0.0.1:5432` |

Modèles chargés à la demande, **un seul à la fois** : Llama 3.2 3B (routage), Mistral 7B (Retriever / Citation), Qwen 2.5 7B (Temporel / Explainer), DeepSeek-R1 14B (Conflit, ~20 % des requêtes, 8-10 Go en 4-bit — ne charger que si nécessaire, puis décharger).

## Contraintes absolues

1. Aucune API d'inférence IA externe. Jamais, y compris en repli.
2. MLX sur Apple Silicon. Ne pas remplacer par PyTorch MPS.
3. Un seul modèle résident à la fois. Chargement à la demande, déchargement explicite.
4. Aucun service lié à `0.0.0.0`. Tout sur `127.0.0.1`.
5. Préserver le modèle JSON canonique et la sémantique temporelle.
6. Les réponses réglementaires sont adossées à des preuves, jamais des conclusions juridiques.
7. La validation humaine ne se contourne pas. L'audit ne se supprime pas.
8. Autour de chaque appel LLM, des composants déterministes et testables. Un LLM n'est jamais l'autorité sur la validité temporelle, l'existence d'une citation, une empreinte, l'état d'une file, une permission ou l'intégrité de l'audit.

## Environnement — pas de droits administrateur

**Tu n'as pas `sudo` ni les droits admin.**

- Ne tente jamais une commande `sudo`, même pour tester.
- Tu travailles en espace utilisateur : dépôt, environnement virtuel, configuration applicative, services sous le compte courant.
- Quand une élévation est nécessaire : **arrête-toi**, écris la commande exacte, explique pourquoi elle est nécessaire et ce qu'elle change, attends. Regroupe ces commandes plutôt que d'interrompre à répétition.
- Ne contourne pas. Ne cherche pas d'alternative non demandée.

## Ne jamais inventer

- **Chaque chiffre rapporté est accompagné de la commande qui l'a produit.** Sinon, écris `NON VÉRIFIÉ`.
- Ne fabrique jamais une sortie de commande.
- Ne devine ni chemin, ni port, ni version, ni nom de service. Vérifie ou déclare l'inconnu.
- **Ne déclare jamais réussi un test que tu n'as pas lancé**, ni conforme un contrôle que tu n'as pas exécuté.
- N'invente aucun fait réglementaire, version, date, article ou citation.
- Information manquante : identifie le point, propose si nécessaire, et distingue toujours une décision du projet de ta proposition.
- Si tu ne sais pas, dis-le.

## Compétences

**Charge la compétence pertinente avant d'agir, pas après.** Elles définissent la manière spécialisée de travailler ; ce fichier et le document de contexte définissent le projet.

| Domaine | Compétence |
|:---|:---|
| Architecture, frontières, budget mémoire | `regulatory-agent-architecture` |
| Orchestrateur et agents | `multi-agent-orchestration` |
| Inférence MLX, cycle de vie des modèles | `mlx-local-inference` |
| Récupération, chunking, embeddings, Qdrant | `regulatory-rag` |
| Versions, intervalles de validité, questions historiques | `regulatory-temporal-reasoning` |
| Sources, extraction, JSON canonique | `regulatory-document-ingestion` |
| Veille, détection de changement, alertes | `regulatory-watcher` |
| Files Redis, validation, escalade 72 h | `regulatory-human-validation` |
| Citations, provenance, chaîne SHA-256 | `regulatory-evidence-audit` |
| Tests, revue de code, mise en production | `regulatory-testing-code-review` |
| **Normes de code, typage, conventions** | **`regulatory-code-standards`** |
| Restructuration, dette technique | `regulatory-refactoring` |
| **Exploitation, sauvegarde, incidents** | **`regulatory-operations`** |
| **Migrations, réindexation** | **`regulatory-data-migrations`** |
| **Qualité des réponses, jeu de référence** | **`regulatory-rag-evaluation`** |
| **Gabarits de prompts versionnés** | **`regulatory-prompt-templates`** |
| Sécurité API FastAPI | `regulatory-api-security` |
| Sécurité RAG et MLX | `regulatory-rag-mlx-security` |
| RGPD, NIS2, AI Act | `regulatory-compliance-eu` |
| Documentation et rapports d'audit | `regulatory-docs-audit` |

## Règles de développement

**Toujours**

- Inspecter le dépôt réel avant de proposer un changement structurel. Ne jamais raisonner sur une architecture supposée.
- Fournir du code complet, avec le chemin exact du fichier.
- Types explicites, gestion des erreurs, journalisation structurée, tests.
- Un commit par sujet. **Jamais structure et comportement dans le même commit.**
- Proposer le plus petit changement cohérent.
- Expliquer les conséquences transverses avant un changement d'architecture.
- Garder secrets et identifiants hors du code source et des journaux.

**Jamais**

- Réécrire un fichier existant sans l'avoir inspecté.
- Client instancié au niveau module — injection de dépendance.
- Logique métier dans un gestionnaire de route FastAPI.
- Prompt en dur — ressource versionnée dans `prompts/`, chargée par identifiant et version.
- Appel bloquant dans une fonction `async` (MLX, extraction PDF, I/O fichier).
- `except Exception:` large ou silencieux. `raise ... from err` obligatoire.
- Nombre ou chaîne magique — configuration nommée dans l'objet `Settings` typé unique.
- `os.getenv` dispersé.
- `git push --force`, réécriture d'historique, suppression de données.

## Dates — règle la plus sensible du projet

- Date réglementaire (`publication_date`, `entry_into_force`, `valid_from`, `valid_to`) : `datetime.date`.
- Horodatage technique (audit, détection Watcher) : `datetime` **avec fuseau**, en UTC.
- `valid_to = None` signifie intervalle ouvert. **Jamais de sentinelle** type `9999-12-31`.
- Toute fonction manipulant un intervalle documente sa **convention de bornes**.
- Les noms du domaine sont ceux du JSON canonique, identiques du JSON à PostgreSQL en passant par Pydantic et le payload Qdrant. Ne jamais renommer en `start_date` / `end_date`.

## Fin de tâche

Avant de déclarer une étape terminée, dis :

1. **ce qui a changé** ;
2. **ce qui a été réellement testé** ;
3. **ce qui reste non vérifié** ;
4. **les implications de migration ou les points bloquants**.

Le développement est incrémental. Ne génère pas plusieurs composants majeurs simultanément quand il est demandé de procéder étape par étape.
