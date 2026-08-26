# REGULATORY AGENT V2 — CONTEXTE PROJET

- **Version** : 2.0 — Architecture unique
- **Date** : 25 août 2026
- **Machine** : Mac Mini `m4pro2` — M4 Pro, 24 Go

> **Note de révision.** Cette version reprend intégralement le contexte projet d'origine. Seules les sections dépendantes du matériel ont été modifiées : **2.3**, **2.6**, **3** (entièrement remplacée), **4**, **17**, **22**, **25** et **29**. Toutes les autres sections sont conservées à l'identique.

---

## 0. RÔLE DE CE DOCUMENT

Ce document constitue le contexte permanent du projet.

Tu es l'assistant principal chargé d'accompagner le développement de Regulatory Agent V2. Toutes les décisions, propositions de code et recommandations doivent être cohérentes avec ce contexte.

Les contraintes indiquées comme **ABSOLUES** ne doivent pas être modifiées sans demande explicite de l'utilisateur.

Les choix indiqués comme **CHOIX ACTUELS** peuvent être remis en question si une meilleure solution est démontrée, mais ne doivent pas être remplacés arbitrairement.

Lorsqu'une information n'est pas définie dans ce contexte, tu dois :

1. identifier explicitement le point manquant ;
2. proposer une solution si nécessaire ;
3. distinguer clairement ce qui est une décision du projet de ce qui est une proposition.

Ne jamais inventer une fonctionnalité, une dépendance ou une architecture en prétendant qu'elle a déjà été validée.

---

## 1. IDENTITÉ DU PROJET

**Nom** : Regulatory Agent — V2

**Objectif** :

Construire un système local de veille réglementaire et d'assistance IA destiné principalement à l'industrie.

Les utilisateurs visés sont notamment :

- ingénieurs ;
- techniciens ;
- responsables QHSE ;
- DPO ;
- RSSI ;
- juristes.

Le système doit permettre de :

- rechercher des informations réglementaires ;
- identifier la version applicable d'un texte ;
- prendre en compte la validité temporelle des textes ;
- détecter les modifications réglementaires ;
- produire des réponses en langage naturel ;
- fournir des citations précises ;
- détecter certaines contradictions ;
- conserver une traçabilité complète ;
- faire intervenir un humain lorsque cela est nécessaire.

---

## 2. CONTRAINTES ABSOLUES

Les contraintes suivantes sont fondamentales.

### 2.1 Fonctionnement local

Le système doit fonctionner **100 % localement**.

Aucune inférence ne doit être effectuée via :

- OpenAI ;
- Anthropic ;
- Google ;
- ou toute autre API LLM externe.

Ne jamais proposer une API externe comme dépendance normale du système.

### 2.2 Inférence

L'inférence LLM doit utiliser :

**MLX sur Apple Silicon.**

Ne pas remplacer MLX par PyTorch MPS.

Les modèles doivent être exécutés localement.

### 2.3 Infrastructure

Le système repose sur **une seule machine** :

- **`m4pro2`** : Mac Mini M4 Pro, 24 Go.

L'architecture distribuée prévue initialement est abandonnée. Tous les services tournent en local sur `127.0.0.1`.

### 2.4 Human-in-the-loop

Les décisions critiques doivent pouvoir être soumises à validation humaine.

Le système ne doit pas présenter une conclusion générée automatiquement comme une décision humaine ou une vérité juridique définitive lorsqu'une validation est requise.

### 2.5 Traçabilité

Chaque réponse importante doit pouvoir être auditée.

L'audit doit permettre de reconstruire au minimum :

```
requête
→ documents récupérés
→ chunks utilisés
→ versions des documents
→ agents exécutés
→ réponse générée
→ citations
→ validation humaine éventuelle
```

Un mécanisme de chaînage SHA-256 est prévu pour l'audit trail.

### 2.6 Gestion mémoire

La machine `m4pro2` dispose de 24 Go de RAM unifiée, partagés entre les modèles, Qdrant, PostgreSQL, Redis et le système.

Sur cette machine, les modèles doivent être chargés de manière contrôlée.

Le principe retenu est :

**lazy loading + déchargement des modèles inutilisés.**

Éviter de charger simultanément plusieurs modèles lourds. Le principe de référence est **un seul modèle chargé à la fois**.

DeepSeek-R1 14B est le cas le plus sensible (8 à 10 Go en 4-bit) : il ne doit être chargé que lorsqu'une analyse de conflit est réellement nécessaire, puis déchargé.

---

## 3. ARCHITECTURE UNIQUE

**Matériel disponible** : Mac Mini `m4pro2` — M4 Pro, 24 Go de RAM.

Tous les composants tournent désormais sur cette **seule machine**. L'architecture distribuée prévue initialement est abandonnée.

### 3.1 Composants et ports

| Composant | Adresse / Port | Rôle |
|:---|:---|:---|
| API FastAPI | `127.0.0.1:8000` | Point d'entrée unique |
| Orchestrateur | Module local | Routage des requêtes |
| Llama 3.2 3B | Chargé à la demande | Routage (léger) |
| Mistral 7B | Chargé à la demande | Retriever / Citation |
| Qwen 2.5 7B | Chargé à la demande | Temporel / Explainer |
| DeepSeek-R1 14B | Chargé à la demande | Agent Conflit (20 % des requêtes) |
| Qdrant | `127.0.0.1:6333` | Base vectorielle |
| Redis | `127.0.0.1:6379` | Cache + files d'attente |
| PostgreSQL | `127.0.0.1:5432` | Audit, métadonnées, historique |
| Interface web | Servie par FastAPI | Chat + Validation |
| Watcher | Module autonome | Surveillance des sources |
| Audit | Module intégré | Traçabilité SHA-256 |

### 3.2 Conséquences du changement

- Plus de communication réseau entre machines.
- Tous les services tournent en local sur `127.0.0.1`.
- **Lazy loading systématique** : un seul modèle chargé à la fois.
- DeepSeek 14B doit être utilisé avec précaution (8-10 Go en 4-bit) — ne charger que si vraiment nécessaire.

### 3.3 Configuration recommandée pour `config.py`

```python
# Qdrant
qdrant_host: str = "127.0.0.1"
qdrant_port: int = 6333

# Redis
redis_host: str = "127.0.0.1"
redis_port: int = 6379

# PostgreSQL
postgres_host: str = "127.0.0.1"
postgres_port: int = 5432

# API
api_host: str = "127.0.0.1"
api_port: int = 8000
```

### 3.4 Agents

Les agents restent inchangés dans leurs responsabilités. Seul leur hébergement change : ils s'exécutent tous sur `m4pro2`, en chargeant leur modèle à la demande.

**Retriever** — Recherche les passages pertinents dans Qdrant.

**Agent temporel** — Détermine quelles versions sont applicables à une date donnée.

**Explainer** — Transforme les informations récupérées en réponse compréhensible pour l'utilisateur.

**Citation** — Produit ou vérifie les références exactes utilisées dans la réponse.

**Conflict** — Détecte les contradictions. Il doit être utilisé ponctuellement afin de limiter la consommation mémoire. Le principe actuel est de l'appeler sur environ 20 % des requêtes lorsqu'une analyse de conflit est pertinente.

---

## 4. ARCHITECTURE LOGIQUE

Architecture cible :

```
                         UTILISATEUR
                              │
                              ▼
      ┌─────────────────────────────────────────────┐
      │             m4pro2  —  127.0.0.1            │
      │                                             │
      │  API FastAPI            :8000               │
      │  Interface web          (servie par l'API)  │
      │  Orchestrateur          (module local)      │
      │                                             │
      │  ─────────── Agents (modèles à la demande)  │
      │  Retriever    Temporal    Explainer         │
      │  Citation     Conflict                      │
      │                                             │
      │  ─────────── Services                       │
      │  Qdrant                 :6333               │
      │  Redis                  :6379               │
      │  PostgreSQL             :5432               │
      │                                             │
      │  ─────────── Modules autonomes              │
      │  Watcher                Audit               │
      └─────────────────────────────────────────────┘
```

La communication entre composants est **interne au processus ou locale sur `127.0.0.1`**.

Il n'y a plus de communication réseau entre machines : les mécanismes d'échange distant envisagés précédemment sont sans objet.

Ne pas introduire de dépendance cloud pour cette communication.

Aucun service ne doit être lié à `0.0.0.0` : Qdrant, Redis et PostgreSQL ne sont accessibles que depuis la machine elle-même.

---

## 5. MODÈLES IA

Répartition actuelle :

| Agent | Modèle | Rôle |
|:---|:---|:---|
| Orchestrateur | Llama 3.2 3B | Routage |
| Retriever | Mistral 7B | Recherche |
| Temporal | Qwen 2.5 7B | Raisonnement temporel |
| Explainer | Qwen 2.5 7B | Synthèse |
| Citation | Mistral 7B | Citations |
| Conflict | DeepSeek-R1 14B | Contradictions |

Les modèles exacts pourront évoluer si les contraintes de performance, mémoire ou qualité l'exigent, mais toute modification doit être justifiée.

Tous ces modèles s'exécutent sur `m4pro2` et sont chargés à la demande, un seul à la fois (voir § 2.6).

---

## 6. PIPELINE GLOBAL

Le système doit suivre conceptuellement ce pipeline :

```
SOURCE RÉGLEMENTAIRE
      ↓
INGESTION
      ↓
EXTRACTION
      ↓
NORMALISATION
      ↓
JSON CANONIQUE
      ↓
VERSIONNEMENT
      ↓
INDEXATION QDRANT
      ↓
QUESTION UTILISATEUR
      ↓
ORCHESTRATEUR
      ↓
RETRIEVER
      ↓
FILTRAGE TEMPOREL
      ↓
ANALYSE DE CONFLIT SI NÉCESSAIRE
      ↓
EXPLICATION
      ↓
CITATION / VÉRIFICATION
      ↓
VALIDATION HUMAINE SI NÉCESSAIRE
      ↓
RÉPONSE
      ↓
AUDIT
```

Chaque étape doit être identifiable et testable.

---

## 7. MODÈLE DE DONNÉES PIVOT

Le format JSON constitue le modèle de données central du projet.

Exemple :

```json
{
  "id": "RGPD_2016_679",
  "title": "Règlement (UE) 2016/679...",
  "source": "EUR-Lex",
  "publication_date": "2016-05-04",
  "entry_into_force": "2018-05-25",
  "version": "2026-08-03",
  "themes": [
    "protection_donnees",
    "numerique"
  ],
  "chapters": [
    {
      "id": "chap1",
      "articles": [
        {
          "id": "art_32",
          "title": "Sécurité du traitement",
          "text": "Compte tenu de l'état des connaissances...",
          "valid_from": "2018-05-25",
          "valid_to": "2026-08-02",
          "citations": [
            "art_33",
            "art_35"
          ]
        },
        {
          "id": "art_32_2026",
          "title": "Sécurité du traitement (version 2026)",
          "text": "Compte tenu de l'état des connaissances... (nouveau texte)",
          "valid_from": "2026-08-03",
          "valid_to": null,
          "citations": [
            "art_33",
            "art_35",
            "art_40"
          ]
        }
      ]
    }
  ],
  "related_texts": [
    {
      "ref": "NIS2_2022_2555",
      "relation": "se_chevauche"
    }
  ]
}
```

Les modèles **Pydantic** doivent être cohérents avec ce schéma.

Les dates réglementaires doivent être manipulées avec des types adaptés, notamment `datetime.date` lorsque seule la date est pertinente.

Les champs pouvant être absents ou nuls doivent utiliser une représentation optionnelle appropriée.

---

## 8. RAISONNEMENT TEMPOREL

La temporalité est une fonctionnalité centrale.

Le système doit pouvoir répondre à une question telle que :

> Quelle version d'un article était applicable le 15 juin 2025 ?

Exemple :

```
Version A
valid_from = 2018-05-25
valid_to   = 2026-08-02

Version B
valid_from = 2026-08-03
valid_to   = null
```

Pour une date située avant le 3 août 2026, la version A peut être applicable.

Pour une date située à partir du 3 août 2026, la version B peut être applicable.

Le système doit toujours distinguer :

- date de publication ;
- date d'entrée en vigueur ;
- date de validité ;
- version du document.

Ne jamais confondre « document le plus récent » et « texte applicable à la date demandée ».

---

## 9. SOURCES RÉGLEMENTAIRES

Sources prévues :

- EUR-Lex ;
- Légifrance ;
- ANSSI ;
- CNIL ;
- INERIS.

Le Watcher doit surveiller ces sources et détecter les modifications.

La fréquence actuellement envisagée pour la veille est de **6 heures**, mais cette valeur doit rester configurable.

---

## 10. INGESTION

Pipeline prévu :

```
PDF / HTML / API
      ↓
Extraction du contenu
      ↓
Nettoyage
      ↓
Identification du document
      ↓
Identification des articles
      ↓
Extraction des métadonnées
      ↓
Détection de version
      ↓
Structuration Pydantic
      ↓
JSON canonique
      ↓
Indexation
```

Le système doit conserver les informations nécessaires à la traçabilité.

Ne jamais jeter les métadonnées réglementaires importantes lors du nettoyage.

---

## 11. QDRANT

Qdrant constitue la base vectorielle du projet. Il écoute sur `127.0.0.1:6333`.

Il doit être utilisé pour :

- indexer les chunks ;
- effectuer la recherche vectorielle ;
- conserver les métadonnées ;
- permettre le filtrage ;
- récupérer les passages pertinents.

Le modèle de métadonnées doit permettre notamment de filtrer par :

- document ;
- source ;
- article ;
- version ;
- thème ;
- dates de validité.

---

## 12. WATCHER

Le Watcher surveille les sources réglementaires.

Pipeline :

```
Récupération source
      ↓
Calcul / comparaison hash
      ↓
Détection changement
      ↓
Identification de la modification
      ↓
Création nouvelle version
      ↓
Mise à jour JSON
      ↓
Indexation
      ↓
Création d'une alerte
      ↓
pending_alerts
```

Une modification détectée ne doit pas automatiquement devenir une décision métier.

Les modifications importantes doivent pouvoir être soumises à validation humaine.

---

## 13. HUMAN-IN-THE-LOOP

Redis contient quatre files principales :

```
pending_links
pending_alerts
pending_responses
pending_weights
```

**`pending_links`**
Liens ou relations proposés par l'IA.
Validation par un juriste ou utilisateur autorisé.

**`pending_alerts`**
Alertes générées par le Watcher.
Validation par un expert métier.

**`pending_responses`**
Réponses considérées comme critiques.
Validation par le responsable approprié avant présentation définitive selon la politique définie.

**`pending_weights`**
Propositions de modification des poids ou paramètres.
Validation manuelle avant application.

### Escalade

Une tâche non traitée pendant **72 heures** doit pouvoir être escaladée vers l'administrateur général.

Le délai doit être configurable.

---

## 14. AUDIT TRAIL

Chaque réponse importante doit être traçable.

Le modèle d'audit doit pouvoir associer :

```
request_id
user_query
timestamp
retrieved_documents
retrieved_chunks
document_versions
agents_called
agent_outputs
final_response
citations
human_validation
```

Le système doit s'inspirer du principe de chaînage SHA-256 identifié dans **RAGCompliance**.

L'audit ne doit pas dépendre d'un service externe.

---

## 15. PROJETS OPEN SOURCE IDENTIFIÉS

Le projet prévoit la réutilisation de briques issues de plusieurs projets open source.

### ChronosGuard

Apports recherchés :

- modèle temporel ;
- `valid_from` ;
- `valid_to` ;
- logique de vérification temporelle ;
- mécanismes de citation exacte.

Référence : `jawwad-ali/chronosguard-compliance-rag`

### RAGCompliance

Apports recherchés :

- audit trail ;
- chaînage SHA-256 ;
- traçabilité requête → chunks → réponse.

Référence : `dakshtrehan/ragcompliance`

### Autonomous AI Compliance

Apports recherchés :

- architecture multi-agent ;
- pattern ReAct ;
- séparation des agents spécialisés.

Référence : `shyamraj7292/Autonomous-AI-compliance-platform`

### GRCX

Apports recherchés :

- architecture de veille ;
- Sentinel ;
- Resolver ;
- Audit Log ;
- surveillance de sources réglementaires.

Référence : `grcx-dev/grcx`

### ai-legal-compliance-assistant

Apports recherchés :

- modèle JSON ;
- graphe de connaissances ;
- extraction d'entités juridiques.

Référence : `Ramseygithub/ai-legal-compliance-assistant`

### eu-regulatory-rag

Apports recherchés :

- ingestion EUR-Lex ;
- communication avec les services EUR-Lex ;
- ingestion de textes comme DORA et NIS2.

Référence : `luciendgolden/eu-regulatory-rag`

---

## 16. STRATÉGIE D'INTÉGRATION OPEN SOURCE

Nous ne devons pas copier aveuglément les projets.

Pour chaque composant open source, analyser :

1. son architecture ;
2. son code ;
3. ses dépendances ;
4. sa licence ;
5. son modèle de données ;
6. les composants réellement utiles ;
7. les adaptations nécessaires ;
8. les dépendances incompatibles.

Principe :

```
Projet OSS
    ↓
Analyse
    ↓
Extraction du composant utile
    ↓
Adaptation
    ↓
Remplacement des dépendances incompatibles
    ↓
Tests
    ↓
Intégration
```

Les adaptations majeures prévues sont notamment :

```
LangChain
    ↓
logique RAG propre au projet

ChromaDB
    ↓
Qdrant

Gemini / OpenAI
    ↓
MLX + modèles locaux
```

Ne jamais intégrer une dépendance simplement parce qu'elle existe dans un projet OSS.

---

## 17. COMPOSANTS DÉVELOPPÉS EN PROPRE

Les composants suivants doivent être développés spécifiquement pour Regulatory Agent V2 :

**Wrapper MLX**

Abstraction commune pour charger et utiliser les modèles locaux.

**Gestionnaire de cycle de vie des modèles**

Chargement à la demande et déchargement des modèles inutilisés sur `m4pro2`. Garantit qu'un seul modèle lourd est résident à la fois. *(Remplace l'ancien composant « Orchestration distribuée », devenu sans objet sur machine unique.)*

**Orchestration locale**

Routage des requêtes entre les agents à l'intérieur du processus, sans communication réseau entre machines.

**Interface de validation humaine**

Interface permettant d'approuver ou rejeter :

- liens ;
- alertes ;
- réponses ;
- propositions de paramètres.

**Watcher français**

Adaptation aux sources :

- Légifrance ;
- ANSSI ;
- CNIL ;
- INERIS.

**Qdrant**

Intégration et gestion de la base vectorielle.

**Modèles Pydantic**

Modèles propres au projet résultant de la fusion des besoins identifiés.

---

## 18. STRUCTURE ACTUELLE DU DÉPÔT

Dépôt : `regulatory-agent-V2`

Structure actuelle :

```
regulatory-agent-V2/
├── .gitignore
├── README.md
├── requirements.txt
├── config.py
├── main.py
├── src/
│    └── agents/
├── data/
│    ├── raw/
│    ├── indexed/
│    └── pending/
├── web/
│    ├── templates/
│    └── static/
└── scripts/
```

La structure initiale a été créée et versionnée.

Les principaux composants fonctionnels restent à développer.

Lorsque tu travailles sur le dépôt réel, vérifie toujours l'état actuel des fichiers avant de supposer qu'un fichier est vide ou inexistant.

Ne jamais écraser du code existant sans l'avoir inspecté.

---

## 19. ORDRE DE DÉVELOPPEMENT ACTUEL

L'ordre de développement prévu est :

```
1. src/models.py
      ↓
2. src/mlx_utils.py
      ↓
3. src/temporal.py
      ↓
4. src/audit.py
      ↓
5. src/api.py
      ↓
6. src/orchestrator.py
      ↓
7. src/watcher.py
      ↓
8. web/
```

Cet ordre peut être modifié si une dépendance technique l'impose, mais tout changement doit être expliqué.

---

## 20. MVP ATTENDU

Le MVP doit comporter au minimum :

### Backend

```
config.py
main.py
src/models.py
src/mlx_utils.py
src/api.py
src/orchestrator.py
```

### Ingestion

Scripts permettant :

```
PDF → JSON
```

### Indexation

Scripts permettant :

```
JSON → embeddings → Qdrant
```

### Interface

Interface web minimale comprenant :

- chat ;
- affichage des réponses ;
- citations ;
- panneau de validation humaine.

### Documentation

- README ;
- documentation API ;
- Swagger/OpenAPI ;
- guides d'installation ;
- guides d'utilisation.

---

## 21. CONTRAINTES D'INTERFACE

L'interface doit rester sobre.

Priorité :

```
Chat
+
Panneaux de validation
```

Ne pas développer immédiatement un graphe D3 complexe.

La visualisation en graphe est considérée comme une fonctionnalité de **phase 2**.

---

## 22. COMPÉTENCES DISPONIBLES

Le projet est accompagné de compétences spécialisées Claude.

Elles doivent être utilisées comme référence lorsqu'elles sont pertinentes :

1. `regulatory-agent-architecture` — Architecture
2. `mlx-local-inference` — MLX
3. `regulatory-rag` — RAG
4. `regulatory-temporal-reasoning` — Temporal Reasoning
5. `regulatory-document-ingestion` — Document Ingestion
6. `regulatory-watcher` — Watcher
7. `multi-agent-orchestration` — Multi-Agent
8. `regulatory-evidence-audit` — Evidence & Audit
9. `regulatory-human-validation` — Human Validation
10. `regulatory-testing-code-review` — Testing & Code Review
11. `regulatory-refactoring` — Refactoring et dette technique
12. `regulatory-api-security` — Sécurité des API FastAPI
13. `regulatory-rag-mlx-security` — Sécurité du pipeline RAG et MLX
14. `regulatory-compliance-eu` — Conformité RGPD, NIS2, AI Act, machines
15. `regulatory-docs-audit` — Documentation technique et rapports d'audit

Ces compétences complètent ce contexte.

Le contexte définit **le projet**.

Les compétences définissent la **manière spécialisée de travailler** sur chaque domaine.

En cas de contradiction, les contraintes absolues de ce contexte et les décisions explicitement prises par l'utilisateur ont priorité.

> **Point de vigilance.** Les compétences ont été rédigées avant l'abandon de l'architecture distribuée. Leur section « Project context » décrit encore une répartition sur plusieurs machines et impose de préserver les frontières entre elles. **Ce document fait autorité** : cette contrainte est remplacée par la gestion mémoire du § 2.6. Les compétences restent valides sur tout le reste.

---

## 23. RÈGLES DE DÉVELOPPEMENT POUR CLAUDE

Lorsque tu produis du code :

### Toujours

- fournir du code complet ;
- indiquer le chemin exact du fichier ;
- respecter l'architecture existante ;
- respecter les contraintes MLX ;
- prendre en compte la mémoire Apple Silicon ;
- utiliser des types explicites ;
- gérer les erreurs ;
- ajouter des logs pertinents ;
- prévoir des tests ;
- expliquer les dépendances ajoutées ;
- éviter les dépendances inutiles ;
- vérifier la cohérence avec les autres composants.

### Ne jamais

- appeler OpenAI, Anthropic ou Google pour l'inférence ;
- remplacer MLX par PyTorch MPS ;
- charger inutilement DeepSeek 14B ;
- ignorer la temporalité réglementaire ;
- supprimer les informations de citation ;
- contourner la validation humaine ;
- supprimer l'audit ;
- inventer des données réglementaires ;
- considérer une réponse LLM comme une vérité juridique sans preuve ;
- copier une dépendance OSS sans vérifier sa compatibilité et sa licence ;
- réécrire un fichier existant sans l'avoir inspecté.

---

## 24. MÉTHODE DE TRAVAIL

Pour chaque nouvelle fonctionnalité, suivre autant que possible :

```
1. Comprendre la demande
      ↓
2. Identifier les fichiers concernés
      ↓
3. Inspecter le code existant
      ↓
4. Vérifier les compétences pertinentes
      ↓
5. Identifier les dépendances
      ↓
6. Définir l'implémentation
      ↓
7. Implémenter
      ↓
8. Tester
      ↓
9. Vérifier les contraintes mémoire / architecture
      ↓
10. Documenter
```

Ne pas commencer directement à modifier du code complexe sans comprendre les interfaces existantes.

---

## 25. TESTS

Chaque composant important doit disposer de tests.

Les tests doivent couvrir notamment :

- modèles Pydantic ;
- dates ;
- versions ;
- temporalité ;
- retrieval ;
- Qdrant ;
- citations ;
- audit ;
- Redis ;
- Watcher ;
- API ;
- orchestration ;
- erreurs ;
- **chargement / déchargement des modèles et contraintes mémoire.**

> *La ligne d'intégration entre machines de la version précédente est supprimée : elle n'a plus d'objet. Elle est remplacée par les tests de cycle de vie des modèles, qui constituent le nouveau risque d'intégration sur machine unique.*

Exemple de test temporel :

```
Version A :
2018-05-25 → 2026-08-02

Version B :
2026-08-03 → null

Question :
Quelle version était applicable le 15 juin 2025 ?

Résultat attendu :
Version A
```

Les tests doivent également vérifier les limites :

- date exactement égale à `valid_from` ;
- date exactement égale à `valid_to` ;
- date située entre deux versions ;
- version sans `valid_to` ;
- absence de version applicable.

---

## 26. GESTION DES INCERTITUDES

Le système traite des informations réglementaires.

La prudence est donc obligatoire.

Lorsqu'une information n'est pas suffisamment établie :

- ne pas inventer ;
- signaler l'incertitude ;
- fournir les sources disponibles ;
- demander une validation humaine lorsque nécessaire.

L'agent Conflict doit détecter et signaler les contradictions.

Il ne doit pas être considéré comme une autorité juridique autonome.

---

## 27. PRINCIPES DE QUALITÉ DES RÉPONSES

Une réponse réglementaire de qualité doit idéalement être :

```
Question
↓
Réponse synthétique
↓
Explication
↓
Source
↓
Article
↓
Version
↓
Date de validité
```

Les affirmations importantes doivent pouvoir être reliées à une source.

La réponse doit distinguer :

- ce qui est directement présent dans la source ;
- ce qui est une synthèse ;
- ce qui est une inférence ;
- ce qui nécessite une validation humaine.

---

## 28. OBJECTIF FINAL

L'objectif n'est pas simplement de construire un chatbot.

Regulatory Agent V2 doit devenir un système local capable de :

```
SURVEILLER
      ↓
INGÉRER
      ↓
VERSIONNER
      ↓
INDEXER
      ↓
RECHERCHER
      ↓
RAISONNER SUR LE TEMPS
      ↓
DÉTECTER LES CONFLITS
      ↓
EXPLIQUER
      ↓
CITER
      ↓
FAIRE VALIDER PAR UN HUMAIN
      ↓
AUDITER
```

La priorité est la fiabilité, la traçabilité et la maîtrise locale des données, avant l'ajout de fonctionnalités secondaires.

---

## 29. ÉTAT ACTUEL

Le projet dispose de son architecture et de sa structure initiale.

**Changement matériel** : le parc se réduit à une seule machine, `m4pro2` (M4 Pro, 24 Go). L'architecture distribuée est abandonnée. Ce changement n'affecte ni le pipeline, ni le modèle de données, ni l'ordre de développement : il affecte la configuration (`config.py`, tout sur `127.0.0.1`) et la gestion mémoire, qui devient la contrainte structurante du projet.

Les prochains développements doivent commencer par les modèles de données, puis l'abstraction MLX, avant de construire progressivement les autres composants.

Ne considère pas les choix actuels comme définitivement optimisés : ils constituent la base de travail validée à ce stade.

Toute amélioration architecturale importante doit être proposée et justifiée avant d'être appliquée.

---

## 30. INSTRUCTION PERMANENTE

Tu es l'assistant principal de développement de Regulatory Agent V2.

Lorsque l'utilisateur te demande d'implémenter quelque chose :

1. respecte ce contexte ;
2. utilise les compétences pertinentes ;
3. inspecte l'état réel du projet si les fichiers sont disponibles ;
4. ne suppose pas qu'un composant existe sans le vérifier ;
5. produis une implémentation cohérente avec l'architecture ;
6. fournis les tests nécessaires ;
7. signale clairement les éventuels points bloquants ;
8. ne modifie jamais une contrainte absolue sans accord explicite.

Le développement doit être incrémental.

Ne génère pas plusieurs composants majeurs simultanément lorsque l'utilisateur demande de procéder étape par étape.

---

**FIN DU CONTEXTE PROJET**
