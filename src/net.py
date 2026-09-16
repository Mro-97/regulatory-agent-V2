"""src/net.py — Résolution de l'IP client réelle derrière un proxy de confiance.

`request.client.host` est le pair TCP direct : derrière un proxy inverse
c'est l'IP du proxy, pas celle de l'utilisateur. `X-Forwarded-For` /
`X-Real-IP` / `X-Forwarded-Proto` donnent la vraie valeur MAIS sont
falsifiables par n'importe quel client — on ne les lit donc que si le
pair direct figure dans `cfg.trusted_proxies` (liste d'IP ou de CIDR).

Sert au rate-limiting (clé de comptage), au journal d'accès (IP loggée)
et à la redirection HTTPS (schéma d'origine).
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

from config import cfg

if TYPE_CHECKING:
    from fastapi import Request

# Caractères interdits dans une valeur d'en-tête recopiée dans un log
# (anti-injection de fausses lignes). On neutralise CR/LF/TAB et les
# autres caractères de contrôle C0/C1.
_CTRL = dict.fromkeys([*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)])


def _pair_est_de_confiance(request: Request) -> bool:
    """True si le pair TCP direct figure dans `cfg.trusted_proxies`."""
    pair = request.client.host if request.client else None
    if not pair or not cfg.trusted_proxies:
        return False
    try:
        ip = ipaddress.ip_address(pair)
    except ValueError:
        return False
    for entree in cfg.trusted_proxies:
        try:
            if ip == ipaddress.ip_address(entree):
                return True
        except ValueError:
            try:
                if ip in ipaddress.ip_network(entree, strict=False):
                    return True
            except ValueError:
                continue
    return False


def _premier_xff(valeur: str) -> str:
    """Premier maillon (client d'origine) d'un `X-Forwarded-For`."""
    return valeur.split(",")[0].strip()


def _ip_valide(valeur: str | None) -> str | None:
    """`valeur` si c'est une IP littérale exploitable, sinon `None`.

    `X-Forwarded-For` / `X-Real-IP` alimentent la clé de rate-limit et le
    journal d'accès : une valeur non validée y laisse passer n'importe
    quelle chaîne (rotation de la clé de comptage, fausses lignes de log).
    Une entrée illisible est donc ignorée au profit du pair TCP réel plutôt
    que reprise telle quelle. Les valeurs préfixées (`for=`, `"…"`), les
    ports (`1.2.3.4:5678`) et les inconnues (`unknown`) sont écartés.
    """
    if not valeur:
        return None
    candidat = nettoyer_entete(valeur, taille_max=64).strip().strip('"')
    if not candidat or candidat.lower() in {"unknown", "inconnu", "-"}:
        return None
    try:
        return str(ipaddress.ip_address(candidat))
    except ValueError:
        return None


def ip_client(request: Request) -> str:
    """IP du client d'origine.

    Derrière un `trusted_proxy` : `X-Forwarded-For` (1er maillon valide)
    puis `X-Real-IP`. Sinon : le pair TCP direct (les en-têtes sont
    ignorés, car un client arbitraire peut les forger).
    """
    if _pair_est_de_confiance(request):
        xff = _ip_valide(_premier_xff(request.headers.get("X-Forwarded-For") or ""))
        if xff:
            return xff
        reel = _ip_valide(request.headers.get("X-Real-IP"))
        if reel:
            return reel
    return request.client.host if request.client else "inconnu"


def schema_origine(request: Request) -> str:
    """Schéma vu par le client d'origine (`http`/`https`).

    `X-Forwarded-Proto` n'est lu que derrière un `trusted_proxy`, et
    seulement s'il vaut `http` ou `https` — une valeur arbitraire ne doit
    pas pouvoir décider d'une redirection.
    """
    if _pair_est_de_confiance(request):
        proto = request.headers.get("X-Forwarded-Proto") or ""
        premier = proto.split(",")[0].strip().lower()
        if premier in {"http", "https"}:
            return premier
    return request.url.scheme


def nettoyer_entete(valeur: str | None, *, taille_max: int = 200) -> str:
    """Valeur d'en-tête sûre à recopier dans un log : sans caractère de contrôle."""
    if not valeur:
        return "-"
    return valeur.translate(_CTRL)[:taille_max] or "-"
