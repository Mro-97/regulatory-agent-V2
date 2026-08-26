---
name: regulatory-api-security
description: Utiliser pour auditer ou durcir la sécurité des API FastAPI de Regulatory Agent V2 (authentification, JWT, CORS, validation, injections, SSRF, rate limiting, exposition des services).
author: Regulatory Agent Team
version: 2.0.0
tags: [security, fastapi, api, jwt, audit]
---

# regulatory-api-security

## Objectif de la compétence

Auditer et durcir la surface d'exposition HTTP de **Regulatory Agent V2** : l'API FastAPI et l'interface web sur `m4pro2` (`127.0.0.1:8000`), ainsi que les interfaces internes des agents et modules (Retriever, Temporal Agent, Explainer, Citation Agent, Conflict Agent, Watcher).

Périmètre couvert :

- authentification et gestion de session (JWT, refresh, révocation) ;
- autorisation (RBAC, contrôle au niveau objet, IDOR) ;
- CORS, en-têtes de sécurité, exposition de `/docs` et `/openapi.json` ;
- validation d'entrée Pydantic et gestion des fichiers téléversés ;
- injections (SQL/PostgreSQL, filtres Qdrant, clés Redis, chemins, commandes) ;
- SSRF sur le Watcher (EUR-Lex, Légifrance, ANSSI, CNIL) ;
- rate limiting, quotas et protection des endpoints d'inférence MLX ;
- secrets, journalisation, gestion d'erreurs ;
- confinement des services sur la boucle locale (`127.0.0.1`).

Hors périmètre : la sécurité du pipeline RAG et des modèles (voir `regulatory-rag-mlx-security`), la conformité juridique (voir `regulatory-compliance-eu`), le format du rapport final (voir `regulatory-docs-audit`).

## Méthodologie

### Étape 1 — Cartographier la surface d'attaque

Avant toute recommandation, produire l'inventaire réel à partir du dépôt :

1. Lister toutes les `APIRouter` et tous les décorateurs de route (`@app.get`, `@router.post`, …).
2. Pour chaque endpoint, relever : méthode, chemin, dépendances d'authentification, modèle d'entrée, modèle de sortie, effets de bord, coût (appel MLX ? écriture PostgreSQL ? publication Redis ?).
3. Repérer les endpoints **sans** dépendance d'auth. Ce sont les candidats prioritaires.
4. Repérer les endpoints coûteux (inférence, ingestion, réindexation Qdrant) : ce sont les cibles de déni de service.
5. Identifier les ports ouverts sur la machine : API (8000), Qdrant (6333/6334), Redis (6379), PostgreSQL (5432) — et vérifier que chacun est lié à `127.0.0.1` et non à `0.0.0.0`.

Rendu attendu : un tableau `endpoint | auth | rôle requis | modèle entrée | coût | exposition (loopback / interface web)`.

### Étape 2 — Appliquer la checklist de contrôle

Traiter chaque famille dans l'ordre. Pour chaque point : **statut** (conforme / non conforme / non applicable / à vérifier), **preuve** (fichier + ligne), **correctif**.

#### A. Authentification

- [ ] Aucun endpoint mutant ou coûteux n'est accessible sans authentification.
- [ ] Algorithme JWT explicitement contraint côté vérification (`algorithms=["RS256"]` ou `["EdDSA"]`). Jamais de liste vide, jamais `none` accepté, jamais l'algorithme lu depuis l'en-tête du jeton.
- [ ] Si HS256 est conservé : secret d'au moins 32 octets aléatoires, hors du code, rotation documentée.
- [ ] Vérification systématique de `exp`, `nbf`, `iat`, `iss`, `aud`. Ne pas désactiver `verify_aud`.
- [ ] Durée de vie courte des jetons d'accès (≤ 15 min) et refresh tokens rotatifs avec détection de réutilisation.
- [ ] Révocation possible : `jti` stocké dans une denylist Redis avec TTL aligné sur `exp`.
- [ ] Mots de passe hachés avec Argon2id ou bcrypt (coût calibré), jamais SHA-256 nu.
- [ ] Comparaisons de secrets en temps constant (`hmac.compare_digest`).
- [ ] Pas de jeton dans l'URL, ni dans les logs, ni dans les messages d'erreur.
- [ ] Protection contre le bourrage d'identifiants : limitation par compte **et** par IP, temporisation progressive.

#### B. Autorisation

- [ ] Modèle de rôles explicite (ex. `lecteur`, `analyste`, `valideur`, `admin`) documenté et testé.
- [ ] Refus par défaut : une route sans dépendance de rôle déclarée doit être considérée comme un défaut, pas comme un accès public.
- [ ] Contrôle au niveau objet : un utilisateur ne doit pas pouvoir lire un document, une file de validation ou un enregistrement d'audit d'un autre périmètre en changeant l'identifiant (IDOR).
- [ ] Les actions de validation humaine (approbation, rejet, escalade 72 h) exigent le rôle `valideur` et sont journalisées avec l'identité de l'auteur.
- [ ] Les endpoints d'administration (réindexation, purge de cache, rechargement de modèle) sont réservés à `admin` et jamais joignables hors de la machine.

#### C. CORS et en-têtes

- [ ] `allow_origins` est une liste explicite d'origines. **Jamais** `["*"]` combiné à `allow_credentials=True` — combinaison invalide et dangereuse.
- [ ] `allow_methods` et `allow_headers` restreints aux besoins réels.
- [ ] `TrustedHostMiddleware` configuré avec les hôtes attendus.
- [ ] HSTS, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `X-Frame-Options`/`frame-ancestors`, CSP restrictive sur l'interface web.
- [ ] `/docs`, `/redoc` et `/openapi.json` désactivés en production ou protégés par authentification (`docs_url=None` si `settings.env == "production"`).
- [ ] Bannière serveur et messages d'erreur ne divulguent ni versions ni chemins internes.

#### D. Validation des entrées

- [ ] Tous les corps de requête passent par un modèle Pydantic, jamais `dict` ou `Any` brut.
- [ ] `model_config = ConfigDict(extra="forbid")` sur les modèles d'entrée.
- [ ] Types contraints : `constr(max_length=…)`, `conint(ge=…, le=…)`, `Literal[…]` pour les énumérations.
- [ ] Longueur maximale imposée sur tout champ texte libre, en particulier la question utilisateur envoyée au RAG (sinon : saturation de contexte et OOM sur la machine).
- [ ] Pagination bornée : `limit` plafonné côté serveur, jamais dérivé directement du client.
- [ ] Téléversements : taille maximale, type MIME vérifié par lecture des octets d'en-tête et non par l'extension ou par `content_type` déclaré, nom de fichier régénéré côté serveur, stockage hors racine web, quota par utilisateur.
- [ ] PDF : garde contre les fichiers-bombes (nombre de pages, taille décompressée, temps d'extraction plafonné, extraction dans un sous-processus avec timeout).
- [ ] Dates réglementaires validées comme dates ISO et cohérentes (`date_debut <= date_fin`) avant d'atteindre la logique temporelle.

#### E. Injections

- [ ] PostgreSQL : requêtes paramétrées ou SQLAlchemy Core/ORM. Aucune concaténation ni f-string dans du SQL. Si `text()` est utilisé, uniquement avec des paramètres liés.
- [ ] Les noms de tables/colonnes dynamiques proviennent d'une liste blanche, jamais de l'entrée utilisateur.
- [ ] Qdrant : les filtres de métadonnées sont construits par un constructeur typé à partir de valeurs validées, jamais par assemblage de dictionnaires bruts issus du client.
- [ ] Redis : espace de noms des clés construit côté serveur (`ra2:{tenant}:{type}:{id}`), entrée utilisateur échappée ou hachée, jamais interpolée telle quelle dans une clé ou un nom de canal. Pas de `EVAL` avec du script assemblé dynamiquement.
- [ ] Chemins de fichiers (stockage JSON canonique, documents ingérés) : résolus via `Path(...).resolve()` puis vérifiés comme descendants du répertoire autorisé. Aucun identifiant de document n'est utilisé tel quel comme segment de chemin.
- [ ] Aucun appel `subprocess` avec `shell=True` sur des données externes ; utiliser une liste d'arguments.
- [ ] Désérialisation : jamais `pickle`, `yaml.load` non sûr, ou `eval` sur des données ingérées ou mises en cache.

#### F. SSRF et sécurité du Watcher

Le Watcher récupère des sources externes ; c'est le principal vecteur SSRF du système.

- [ ] Liste blanche de domaines sources (EUR-Lex, Légifrance, ANSSI, CNIL, …). Toute autre origine est refusée.
- [ ] Résolution DNS vérifiée : rejet des adresses privées, de bouclage, lien-local et métadonnées cloud, y compris après redirection.
- [ ] Redirections non suivies automatiquement, ou suivies avec revalidation complète à chaque saut et un maximum strict.
- [ ] Timeouts de connexion et de lecture obligatoires ; taille de réponse plafonnée.
- [ ] Vérification TLS jamais désactivée (`verify=False` interdit).
- [ ] Le contenu récupéré est traité comme **donnée non fiable** : pas d'exécution, pas de rendu HTML brut, assainissement avant indexation (voir `regulatory-rag-mlx-security`).

#### G. Disponibilité et coût

- [ ] Rate limiting par utilisateur et par IP, adossé à Redis, avec des plafonds différenciés : lecture bon marché vs. requête RAG vs. ingestion.
- [ ] Sémaphore de concurrence sur les appels d'inférence MLX, dimensionné pour les 24 Go partagés — le chargement d'un modèle et le service des requêtes se disputent la même mémoire.
- [ ] Timeout applicatif sur chaque appel de modèle, avec annulation propre et réponse 503/504 explicite.
- [ ] Taille maximale du corps de requête imposée au niveau du reverse proxy et de l'application.
- [ ] Aucune opération bloquante dans une route `async def` : l'inférence MLX, l'extraction PDF et les I/O fichiers vont dans un exécuteur dédié ou une file de travail.
- [ ] File d'attente bornée : rejet explicite plutôt que croissance illimitée de la mémoire.

#### H. Secrets, journalisation, erreurs

- [ ] Configuration via `pydantic-settings` + variables d'environnement ; `.env` exclu du dépôt et vérifié par un scan d'historique Git.
- [ ] Aucun secret, jeton, mot de passe ou clé API en dur, y compris dans les tests et les notebooks.
- [ ] Les logs n'exposent ni jetons, ni identifiants, ni contenu intégral des questions utilisateurs contenant des données personnelles.
- [ ] Gestionnaire d'exceptions global : réponse générique côté client, trace complète côté serveur, identifiant de corrélation partagé.
- [ ] `debug=False` et rechargement automatique désactivé en production.
- [ ] Journalisation structurée (JSON) avec identifiant de requête, utilisateur, route, durée, statut — reliable à la chaîne d'audit du projet.

#### I. Exposition des services

- [ ] Qdrant, Redis et PostgreSQL écoutent exclusivement sur `127.0.0.1`. Aucun bind sur `0.0.0.0`, aucune exception « c'est local de toute façon ».
- [ ] Qdrant : clé API activée, TLS si le trafic quitte la machine.
- [ ] Redis : `requirepass` ou ACL par service, `protected-mode` actif, commandes dangereuses désactivées ou renommées.
- [ ] PostgreSQL : utilisateur applicatif dédié avec privilèges minimaux, `scram-sha-256`, pas de compte superutilisateur pour l'application.
- [ ] Les appels entre modules ne sont pas implicitement fiables parce qu'ils sont locaux : tout processus de la machine peut atteindre un port sur `127.0.0.1`. Authentifier les accès aux services de données (clé Qdrant, `requirepass` Redis, utilisateur PostgreSQL dédié).

#### J. Chaîne d'approvisionnement

- [ ] Dépendances épinglées et fichier de verrouillage présent.
- [ ] `pip-audit` (ou équivalent) exécuté et sans vulnérabilité critique non traitée.
- [ ] Analyse statique de sécurité (`bandit`, `semgrep`) intégrée à la CI.
- [ ] Aucune dépendance introduisant un appel d'inférence distant (contrainte non négociable du projet).

### Étape 3 — Coter et hiérarchiser

Attribuer une sévérité à chaque constat :

| Sévérité | Définition opérationnelle | Délai cible |
|---|---|---|
| **Critique** | Contournement d'authentification, exécution de code, exfiltration de la base documentaire ou d'audit, secret exposé publiquement | Correction immédiate, avant toute mise en service |
| **Élevée** | Élévation de privilèges, IDOR, injection exploitable avec un compte valide, SSRF vers le réseau interne | ≤ 7 jours |
| **Moyenne** | Absence de rate limiting, CORS trop permissif, fuite d'informations dans les erreurs, en-têtes manquants | ≤ 30 jours |
| **Faible** | Durcissement recommandé sans exploitation directe démontrée | Backlog |

Hiérarchiser par (sévérité × exploitabilité réelle dans l'architecture), pas par ordre alphabétique des fichiers.

### Étape 4 — Corriger avec preuve

Pour chaque correctif proposé :

1. Montrer le code vulnérable **et** le code corrigé.
2. Fournir un test de régression qui échoue avant le correctif et passe après (pytest + `httpx.AsyncClient`).
3. Indiquer les effets de bord sur les autres machines et sur les contrats de service existants.
4. Ne jamais annoncer un test comme passant s'il n'a pas été exécuté.
5. Ne pas modifier le comportement fonctionnel dans le même commit qu'un correctif de sécurité, sauf si le comportement lui-même est la faille.

### Étape 5 — Vérifier

- Rejouer la suite de tests de sécurité complète.
- Vérifier que les tests temporels et de citation du projet ne régressent pas.
- Relister les endpoints et confirmer que le tableau de l'étape 1 est à jour.

## Exemples d'utilisation

**Audit complet avant mise en service**
> « Audite la sécurité de l'API FastAPI de `m4pro2` avant la mise en production interne. Produis le tableau des endpoints, la checklist renseignée avec preuves fichier/ligne, et la liste des constats critiques et élevés. »

**Revue ciblée d'un module**
> « Revois `api/routers/query.py` et `api/deps.py` : je veux savoir si un utilisateur `lecteur` peut déclencher une réindexation Qdrant ou lire la file de validation d'un autre périmètre. »

**Durcissement JWT**
> « Notre authentification utilise HS256 avec un secret dans `config.py`. Propose la migration vers RS256 avec révocation par `jti` dans Redis, refresh rotatif, et les tests associés. »

**Protection du Watcher**
> « Le Watcher accepte une URL de source en paramètre d'API. Évalue le risque SSRF et implémente la liste blanche de domaines avec validation post-résolution DNS et post-redirection. »

**Défense du budget d'inférence**
> « Les endpoints qui appellent MLX n'ont ni quota ni sémaphore. Propose une stratégie de rate limiting Redis différenciée par coût d'endpoint et un plafond de concurrence global à la machine. »

## Critères de succès

L'utilisation de la compétence est réussie si :

1. **Exhaustivité** — tous les endpoints du dépôt figurent dans l'inventaire, aucun n'est « oublié ».
2. **Traçabilité** — chaque constat cite un fichier et une ligne réels du dépôt, jamais une supposition.
3. **Absence de fabulation** — un point non vérifiable est marqué « à vérifier », pas déclaré conforme.
4. **Aucune faille critique ou élevée non traitée** ne subsiste sans décision explicite d'acceptation du risque, datée et motivée.
5. **Testabilité** — chaque correctif de sévérité critique ou élevée est accompagné d'un test de régression exécutable.
6. **Respect des invariants du projet** — aucun correctif n'introduit d'API d'inférence externe, ne déplace un service entre machines, ni ne casse la sémantique temporelle ou la chaîne d'audit.
7. **Rapport exploitable** — chaque constat comporte : identifiant, sévérité, composant, preuve, impact, correctif, effort estimé, responsable.

## Liens et références

- OWASP API Security Top 10 (2023) — <https://owasp.org/API-Security/>
- OWASP ASVS (Application Security Verification Standard) — <https://owasp.org/www-project-application-security-verification-standard/>
- OWASP Cheat Sheet Series (JWT, REST, CORS, SSRF, File Upload) — <https://cheatsheetseries.owasp.org/>
- RFC 8725 — JSON Web Token Best Current Practices — <https://www.rfc-editor.org/rfc/rfc8725>
- RFC 9700 — Best Current Practice for OAuth 2.0 Security — <https://www.rfc-editor.org/rfc/rfc9700>
- FastAPI — Security et dépendances — <https://fastapi.tiangolo.com/tutorial/security/>
- Pydantic v2 — validation et `extra="forbid"` — <https://docs.pydantic.dev/latest/>
- Qdrant — sécurité et authentification — <https://qdrant.tech/documentation/guides/security/>
- Redis — sécurité et ACL — <https://redis.io/docs/latest/operate/oss_and_stack/management/security/>
- ANSSI — Guide d'hygiène informatique et recommandations de sécurisation des sites web — <https://cyber.gouv.fr/publications>
- Outils : `pip-audit`, `bandit`, `semgrep`, `ruff`, `trufflehog`/`gitleaks` pour l'historique Git

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
