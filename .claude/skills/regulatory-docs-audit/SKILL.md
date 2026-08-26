---
name: regulatory-docs-audit
description: Utiliser pour structurer la documentation technique de Regulatory Agent V2 (architecture, API, déploiement, sécurité) et produire des rapports d'audit sécurité, conformité ou qualité du code.
author: Regulatory Agent Team
version: 2.0.0
tags: [documentation, audit, reporting, architecture, adr]
---

# regulatory-docs-audit

## Objectif de la compétence

Deux livrables, une même exigence de preuve :

1. **La documentation technique** du projet — ce qui permet à quelqu'un d'autre (ou à soi-même dans six mois) de comprendre, exploiter, dépanner et faire évoluer le système.
2. **Les rapports d'audit** — sécurité, conformité et qualité du code — exploitables par une direction comme par un développeur, et opposables à un auditeur externe.

Règle transverse : **rien n'est affirmé sans preuve.** Un contrôle non testé est « non vérifié », pas « conforme ». Une commande non exécutée n'est pas rapportée comme exécutée. C'est ce qui fait la différence entre un rapport d'audit et un document de communication.

## Partie 1 — Documentation technique

### Principe d'organisation

Combiner deux cadres complémentaires :

- **Diátaxis** pour le type de contenu : tutoriels (apprendre), guides pratiques (résoudre un problème), référence (consulter), explication (comprendre). Ne jamais mélanger deux types dans un même document — c'est la cause principale des documentations illisibles.
- **C4 / arc42** pour l'architecture : contexte, conteneurs, composants, code, avec des vues à granularité croissante.

### Arborescence cible

```
docs/
├── README.md                       # Point d'entrée : qu'est-ce que c'est, par où commencer
├── architecture/
│   ├── 01-contexte.md              # C4 niveau 1 : système, utilisateurs, systèmes externes
│   ├── 02-conteneurs.md            # C4 niveau 2 : services, ports et responsabilités  
│   ├── 03-composants.md            # C4 niveau 3 : agents, retriever, watcher, gateway
│   ├── 04-flux-donnees.md          # Ingestion → index → requête → réponse → audit
│   ├── 05-modele-donnees.md        # JSON canonique, schéma PostgreSQL, payload Qdrant, clés Redis
│   ├── 06-modele-temporel.md       # Versions, intervalles de validité, résolution
│   └── decisions/                  # ADR — une décision par fichier, numérotée
│       ├── 0001-inference-locale-mlx.md
│       ├── 0002-qdrant-comme-vector-store.md
│       └── ...
├── api/
│   ├── reference.md                # Généré depuis OpenAPI, pas écrit à la main
│   ├── authentification.md
│   ├── erreurs.md                  # Taxonomie, codes, format de réponse
│   └── contrats-internes.md        # Contrats d'appel entre modules locaux         
├── exploitation/
│   ├── installation.md
│   ├── deploiement-3-macs.md       # Qui tourne où, ports, dépendances de démarrage
│   ├── configuration.md            # Toutes les variables, valeurs par défaut, effets
│   ├── sauvegarde-restauration.md  # Procédure testée, RPO/RTO
│   ├── supervision.md              # Métriques, seuils, alertes
│   └── runbooks/                   # Un fichier par incident type
│       ├── qdrant-indisponible.md
│       ├── oom-mac-a.md
│       ├── file-validation-bloquee.md
│       └── source-inaccessible.md
├── securite/
│   ├── modele-menace.md
│   ├── controles.md                # Contrôle → implémentation → preuve → test
│   ├── gestion-secrets.md
│   ├── gestion-incidents.md
│   └── inventaire-modeles.md       # Modèle, révision, empreinte, machine, usage
├── conformite/
│   ├── registre-traitements.md
│   ├── matrice-conformite.md
│   ├── conservation-effacement.md
│   └── documentation-ia.md         # Base de la documentation technique AI Act
├── qualite/
│   ├── strategie-test.md
│   ├── standards-code.md
│   └── metriques.md                # Base de mesure et évolution
└── audits/
    ├── TEMPLATE-rapport-audit.md
    └── AAAA-MM-JJ-<type>-audit.md
```

### Règles de qualité documentaire

- **Chaque document porte en tête** : propriétaire, date de dernière révision, statut (brouillon / validé / obsolète), périmètre.
- **Un document sans révision depuis douze mois est signalé comme potentiellement obsolète**, automatiquement si possible.
- **Diagrammes comme code** — Mermaid dans le dépôt, versionnés avec le code. Pas d'image binaire non éditable.
- **Aucun secret, aucune donnée personnelle réelle** dans la documentation, y compris dans les exemples : utiliser des valeurs factices reconnaissables.
- **La référence d'API est générée** depuis le schéma OpenAPI. Une référence écrite à la main diverge en quelques semaines.
- **Les exemples de code sont exécutables** et vérifiés en intégration continue (au minimum les liens et les blocs de commande critiques).
- **Un ADR n'est jamais modifié après acceptation** : il est remplacé par un nouvel ADR qui le supersède, en le référençant. C'est ce qui préserve l'historique du raisonnement.
- **Écrire pour le lecteur qui découvre**, pas pour celui qui a écrit le code.

### Modèle d'ADR

```markdown
# ADR-NNNN : <titre de la décision>

- **Statut** : proposé | accepté | remplacé par ADR-XXXX | déprécié
- **Date** : AAAA-MM-JJ
- **Décideurs** : ...

## Contexte
Quelle situation impose une décision. Contraintes techniques, réglementaires, matérielles.

## Options envisagées
1. ... — avantages, inconvénients
2. ...

## Décision
Ce qui a été retenu, et pourquoi cette option plutôt que les autres.

## Conséquences
Positives, négatives, et ce que cette décision rend plus difficile à l'avenir.

## Vérification
Comment on saura que la décision tient : test, métrique, seuil.
```

## Partie 2 — Rapport d'audit

### Structure obligatoire

Le rapport doit être lisible à trois profondeurs : une page pour la direction, cinq pages pour un responsable, l'intégralité pour un développeur.

```markdown
# Rapport d'audit — <sécurité | conformité | qualité du code>
Regulatory Agent V2 · Version auditée : <commit / tag> · Date : AAAA-MM-JJ

## 1. Synthèse pour la direction          (1 page maximum)
- Objet et périmètre en trois lignes
- Verdict global et niveau de risque résiduel
- Nombre de constats par sévérité
- Les trois actions les plus urgentes
- Décision demandée (mise en service : oui / oui sous conditions / non)

## 2. Périmètre et limites
- Ce qui a été audité : composants, machines, version exacte du code
- Ce qui n'a PAS été audité, et pourquoi
- Limites de l'exercice : accès non obtenus, environnements non représentatifs,
  contrôles non testables en l'état
- Environnement de test et données utilisées

## 3. Méthodologie
- Référentiels appliqués (OWASP ASVS, OWASP LLM Top 10, article 21 NIS2, RGPD, ...)
- Outils et versions exactes
- Nature des vérifications : revue de code / test dynamique / entretien / inspection
  de configuration
- Échelle de sévérité employée (définie, pas supposée)

## 4. Synthèse des constats
Tableau récapitulatif, trié par sévérité décroissante.

| ID | Sévérité | Composant | Titre | Statut |
|----|----------|-----------|-------|--------|

Puis un graphique ou un décompte par sévérité et par composant.

## 5. Constats détaillés
Une fiche par constat (voir modèle ci-dessous).

## 6. Points conformes
Ce qui fonctionne bien. Un rapport qui ne liste que des problèmes donne une image
fausse du système et démobilise l'équipe.

## 7. Plan de remédiation
Priorisé, avec responsables et échéances : 0-30 j / 31-60 j / 61-90 j / au-delà.
Chaque action porte un identifiant de constat.

## 8. Risques résiduels acceptés
Ce qui n'est pas corrigé, pourquoi, qui l'accepte, et jusqu'à quelle date.

## 9. Annexes
- Inventaire des endpoints / des traitements / des modèles selon le type d'audit
- Sorties d'outils brutes
- Journal des vérifications effectuées
- Glossaire
```

### Modèle de fiche de constat

```markdown
### [SEV-NNN] Titre court et factuel

| Champ | Valeur |
|---|---|
| **Sévérité** | Critique / Élevée / Moyenne / Faible / Informationnel |
| **Catégorie** | Authentification / Injection / Configuration / Conformité / Maintenabilité |
| **Composant** | API FastAPI — `m4pro2:8000` |
| **Référentiel** | OWASP API1:2023 / RGPD art. 32 / NIS2 art. 21(d) |
| **Statut** | Ouvert / En cours / Corrigé / Risque accepté |

**Description**
Ce qui a été observé, factuellement.

**Preuve**
Fichier et ligne, extrait de configuration, requête et réponse, sortie d'outil.
Reproductible par un tiers.

**Scénario d'exploitation ou d'impact**
Conditions de départ concrètes → conséquence concrète. Pas de formulation vague
du type « pourrait poser problème ».

**Impact**
Technique, opérationnel, réglementaire.

**Correction recommandée**
Solution concrète, avec le code ou la configuration cible.

**Vérification**
Comment prouver que c'est corrigé : test à écrire, commande à exécuter,
résultat attendu.

**Effort estimé** : faible (< 1 j) / moyen (1-5 j) / élevé (> 5 j)
**Responsable** :
**Échéance** :
```

### Échelle de sévérité — à reproduire dans chaque rapport

| Sévérité | Définition |
|---|---|
| **Critique** | Exploitation possible sans authentification, ou compromission de l'intégrité réglementaire (réponses falsifiables, chaîne d'audit cassable), ou violation de données caractérisée. Bloque la mise en service. |
| **Élevée** | Exploitation possible avec un compte valide, élévation de privilèges, fuite entre périmètres, non-conformité déjà exigible avec sanction encourue. Correction sous 7 jours. |
| **Moyenne** | Absence de défense en profondeur, exposition d'information, non-conformité à échéance future, dette technique bloquant la vérification. Correction sous 30 jours. |
| **Faible** | Durcissement recommandé, écart de bonne pratique sans impact démontré. Backlog. |
| **Informationnel** | Observation utile sans écart : dette assumée, point de vigilance, recommandation d'évolution. |

### Règles de rédaction non négociables

1. **Distinguer observé, testé et supposé.** Trois formulations distinctes : « le code appelle X » (observé), « la requête Y a retourné Z » (testé), « il est probable que » (supposé, et à marquer comme tel).
2. **Ne jamais rapporter comme exécuté un test qui ne l'a pas été.**
3. **Chaque constat est reproductible** par un tiers à partir de la fiche seule.
4. **Pas de gonflement de sévérité.** Un rapport où tout est critique ne hiérarchise rien et sera ignoré.
5. **Pas de constat sans correction proposée.**
6. **Aucun secret réel dans le rapport** — jetons, mots de passe et clés sont masqués et décrits, jamais reproduits.
7. **La version auditée est identifiée par un commit précis.** Un rapport sur « la branche principale » n'est pas auditable trois mois plus tard.
8. **Les limites sont énoncées d'emblée**, pas dissimulées en annexe.

### Rapport de suivi

Un audit sans suivi ne sert à rien. Prévoir un rapport de suivi qui reprend chaque identifiant de constat avec : statut actuel, preuve de correction, test de non-régression associé, et date de clôture. Les constats critiques et élevés ne se ferment que sur preuve de test, jamais sur déclaration.

## Exemples d'utilisation

**Mise en place de la documentation**
> « Le projet n'a qu'un README. Génère l'arborescence `docs/` complète avec les documents d'architecture C4 niveaux 1 à 3 renseignés à partir du code réel, les diagrammes Mermaid, et les ADR rétroactifs pour les décisions structurantes déjà prises (MLX local, Qdrant, passage à une machine unique). »

**Runbook d'incident**
> « Écris le runbook `oom-mac-a.md` : symptômes, diagnostic, actions immédiates, remise en service, prévention, avec les commandes exactes. »

**Rapport d'audit sécurité**
> « Formalise les constats de l'audit de sécurité API en rapport complet suivant le modèle : synthèse direction, périmètre et limites, méthodologie, fiches de constat avec preuves fichier/ligne, plan de remédiation 30/60/90. »

**Rapport de conformité**
> « Produis le rapport d'audit de conformité à partir de la matrice d'écarts RGPD/NIS2/AI Act, avec verdict par corpus et risques résiduels à faire accepter formellement. »

**Rapport qualité de code**
> « Produis le rapport d'audit qualité : base de mesure, dix pires modules, violations des seuils, plan de refactoring séquencé, et ce qui bloque aujourd'hui la mise en production. »

**Suivi**
> « Génère le rapport de suivi de l'audit du <date> : statut de chaque constat, preuves de correction, tests de non-régression, et ce qui reste ouvert. »

## Critères de succès

**Documentation**

1. Un nouvel arrivant peut installer, démarrer et comprendre le système sans solliciter l'équipe.
2. Chaque décision d'architecture structurante a son ADR, avec son contexte et ses conséquences.
3. Les diagrammes sont dans le dépôt, éditables, et à jour avec le code.
4. La référence d'API est générée, pas recopiée.
5. Chaque incident type a son runbook, testé au moins une fois.
6. Aucun secret ni donnée personnelle réelle dans `docs/`.
7. Chaque document a un propriétaire et une date de révision.

**Audit**

8. La synthèse tient en une page et permet une décision.
9. Chaque constat est reproductible à partir de sa seule fiche.
10. Aucune affirmation non étayée ; les points non testés sont marqués comme tels.
11. La sévérité est justifiée par un scénario d'impact concret, pas par une impression.
12. Le périmètre et les limites sont explicites dès la section 2.
13. Le plan de remédiation a des responsables, des efforts et des échéances.
14. Les risques résiduels sont formellement acceptés, datés et signés.
15. Un rapport de suivi est planifié, et les constats critiques ne se ferment que sur preuve de test.

## Liens et références

- Diátaxis — cadre de documentation technique — <https://diataxis.fr/>
- C4 model — <https://c4model.com/>
- arc42 — modèle de documentation d'architecture — <https://arc42.org/>
- Architecture Decision Records — <https://adr.github.io/>
- Mermaid — diagrammes comme code — <https://mermaid.js.org/>
- OWASP ASVS — utilisable comme checklist d'audit — <https://owasp.org/www-project-application-security-verification-standard/>
- OWASP Web Security Testing Guide — <https://owasp.org/www-project-web-security-testing-guide/>
- CVSS v4.0 — cotation de vulnérabilités — <https://www.first.org/cvss/>
- ISO/IEC 27001 Annexe A — référentiel de contrôles — <https://www.iso.org/standard/27001>
- ISO/IEC 42001 — management des systèmes d'IA — <https://www.iso.org/standard/81230.html>
- ANSSI — guides et référentiels d'audit — <https://cyber.gouv.fr/publications>
- OpenAPI Specification — <https://spec.openapis.org/oas/latest.html>

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
