# security/ — kit de test d'intrusion « boîte grise »

Deux scripts pour vérifier la posture de sécurité avant / après déploiement.
Ils ne détruisent rien (au plus 1 doc canari `ZZZ_PENTEST` via `/ingest` et
quelques lignes dans `data/feedback.jsonl`).

## Lancement

```bash
export B=http://127.0.0.1:8002          # port réel (cf. .env API_PORT) ; prod : https://ton-domaine
export BASE=$B
export ORIGIN=$B                        # doit matcher une valeur de CORS_ORIGINS
export KEY=$(grep -E '^API_KEY=' .env | cut -d= -f2-)

bash security/pentest.sh   | tee /tmp/rapport_pentest.txt
venv/bin/python security/llm_abuse.py | tee /tmp/rapport_llm.txt

# charge soutenue (optionnel)
RUN_FLOOD=1 bash security/pentest.sh
```

`pentest.sh` fait un **préflight** : si `KEY` n'authentifie pas, il s'arrête
(sinon toutes les sections authentifiées renvoient 401 et leurs verdicts
sont faux). Compter ~6 min (3 pauses de 60 s pour vider la fenêtre du
rate-limiter).

## Lecture

- `[PASS]` sûr · `[FAIL]` vulnérable · `[REVIEW]` dépend du déploiement.
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
