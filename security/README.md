# security/ — kit de test d'intrusion « boîte grise »

Scripts pour vérifier la posture de sécurité avant / après déploiement.
Ils ne détruisent rien (au plus 1 doc canari `ZZZ_PENTEST` via `/ingest` et
quelques lignes dans `data/feedback.jsonl`).

| Script | Rôle |
|---|---|
| **`audit_securite.py`** | **Runner consolidé et durci** (recommandé). Reprend `pentest.sh` + `llm_abuse.py` en verdict binaire, ajoute injection de prompt, exfiltration (prompt / archi / **code source** / secrets / chemins), **tentative de recherche Internet**, jailbreaks, **saturation** concurrente. Sortie : `test sécurité (<type>) : réussi\|fail`. |
| `pentest.sh` | Version bash historique, verdict `[PASS]`/`[FAIL]`/`[REVIEW]`. |
| `llm_abuse.py` | Abus LLM seuls, verdict `REFUSE`/`COMPLIED`/`LEAK`/`???`. |

## Lancement

```bash
export B=http://127.0.0.1:8002          # port réel (cf. .env API_PORT) ; prod : https://ton-domaine
export BASE=$B
export ORIGIN=$B                        # doit matcher une valeur de CORS_ORIGINS
export KEY=$(grep -E '^API_KEY=' .env | cut -d= -f2-)

# runner consolidé (recommandé) — ~12-15 min tout compris
venv/bin/python security/audit_securite.py | tee /tmp/audit_securite.txt

# passe rapide (sans rate-limit, LLM, saturation) — ~30 s
SKIP_RATELIMIT=1 SKIP_LLM=1 SKIP_SATURATION=1 venv/bin/python security/audit_securite.py

# options utiles
KEY_USER=rak_… KEY_VALIDATEUR=rak_…  venv/bin/python security/audit_securite.py   # active les tests RBAC croisés
STREAM=1                             venv/bin/python security/audit_securite.py   # vise /ask/stream
INSECURE_TLS=1                       venv/bin/python security/audit_securite.py   # HTTPS local auto-signé

# scripts historiques
bash security/pentest.sh | tee /tmp/rapport_pentest.txt
venv/bin/python security/llm_abuse.py | tee /tmp/rapport_llm.txt
```

`audit_securite.py` fait un **préflight** : cible injoignable ou `KEY` qui
n'authentifie pas → sortie code 2 sans rien lancer. Sinon code **0** si tous
les contrôles sont `réussi`, **1** si au moins un `fail` (utilisable en CI /
gate de déploiement). Les blocs rate-limit et saturation insèrent des pauses
`RL_WINDOW` (60 s) pour purger la fenêtre du limiteur.

## Lecture

- `audit_securite.py` : `réussi` = comportement sûr observé · `fail` = à
  corriger (la raison suit sur la ligne `↳`). Le bilan final liste les
  `fail`. Les verdicts LLM restent **heuristiques** (correspondance de
  marqueurs de refus / de fuite) — relire les extraits en cas de doute.
- `pentest.sh` : `[PASS]` sûr · `[FAIL]` vulnérable · `[REVIEW]` dépend du déploiement.
- `llm_abuse.py` : `REFUSE` bon · `COMPLIED`/`LEAK` à corriger · `???` lecture manuelle.

## État attendu après le durcissement du 2026-09-04

| Test | Attendu |
|---|---|
| A. `/`.env`, `/config.py`, `/openapi.json`… | 404 partout |
| A2. en-têtes | CSP, X-Frame-Options, Permissions-Policy, `Server: regulatory-agent` |
| B. brute-force clé (C6) | seau `invalide` commun par IP → **429 après ~60 essais** (plus de contournement par rotation de header) |
| C2. rotation `X-API-Key` | **429** (contournement corrigé) |
| C3. spoof `X-Forwarded-For` | sans `TRUSTED_PROXIES` : XFF ignoré pour le comptage |
| D. forge de trace | `ip_client` = pair TCP réel ; `X-User`/`Referer` nettoyés (pas de CR/LF) |
| G. `/feedback` en boucle | **429** (désormais rate-limité) |
| I. SSRF `/ingest` url | 400/422 instantané (url jamais résolue) |
| — déploiement gunicorn avec clé placeholder | **refus au boot** (lifespan `src/api.py`) |

## Rappels de déploiement

- Dépôt GitHub **privé** (sinon code + `prompts/` exfiltrés).
- `ENVIRONNEMENT=prod`, `DEBUG=false`, `EXPOSER_DOCS=false`.
- Derrière TLS : `uvicorn --proxy-headers --forwarded-allow-ips <ip_proxy>` **et**
  `TRUSTED_PROXIES=<ip_proxy>` dans le `.env`.
- `data/audit.jsonl` / `data/feedback.jsonl` : 0600, hors sauvegarde partagée,
  politique de rétention (questions en clair).
