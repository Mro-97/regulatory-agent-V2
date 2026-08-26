---
name: regulatory-rag-evaluation
description: Utiliser pour mesurer la qualité des réponses de Regulatory Agent V2 — jeu de questions de référence, métriques de récupération, exactitude des citations et exactitude temporelle, détection de régression, campagnes reproductibles.
author: Regulatory Agent Team
version: 1.0.0
tags: [evaluation, rag, metrics, regression, gold-set, quality]
---

# regulatory-rag-evaluation

## Objectif de la compétence

Mesurer **la qualité des réponses**, et pas seulement l'absence de plantage.

C'est le trou que les tests classiques ne bouchent pas : une régression de qualité — un filtre temporel qui cesse d'être appliqué, un modèle d'embedding remplacé, un prompt reformulé — ne casse aucun test unitaire. Le système continue de répondre. Il répond faux.

Position par rapport à `regulatory-testing-code-review` : cette compétence-là vérifie que **le code fait ce qu'il prétend** ; celle-ci vérifie que **le système répond juste**. Les deux sont nécessaires et ne se remplacent pas.

## Principe fondamental

> **Trois niveaux se mesurent séparément : la récupération, l'ancrage, la réponse.**

Une bonne réponse produite à partir d'une mauvaise récupération est un accident, pas une réussite. Si les trois niveaux sont agrégés en un seul score, l'accident devient invisible et la cause d'une régression indéterminable.

| Niveau | Question | Dépend de |
|:---|:---|:---|
| **Récupération** | Les bons passages sont-ils remontés ? | Chunking, embeddings, filtres, Qdrant |
| **Ancrage** | La réponse s'appuie-t-elle réellement sur eux ? | Assemblage du contexte, prompt, vérification de citation |
| **Réponse** | Est-elle juste, datée, et refuse-t-elle quand il le faut ? | Agents, temporel, conflit |

---

## 1. Le jeu de référence

### Nature

Un ensemble de questions dont la **bonne réponse est connue et validée par un humain compétent**. C'est un actif du projet, pas un fichier de test : il se versionne, se relit et s'enrichit.

### Composition obligatoire

Un jeu qui ne contient que des questions faciles ne mesure rien. Ces huit familles sont obligatoires.

| Famille | Ce qu'elle éprouve | Comportement attendu |
|:---|:---|:---|
| Question courante simple | Chaîne nominale | Réponse citée, version en vigueur |
| Question historique datée | Filtrage temporel | Version applicable à la date, pas la plus récente |
| Question à la borne exacte | Convention de bornes | Version correcte à `valid_from` et à `valid_to` |
| Question sur version transitoire | Transition de version | Version A avant bascule, B après |
| Question à contradiction | Agent Conflict | Contradiction signalée, non lissée |
| Question hors corpus | Honnêteté | **Refus explicite**, pas de réponse plausible |
| Question ambiguë sans date | Explicitation | Hypothèse énoncée ou clarification demandée |
| Question piégée par la récence | Anti-biais | Ne pas confondre « texte le plus récent » et « texte applicable » |

Les trois dernières familles sont les plus révélatrices et les plus souvent oubliées. **Un système qui ne sait pas refuser n'est pas mesurable.**

### Format d'un cas

```yaml
- id: TMP-014
  family: borne_exacte
  question: "Quelle version de l'article 32 du RGPD s'applique au 2 août 2026 ?"
  as_of: 2026-08-02
  expected:
    document_id: RGPD_2016_679
    article_id: art_32
    version: "2018-05-25"
    must_cite: [art_32]
    must_not_cite: [art_32_2026]
    must_state_date: true
  gold_chunks: [RGPD_2016_679#art_32#c1, RGPD_2016_679#art_32#c2]
  validated_by: juriste
  validated_at: 2026-08-20
  notes: "Borne haute incluse. Cas de bascule le lendemain : voir TMP-015."
```

### Règles de gestion

- **Chaque cas est validé par un humain compétent** et porte la trace de cette validation. Un jeu de référence auto-généré par un LLM mesure la cohérence du LLM avec lui-même.
- Le jeu est **versionné dans le dépôt**. Un résultat de campagne ne veut rien dire sans la version du jeu qui l'a produit.
- **Un cas publié ne se modifie pas.** Une correction crée un nouveau cas et retire l'ancien avec un motif. Sinon les campagnes cessent d'être comparables.
- Taille minimale de départ : une trentaine de cas couvrant les huit familles. En dessous, le bruit dépasse le signal.
- **Boucle avec la validation humaine** : toute réponse rejetée en `pending_responses` est un candidat au jeu de référence. C'est la source d'enrichissement la plus utile du projet, parce qu'elle est constituée d'échecs réels et non d'échecs imaginés.

---

## 2. Métriques

### Récupération

| Métrique | Définition | Pourquoi elle compte |
|:---|:---|:---|
| `recall@k` | Part des chunks d'or présents dans les k remontés | Plafond de qualité : ce qui n'est pas remonté ne peut pas être cité |
| `precision@k` | Part des chunks remontés qui sont pertinents | Contexte pollué = dilution et coût mémoire |
| `MRR` | Rang réciproque moyen du premier chunk d'or | Position dans le contexte, qui influence la génération |
| `temporal_filter_accuracy` | Part des cas où le filtre de validité a été correctement appliqué | Cœur du projet |

### Ancrage et citation

| Métrique | Définition |
|:---|:---|
| `citation_existence_rate` | Part des citations produites qui existent réellement dans le corpus |
| `citation_exactness_rate` | Part des citations pointant le bon article **et** la bonne version |
| `unsupported_claim_rate` | Part des affirmations engageantes sans citation rattachée |

`citation_existence_rate` doit valoir **100 %**. Une citation inexistante n'est pas une dégradation de qualité, c'est un défaut bloquant : la vérification déterministe de citation aurait dû l'intercepter.

### Temporel

| Métrique | Définition |
|:---|:---|
| `version_selection_accuracy` | Part des cas où la version retenue est celle attendue |
| `boundary_accuracy` | Idem, restreint aux cas de borne exacte |
| `anomaly_detection_rate` | Part des recouvrements et trous correctement signalés plutôt qu'arbitrés |

### Réponse

| Métrique | Définition |
|:---|:---|
| `legitimate_refusal_rate` | Part des questions hors corpus correctement refusées |
| `abusive_refusal_rate` | Part des questions couvertes indûment refusées |
| `date_statement_rate` | Part des réponses énonçant la date d'applicabilité |

Ces deux taux de refus se lisent **ensemble**. Un système qui refuse tout obtient un `legitimate_refusal_rate` parfait et ne sert à rien.

### Coût — spécifique à la machine unique

| Métrique | Pourquoi |
|:---|:---|
| Latence p50 / p95 par type de parcours | Un parcours avec chargement de modèle n'est pas comparable à un parcours sans |
| Mémoire crête par campagne | Les 24 Go sont partagés ; la marge se mesure, elle ne se suppose pas |
| Nombre de chargements / déchargements de modèle | Un routage instable fait basculer les modèles et effondre la latence |
| Taux d'invocation de l'agent Conflict | Cible de référence ~20 % ; une dérive coûte 8 à 10 Go par appel |

---

## 3. Protocole de campagne

**Une mesure non reproductible n'est pas une mesure.** Toute campagne fige et journalise :

- version du jeu de référence ;
- révision Git du code ;
- modèle d'embedding **et sa révision**, dimension des vecteurs ;
- instantané ou identifiant de collection Qdrant, nombre de points indexés ;
- modèles d'inférence, quantification, `temperature = 0`, `seed` fixée, `max_tokens` ;
- identifiants **et versions** des prompts utilisés ;
- date de référence utilisée pour les questions « aujourd'hui ».

Ce dernier point est un piège propre à ce projet : un cas dont la réponse attendue dépend de la date du jour changera de résultat sans qu'aucun code n'ait bougé. Ces cas fixent une date de référence explicite (`as_of`).

Le rendu d'une campagne comporte : le tableau des métriques, la comparaison à la campagne de référence, et **la liste nominative des cas en échec** avec, pour chacun, le niveau fautif (récupération, ancrage ou réponse). Un score global sans liste d'échecs n'est pas exploitable.

---

## 4. Détection de régression

| Métrique | Seuil de référence | Tolérance de régression |
|:---|:---|:---|
| `citation_existence_rate` | 100 % | Aucune — bloquant |
| `version_selection_accuracy` | ≥ 98 % | −1 point |
| `boundary_accuracy` | 100 % | Aucune — bloquant |
| `recall@15` | ≥ 90 % | −3 points |
| `citation_exactness_rate` | ≥ 95 % | −2 points |
| `legitimate_refusal_rate` | ≥ 95 % | −3 points |
| `abusive_refusal_rate` | ≤ 5 % | +3 points |
| Latence p95 | À établir | +20 % |

> Ces valeurs sont des **points de départ à calibrer** sur la première campagne de référence. Ce qui n'est pas négociable, ce sont les deux lignes bloquantes : une citation qui n'existe pas et une erreur de borne temporelle sont des défauts, pas des dégradations statistiques.

Une campagne est obligatoire avant : un changement de modèle d'embedding ou de sa révision, un changement de stratégie de chunking, une modification de prompt, un changement de modèle d'inférence, une modification de la logique temporelle ou de routage, une réindexation.

---

## 5. Ce qu'un LLM peut juger, et ce qu'il ne doit jamais juger

| Objet | Méthode |
|:---|:---|
| Existence d'une citation | **Déterministe** — recherche dans le corpus indexé |
| Exactitude d'un article ou d'une version | **Déterministe** — comparaison d'identifiants |
| Validité temporelle | **Déterministe** — comparaison d'intervalles |
| Présence de la date d'applicabilité | **Déterministe** — extraction structurée |
| Fidélité de la reformulation à la source | LLM juge acceptable, avec accord humain mesuré |
| Clarté et utilité de l'explication | LLM juge acceptable, indicatif |

Un LLM juge ne décide jamais si une citation existe ou si une version est applicable. Ce sont exactement les propriétés que le projet interdit de confier à un modèle, et les confier au juge revient à les confier au modèle.

Quand un LLM juge est utilisé, son accord avec l'annotation humaine est mesuré sur un sous-ensemble avant que ses verdicts ne soient exploités.

---

## 6. Erreurs classiques

- **Mesurer la réponse sans mesurer la récupération.** On corrige alors le prompt pour compenser un défaut d'index.
- **Jeu de référence trop facile.** Que des questions courantes : le système paraît excellent et échoue sur la première question historique réelle.
- **Cas modifiés en place** pour « corriger » un échec. Les campagnes deviennent incomparables et la régression disparaît des radars.
- **Absence de cas hors corpus.** Le taux de refus légitime n'est jamais mesuré, donc l'hallucination non plus.
- **Réutiliser le jeu de référence comme jeu de développement.** À force d'ajuster sur lui, il cesse de prédire quoi que ce soit. En garder une part scellée.
- **Ignorer la variance.** Sans `temperature = 0` ni graine fixée, un écart de deux points ne signifie rien.
- **Mesurer sur un index qui bouge.** Une réindexation pendant la campagne invalide la comparaison.

---

## Exemples de prompts

> « Construis le jeu de référence initial : trente cas couvrant les huit familles du § 1, au format YAML donné, à partir des documents réellement indexés. Marque explicitement les cas qui exigent une validation juridique avant usage. »

> « Implémente le harnais de campagne : exécution du jeu, calcul des métriques des § 2, journalisation complète de la configuration du § 3, rendu comparatif avec la campagne de référence et liste nominative des échecs par niveau fautif. »

> « Nous changeons `bge-m3` de révision. Établis le protocole : campagne de référence avant, réindexation contrôlée, campagne après, critères de bascule ou de retour arrière. »

> « Analyse les cinquante dernières réponses rejetées dans `pending_responses` et propose les cas de référence correspondants, en identifiant pour chacun le niveau fautif. »

> « Le taux d'exactitude de citation est passé de 96 % à 91 % entre deux campagnes. Détermine si la cause est la récupération, l'ancrage ou la génération, en t'appuyant sur les métriques par niveau. »

## Références

- RAGAS — métriques d'évaluation RAG — <https://docs.ragas.io/>
- TREC — protocoles d'évaluation de la recherche d'information — <https://trec.nist.gov/>
- BEIR — évaluation hétérogène de la récupération — <https://github.com/beir-cellar/beir>
- NIST AI Risk Management Framework — <https://www.nist.gov/itl/ai-risk-management-framework>

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
