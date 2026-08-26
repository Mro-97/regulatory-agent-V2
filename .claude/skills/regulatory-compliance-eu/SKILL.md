---
name: regulatory-compliance-eu
description: Utiliser pour évaluer la conformité de Regulatory Agent V2 au RGPD, à NIS2, à l'AI Act et à la réglementation machines, identifier les écarts et bâtir un plan de mise en conformité.
author: Regulatory Agent Team
version: 2.0.0
tags: [compliance, rgpd, nis2, ai-act, machinery, gap-analysis]
---

# regulatory-compliance-eu

## Avertissement préalable — à rappeler à chaque usage

Cette compétence produit une **analyse d'écart technique et organisationnelle**, pas un avis juridique. Les qualifications finales (statut d'entité NIS2, classification AI Act, responsabilité produit) relèvent d'un juriste ou du délégué à la protection des données de l'organisation.

Deux règles absolues :

1. **Ne jamais inventer un article, une date, un numéro de règlement ou un délai.** Toute référence doit être vérifiée dans le texte officiel (EUR-Lex, Légifrance) avant d'être affirmée.
2. **Le droit bouge.** Le calendrier d'application de NIS2 en France et de l'AI Act a déjà été modifié plusieurs fois. Toute échéance citée doit porter une **date de vérification** et être revérifiée à la source. Le Watcher du projet est l'outil approprié pour cette revérification.

## Objectif de la compétence

Évaluer la conformité de **Regulatory Agent V2** — le système lui-même, pas les machines de ses utilisateurs — à quatre corpus, et produire un plan de mise en conformité priorisé.

Deux rôles distincts doivent rester séparés dans toute l'analyse :

- **Le système comme traitement** : il traite des données, il est un système d'information, il est un système d'IA → RGPD, NIS2, AI Act s'appliquent **à lui**.
- **Le système comme outil de veille** : il informe sur la réglementation machines → obligations d'exactitude, de traçabilité et de non-substitution à une évaluation de conformité.

Confondre les deux est l'erreur la plus fréquente et la plus coûteuse de ce type d'analyse.

## Corpus couverts

Statut vérifié le **21 août 2026** — à revalider avant tout usage opérationnel.

### 1. RGPD — Règlement (UE) 2016/679

Applicable depuis le 25 mai 2018. En France, complété par la loi n° 78-17 modifiée (« Informatique et Libertés »).

### 2. NIS2 — Directive (UE) 2022/2555

Directive européenne dont la date limite de transposition était le 17 octobre 2024. **En France, la transposition passe par le projet de loi relatif à la résilience des infrastructures critiques et au renforcement de la cybersécurité (« loi résilience »)**, complété par des décrets et référentiels ANSSI. Le calendrier réel d'entrée en application dépend de la publication de ces textes d'application : vérifier l'état d'avancement auprès de l'ANSSI et de MonEspaceNIS2 avant de fixer une échéance dans un plan.

Point d'attention : **Regulatory Agent V2 est un système d'information de l'organisation utilisatrice.** Si cette organisation est une entité essentielle ou importante au sens de NIS2, le système entre dans le périmètre des mesures de gestion des risques et de la sécurité de la chaîne d'approvisionnement.

### 3. AI Act — Règlement (UE) 2024/1689

Entré en vigueur le 1ᵉʳ août 2024, application par phases. État au 21 août 2026 :

- Interdictions et obligations de littératie IA : applicables depuis février 2025.
- Obligations relatives aux modèles d'usage général : applicables depuis août 2025.
- Obligations de transparence (article 50) et pouvoirs d'enquête des autorités : **applicables depuis le 2 août 2026**.
- Exigences relatives aux systèmes à haut risque de l'annexe III : **reportées au 2 décembre 2027** (vérifier le texte modificatif exact).
- Systèmes à haut risque intégrés à des produits réglementés (annexe I, dont les machines) : **échéance au 2 août 2028**.

Qualification à instruire, pas à présumer : un assistant de veille réglementaire n'est pas automatiquement à haut risque. Mais si ses sorties sont utilisées comme composant de sécurité ou pour décider de la conformité d'une machine, la question de l'annexe I devient sérieuse. **Documenter le raisonnement de qualification et le faire valider juridiquement.**

Obligation qui s'applique dès maintenant, quelle que soit la classification : l'utilisateur doit savoir qu'il interagit avec une IA, et le personnel doit être formé à ses capacités, ses limites et ses risques.

### 4. Sécurité des machines

- **Règlement (UE) 2023/1230** relatif aux machines, applicable à partir du **20 janvier 2027**, en remplacement de la directive 2006/42/CE. Introduit notamment des exigences sur la cybersécurité des fonctions de sécurité, les logiciels de sécurité, l'IA dans les systèmes de sécurité et les notices sous forme numérique.
- **Directive 2006/42/CE**, encore applicable jusqu'à la bascule — d'où une période où les deux textes coexistent dans le corpus, ce que la logique temporelle du projet doit gérer correctement.
- Normes harmonisées de référence : EN ISO 12100 (appréciation du risque), EN ISO 13849 / CEI 62061 (parties de commande relatives à la sécurité), CEI 62443 (cybersécurité des systèmes d'automatisation industrielle).
- **Règlement (UE) 2024/2847 (Cyber Resilience Act)** : pertinent si des produits comportant des éléments numériques sont concernés ; à instruire séparément.

## Méthodologie

### Étape 1 — Cartographier les données

Sans cette étape, l'analyse RGPD est du vent. Produire, à partir du code et de l'infrastructure réels :

| Question | Où chercher dans le projet |
|---|---|
| Quelles données personnelles entrent ? | Comptes utilisateurs, questions posées, contenu des documents ingérés, journaux d'accès |
| Où sont-elles stockées ? | PostgreSQL (audit, comptes), Qdrant (payloads **et vecteurs**), Redis (cache, files), fichiers JSON, journaux, sauvegardes |
| Combien de temps ? | TTL Redis, politiques de rétention PostgreSQL, rotation des journaux, cycle de vie des instantanés Qdrant |
| Qui y accède ? | Rôles applicatifs, comptes de service, accès administrateur à la machine |
| Sortent-elles de l'UE ? | Architecture 100 % locale : normalement non — **le vérifier**, y compris télémétrie des bibliothèques, mises à jour, téléchargement de modèles |

**Point technique déterminant :** un embedding bge-m3 calculé à partir d'un texte contenant des données personnelles est une **donnée dérivée qui reste personnelle**. Il doit figurer au registre, suivre la même durée de conservation et être effacé lors de l'exercice du droit à l'effacement. Un système qui supprime la ligne PostgreSQL mais laisse le vecteur dans Qdrant n'est pas conforme.

### Étape 2 — Analyse RGPD

Instruire chaque point avec statut (conforme / écart / non applicable / à vérifier), preuve et action.

**Licéité et gouvernance**

- [ ] Base légale identifiée par finalité (intérêt légitime pour la veille professionnelle, exécution du contrat, obligation légale pour la traçabilité).
- [ ] Registre des activités de traitement à jour, incluant les traitements dérivés (embeddings, cache, journaux d'audit).
- [ ] Rôles qualifiés : responsable de traitement / sous-traitant, y compris pour les prestataires d'hébergement ou d'infogérance.
- [ ] Politique de confidentialité et information des personnes (articles 13 et 14), incluant la mention de l'usage d'un système d'IA.
- [ ] AIPD (article 35) : instruire le besoin. Un traitement à grande échelle, une surveillance systématique ou l'usage de technologies innovantes peuvent la déclencher. Documenter la décision même si la conclusion est négative.

**Principes**

- [ ] **Minimisation** : les documents ingérés contiennent-ils des données personnelles qui ne servent pas la finalité (noms de signataires, coordonnées) ? Prévoir un filtrage ou une pseudonymisation à l'ingestion.
- [ ] **Limitation des finalités** : les journaux d'audit ne servent pas à évaluer les personnes.
- [ ] **Exactitude** : mécanisme de correction et de réindexation.
- [ ] **Limitation de conservation** : durée définie et **appliquée techniquement** pour chaque support — pas seulement écrite dans une politique.
- [ ] **Intégrité et confidentialité** (article 32) : chiffrement au repos de la machine et des sauvegardes, contrôle d'accès, journalisation, tests de restauration.

**Droits des personnes — le point dur du projet**

Le droit à l'effacement entre en tension avec la chaîne d'audit SHA-256, conçue pour être immuable. Cette tension doit être résolue **par conception**, et la résolution documentée :

- [ ] Procédure d'effacement couvrant : source JSON, chunks, **vecteurs Qdrant**, cache Redis, index de recherche, sauvegardes, journaux.
- [ ] La chaîne d'audit ne stocke que des **empreintes et références**, pas de contenu personnel en clair — c'est ce qui permet de la conserver intacte tout en effaçant la donnée.
- [ ] Si du contenu personnel doit figurer dans un enregistrement d'audit, prévoir le chiffrement par clé dédiée et l'effacement cryptographique (destruction de la clé), avec traçabilité de l'opération.
- [ ] Délais de réponse aux demandes tenus (un mois, prolongeable dans les conditions prévues) et procédure testée au moins une fois.
- [ ] Droit d'accès : capacité d'extraire l'ensemble des données relatives à une personne, y compris les questions posées et les entrées d'audit.
- [ ] Décision automatisée (article 22) : le système **assiste**, il ne décide pas. La validation humaine du projet est l'élément qui documente ce point — s'assurer qu'elle n'est pas contournable.

**Violations**

- [ ] Procédure de détection, de qualification et de notification à la CNIL sous 72 heures, avec modèle de notification préparé et registre des violations.

### Étape 3 — Analyse NIS2

Instruire les mesures de gestion des risques (article 21 de la directive) en les rapportant aux contrôles réels du projet. Attention : les exigences françaises applicables seront celles de la loi de transposition et des référentiels ANSSI, qui peuvent être plus précis que la directive.

| Mesure (article 21) | Traduction dans Regulatory Agent V2 |
|---|---|
| Analyse des risques et politique de sécurité | Modèle de menace documenté (voir `regulatory-rag-mlx-security`), politique validée par la direction |
| Gestion des incidents | Détection, qualification, journalisation, procédure d'escalade, exercices |
| Continuité et gestion des crises | La machine unique est un point de défaillance unique : sauvegarde hors machine, restauration testée, plan de reprise, dépendance à Qdrant et PostgreSQL |
| Sécurité de la chaîne d'approvisionnement | Dépendances Python épinglées, SBOM, provenance des poids de modèles, prestataires |
| Sécurité de l'acquisition et du développement | Revue de code, tests de sécurité en CI, gestion des vulnérabilités, correctifs |
| Évaluation de l'efficacité | Audits périodiques, indicateurs, tests d'intrusion |
| Hygiène et formation | Formation des utilisateurs et des administrateurs, y compris à l'IA (recoupe l'AI Act) |
| Cryptographie | Chiffrement en transit et au repos, gestion des clés |
| Sécurité des ressources humaines et contrôle d'accès | Comptes nominatifs, moindre privilège, revue périodique des droits |
| Authentification multifacteur et communications sécurisées | MFA pour les administrateurs, canaux authentifiés pour tout accès distant à la machine |

Notification d'incident : le régime NIS2 prévoit une alerte précoce, puis une notification, puis un rapport final, avec des délais courts. **Vérifier les délais et les destinataires exacts dans le texte français applicable** avant de les inscrire dans une procédure — ne pas les citer de mémoire.

Enregistrement : si l'organisation relève du périmètre, elle doit s'enregistrer auprès de l'ANSSI (MonEspaceNIS2). Cette action est indépendante du niveau de maturité technique et se fait tôt.

### Étape 4 — Analyse AI Act

- [ ] **Qualification argumentée** du système : usage prévu, utilisateurs, nature des sorties, degré d'autonomie. Conclusion motivée sur l'appartenance ou non à l'annexe III ou à l'annexe I, avec les hypothèses qui feraient basculer la qualification.
- [ ] **Transparence (article 50)** : l'interface indique clairement à l'utilisateur qu'il interagit avec un système d'IA et que les réponses doivent être vérifiées. Applicable depuis le 2 août 2026.
- [ ] **Littératie IA** : programme de formation des utilisateurs et administrateurs, avec traçabilité — obligation déjà applicable.
- [ ] **Surveillance humaine** : le circuit de validation humaine du projet est l'implémentation de cette exigence ; documenter le pouvoir réel du valideur (peut-il refuser ? bloquer ? corriger ?) et l'impossibilité de contourner le circuit.
- [ ] **Exactitude et robustesse** : indicateurs mesurés de taux de citation vérifiée, de taux d'abstention, de couverture temporelle ; documentation des limites connues.
- [ ] **Journalisation** : conservation des traces permettant de reconstituer une réponse — la chaîne d'audit SHA-256 du projet y répond, à condition d'être complète.
- [ ] **Documentation technique** : à structurer dès maintenant même si l'échéance haut risque est reportée ; la produire tard coûte beaucoup plus cher (voir `regulatory-docs-audit`).
- [ ] **Modèles d'usage général** : le projet utilise des modèles tiers (Llama, DeepSeek, bge-m3). Vérifier les licences, les conditions d'usage et la documentation fournie par leurs fournisseurs, et conserver ces éléments.

### Étape 5 — Réglementation machines : le rôle du système

Le système ne met pas de machines sur le marché. Ses obligations sont donc **indirectes mais réelles**, parce qu'il oriente des décisions de sécurité.

- [ ] **Périmètre déclaré** : la documentation et l'interface indiquent explicitement que les réponses sont une aide à la veille et **ne constituent ni une évaluation de conformité, ni une déclaration UE de conformité, ni un avis juridique**.
- [ ] **Traçabilité de source** : chaque affirmation renvoie au texte, à l'article et à la version exacte, avec sa date de validité.
- [ ] **Gestion de la transition 2006/42/CE → (UE) 2023/1230** : la logique temporelle doit distinguer les deux régimes, gérer la période de coexistence et ne pas présenter une exigence de l'ancien texte comme applicable après la bascule, ni l'inverse. C'est le cas de test temporel le plus important du projet.
- [ ] **Couverture des normes harmonisées** : indiquer clairement quand une réponse s'appuie sur une norme et quelle version.
- [ ] **Fraîcheur** : indicateur de date de dernière vérification de chaque source, et alerte quand une source n'a pas été rafraîchie au-delà d'un seuil.
- [ ] **Nouveautés du règlement 2023/1230** à couvrir dans le corpus : cybersécurité des fonctions de sécurité, comportement des logiciels de sécurité, systèmes à comportement évolutif, notices numériques, modifications substantielles.

### Étape 6 — Matrice d'écarts et plan

Format de restitution obligatoire, une ligne par écart :

| ID | Corpus | Référence (article/mesure) | Exigence | Statut | Preuve | Écart constaté | Risque | Action corrective | Effort | Responsable | Échéance | Date de vérification de la source |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

Cotation du risque : croiser la **gravité** (sanction encourue, impact sur les personnes, impact opérationnel) et la **probabilité** (exposition réelle, existence de contrôles compensatoires). Ne pas coter tout en « critique » : une matrice où tout est rouge n'oriente rien.

Priorisation recommandée :

1. Ce qui est déjà exigible et manquant (RGPD article 32, transparence AI Act, information des personnes).
2. Ce qui est structurant et long à mettre en place (procédure d'effacement traversant Qdrant, registre, documentation technique).
3. Ce qui devient exigible à échéance connue (règlement machines en janvier 2027, haut risque AI Act ensuite).
4. Ce qui dépend de textes non encore publiés — à suivre par le Watcher, pas à figer dans un plan.

## Exemples d'utilisation

**Analyse d'écart complète**
> « Réalise l'analyse de conformité de Regulatory Agent V2 sur les quatre corpus. Commence par la cartographie des données à partir du code réel, puis produis la matrice d'écarts avec cotation du risque et plan à 30/60/90 jours. »

**Droit à l'effacement**
> « Trace ce qui se passe réellement quand on supprime un document : quelles données restent dans Qdrant, Redis, PostgreSQL, les fichiers JSON, les journaux et les sauvegardes ? Propose la procédure d'effacement complète et le test qui la prouve. »

**Tension audit / effacement**
> « Notre chaîne d'audit SHA-256 est immuable et contient le texte des questions. Comment concilier cela avec le droit à l'effacement ? Propose une conception avec empreintes, chiffrement par clé dédiée et effacement cryptographique. »

**Qualification AI Act**
> « Instruis la qualification du système au regard de l'AI Act : argumente l'appartenance ou non à l'annexe III et à l'annexe I, liste les hypothèses d'usage qui feraient basculer la classification, et déduis les obligations applicables aujourd'hui. »

**Préparation NIS2**
> « Mappe les dix mesures de l'article 21 de NIS2 aux contrôles réellement implémentés dans le projet et identifie les écarts. Signale les points où l'exigence française précise dépend de textes d'application à vérifier. »

**Transition machines**
> « Vérifie que la logique temporelle gère correctement la coexistence puis la bascule entre la directive 2006/42/CE et le règlement (UE) 2023/1230 au 20 janvier 2027. Écris les cas de test. »

## Critères de succès

1. **Aucune référence inventée** — chaque article, date et numéro cité est vérifiable dans le texte officiel, et porte sa date de vérification.
2. **Les incertitudes sont signalées comme telles**, notamment sur le calendrier NIS2 en France et les textes d'application non publiés.
3. **La cartographie des données est issue du code réel**, pas d'une supposition d'architecture.
4. **Les embeddings et les caches figurent explicitement** dans le registre, les durées de conservation et la procédure d'effacement.
5. **La tension audit/effacement est résolue par conception** et documentée, pas contournée ni ignorée.
6. **La matrice d'écarts est actionnable** : chaque ligne a un responsable, un effort estimé et une échéance.
7. **La cotation du risque est discriminante** — tout n'est pas critique.
8. **La distinction est tenue** entre les obligations qui pèsent sur le système et celles qui pèsent sur les machines de ses utilisateurs.
9. **Le caractère non juridique de l'analyse est rappelé** dans le livrable, et les points nécessitant une validation par un juriste ou un DPO sont identifiés nommément.
10. **Les invariants du projet sont préservés** : aucune recommandation n'introduit de traitement hors du périmètre local ni ne casse la chaîne d'audit.

## Liens et références

**Textes**

- RGPD — Règlement (UE) 2016/679 — <https://eur-lex.europa.eu/eli/reg/2016/679/oj>
- Directive NIS2 — (UE) 2022/2555 — <https://eur-lex.europa.eu/eli/dir/2022/2555/oj>
- AI Act — Règlement (UE) 2024/1689 — <https://eur-lex.europa.eu/eli/reg/2024/1689/oj>
- Règlement machines — (UE) 2023/1230 — <https://eur-lex.europa.eu/eli/reg/2023/1230/oj>
- Directive machines — 2006/42/CE — <https://eur-lex.europa.eu/eli/dir/2006/42/oj>
- Cyber Resilience Act — Règlement (UE) 2024/2847 — <https://eur-lex.europa.eu/eli/reg/2024/2847/oj>

**Autorités et ressources**

- CNIL — RGPD, AIPD, IA et données personnelles — <https://www.cnil.fr/>
- ANSSI — NIS2, guides et référentiels — <https://cyber.gouv.fr/>
- MonEspaceNIS2 (enregistrement et FAQ ANSSI) — <https://monespacenis2.cyber.gouv.fr/>
- EDPB / CEPD — lignes directrices — <https://www.edpb.europa.eu/>
- Commission européenne — AI Act et calendrier d'application — <https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai>
- Légifrance — droit français applicable — <https://www.legifrance.gouv.fr/>
- Normes : EN ISO 12100, EN ISO 13849, CEI 62061, CEI 62443, ISO/IEC 27001, ISO/IEC 42001 (management de l'IA)

## Contexte projet

Cette compétence s'applique à **Regulatory Agent V2**, un système local de veille réglementaire et d'assistance IA pour l'industrie. L'ensemble du système tourne sur une **machine unique** : `m4pro2` — Mac Mini M4 Pro, 24 Go de mémoire unifiée. L'architecture distribuée sur trois machines est abandonnée.

Tous les services écoutent exclusivement sur `127.0.0.1` :

- **FastAPI** `:8000` — point d'entrée unique ; sert aussi l'interface web (chat + panneaux de validation).
- **Qdrant** `:6333` — base vectorielle.
- **Redis** `:6379` — cache et files de validation.
- **PostgreSQL** `:5432` — audit, métadonnées, historique.
- **Orchestrateur, agents, Watcher et audit** — modules locaux sur la même machine.

Les modèles sont chargés à la demande, **un seul à la fois** : Llama 3.2 3B (routage), Mistral 7B (Retriever / Citation), Qwen 2.5 7B (Temporel / Explainer), DeepSeek-R1 14B (Conflit, ~20 % des requêtes, 8-10 Go en 4-bit — à ne charger que si c'est réellement nécessaire, puis à décharger).

Le projet impose l'inférence locale avec MLX et s'organise autour des documents réglementaires, de l'historique des versions, des citations exactes, de la validation humaine et de l'auditabilité. Le caractère strictement local de l'inférence est un **atout de conformité majeur** : pas de transfert de données vers un fournisseur d'IA tiers, pas de transfert hors UE lié au traitement. Cet atout doit être documenté et préservé.

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
