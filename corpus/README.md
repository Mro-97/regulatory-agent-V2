# corpus/ — textes réglementaires sources

Tous les textes exploités par l'ingestion (`scripts/ingest.py`), sous deux formes :

| Dossier / fichier | Rôle | Versionné git |
|---|---|---|
| `raw/` | originaux tels que téléchargés (PDF, HTML, XML, JSON) | **non** (`.gitignore`) — reproductible via `MANIFEST.json` |
| `json/` | format canonique `DocumentReglementaire`, prêt pour `ingest.py` | **oui** |
| `INDEX.md` | table lisible : id · source · URL officielle · dates · statut | oui |
| `MANIFEST.json` | pour chaque source : URL, sha256, taille, horodatage du téléchargement | oui |

`raw/` n'est pas commité (git resterait alourdi à vie par ~100 Mo de PDF).
`MANIFEST.json` + `scripts/corpus_sources.py` permettent de le reconstruire à
l'identique. Pour versionner quand même le brut : `git add -f corpus/raw/…`
ou configurer git-lfs.

## Workflow

```
# 1. déclarer / mettre à jour les sources
$EDITOR scripts/corpus_sources.py

# 2. télécharger + convertir (à lancer là où il y a du réseau, ex. m4pro2)
python scripts/corpus_fetch.py            # tout
python scripts/corpus_fetch.py --only RGPD_2016_679 NIS2_2022_2555
python scripts/corpus_fetch.py --raw-only # télécharge sans convertir

# 3. ingérer dans Qdrant
python scripts/ingest.py --json corpus/json/RGPD_2016_679.json
# ou en lot : boucle sur corpus/json/*.json
```

## Périmètre

Sources **ouvertes** uniquement : EUR-Lex, Légifrance, ANSSI, CNIL, ENISA,
NIST (publications publiques). **Exclu** : normes ISO/IEC (payantes, pas de
source légale gratuite) — seules des notes publiques peuvent y renvoyer.

Chaque `json/<id>.json` porte `metadonnees_supplementaires.corpus` avec
l'URL source, la date de récupération et le convertisseur utilisé.
