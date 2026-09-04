# security/ — kit de test d'intrusion « boîte grise »

Deux scripts pour vérifier la posture de sécurité avant / après déploiement.
Ils ne détruisent rien (au plus 1 doc canari `ZZZ_PENTEST` via `/ingest` et
quelques lignes dans `data/feedback.jsonl`).

## Lancement

```bash
export B=http://127.0.0.1:8000          # en prod : https://ton-domaine
export KEY='<vraie_cle_API>'
export ORIGIN="$B"

bash security/pentest.sh   | tee /tmp/rapport_pentest.txt
python3 security/llm_abuse.py | tee /tmp/rapport_llm.txt

# charge soutenue (optionnel)
RUN_FLOOD=1 bash security/pentest.sh
```

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
