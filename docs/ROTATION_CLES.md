# Rotation des clés API — procédure

Répond au point **S-2** de la revue. Le code existe déjà : magasin de hachages
(`data/api_keys.json`) et `scripts/gerer_cles.py` (générer / lister / révoquer).
Ce qui manquait, c'était la **procédure** — c'est-à-dire comment tourner sans
couper le service, et ce que la révocation implique réellement.

## Le principe : deux temps, jamais un seul

Une rotation sûre ajoute **avant** de retirer :

1. générer la nouvelle clé et la distribuer ;
2. vérifier qu'elle fonctionne ;
3. **puis** révoquer l'ancienne.

L'ordre inverse crée une fenêtre où plus aucune clé valide n'existe : l'API
répond alors `503 Authentification non configurée` à tout le monde.

## Ce qu'il faut savoir avant de commencer

- **La révocation est immédiate.** Le magasin est rechargé dès que
  `data/api_keys.json` change (correctif `da6f808`). Il n'y a **aucun délai de
  grâce** : un client qui utilise encore l'ancienne clé est refusé à la seconde
  où elle est retirée. C'est voulu en incident, mais cela impose de valider la
  nouvelle clé **avant** de révoquer.
- **Révoquer une clé invalide les sessions ouvertes avec elle.** Le cookie de
  session porte la clé elle-même : les navigateurs déjà connectés avec
  l'ancienne clé devront ressaisir la nouvelle. À prévoir si la rotation a lieu
  en heures ouvrées.
- **Les clés ne sont jamais stockées en clair.** Seul leur SHA-256 est persisté.
  Une clé affichée une fois ne peut **pas** être retrouvée : si elle est perdue,
  il faut en générer une autre.
- **Le rôle est porté par chaque clé** (`user` < `validateur` < `admin`). Une
  nouvelle clé peut avoir un rôle différent de celle qu'elle remplace.

## Quand tourner

| Situation | Délai |
|---|---|
| **Compromission suspectée ou avérée** | **Immédiat**, sans attendre la fenêtre de maintenance |
| Départ d'une personne ayant eu accès | Dans la journée |
| Rotation préventive | Trimestrielle, ou selon ta politique |
| Avant une mise en production exposée | Avant l'ouverture |

## Procédure de rotation préventive

### 1. Voir l'existant

```bash
cd ~/regulatory-agent
venv/bin/python scripts/gerer_cles.py lister
```

Affiche label, rôle, préfixe de hash et date de création — jamais la clé.

### 2. Générer la nouvelle clé

```bash
venv/bin/python scripts/gerer_cles.py generer --role admin --label prod-2026-10
```

La clé s'affiche **une seule fois**. Copie-la immédiatement dans ton gestionnaire
de secrets. Elle n'est plus jamais récupérable.

### 3. Vérifier qu'elle fonctionne — AVANT de toucher à l'ancienne

```bash
CLE="rak_..."   # la nouvelle
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "X-API-Key: $CLE" http://127.0.0.1:8002/whoami
```

Un `200` signifie que la nouvelle clé est active. Un `401` signifie qu'il ne faut
**pas** continuer : l'ancienne est encore la seule qui marche.

À ce stade, **les deux clés fonctionnent** — c'est la fenêtre de bascule. Les
clients peuvent migrer sans interruption.

### 4. Déployer la nouvelle clé

Remplace la clé dans le client, le navigateur, ou le script concerné. Vérifie
que le nouveau client fonctionne pour de bon.

### 5. Révoquer l'ancienne

```bash
venv/bin/python scripts/gerer_cles.py revoquer --label prod-2026-09
```

La prise en compte est **immédiate, sans redémarrage**.

### 6. Contrôler

```bash
venv/bin/python scripts/gerer_cles.py lister           # l'ancienne a disparu
curl -s -o /dev/null -w "ancienne: %{http_code}\n" \
  -H "X-API-Key: <ancienne>" http://127.0.0.1:8002/whoami   # doit renvoyer 401
curl -s -o /dev/null -w "nouvelle: %{http_code}\n" \
  -H "X-API-Key: $CLE" http://127.0.0.1:8002/whoami          # doit renvoyer 200
```

## Procédure en cas de compromission

Ici, on **inverse l'ordre** : la priorité est de couper l'accès, pas d'éviter la
coupure de service.

```bash
# 1. Révoquer immédiatement la clé suspecte.
venv/bin/python scripts/gerer_cles.py revoquer --label <label-compromis>

# 2. Générer un remplacement.
venv/bin/python scripts/gerer_cles.py generer --role admin --label <label>-urgence

# 3. Redémarrer l'API n'est PAS nécessaire (prise en compte immédiate),
#    mais reste sans risque si tu veux en avoir la certitude.
```

Si tu ne connais pas le label, `lister` donne le préfixe de hash, et
`revoquer --hash <préfixe>` retire l'entrée correspondante.

**Après une compromission**, vérifie aussi `data/audit.jsonl` (ou la table
`audit_trail`) pour identifier ce qui a été consulté avec la clé compromise.

## Points de vigilance

- **Ne jamais commiter une clé.** Le préfixe `rak_` est reconnaissable dans un
  diff, comme `ghp_` chez GitHub.
- **Le fichier doit rester en `0600`.** Le boot refuse de démarrer en
  production si `data/api_keys.json` est lisible par le groupe ou par tous.
- **`testeur-1`** (rôle `user`, créée le 2026-09-07) est encore dans le magasin.
  À révoquer si elle ne sert plus.
- **Un seul magasin, deux canaux.** Les clés peuvent aussi venir de
  `API_KEYS_HACHEES` (variable d'environnement) ; `lister` ne montre que le
  fichier. En production, `API_KEY` / `API_KEYS` en clair sont **refusés** au
  démarrage.
