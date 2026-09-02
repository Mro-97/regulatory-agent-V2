"""src/http_safety.py — Garde-fous SSRF pour les clients HTTP sortants.

Préventif contre le vecteur d'attaque #4 identifié à l'audit sécurité :
si un jour une URL de source devient contrôlable par l'utilisateur
(paramètre d'API, entrée admin), sans deny-list elle peut pointer vers :

- 127.0.0.0/8       (localhost, services internes)
- 10.0.0.0/8        (RFC1918 privé)
- 172.16.0.0/12     (RFC1918 privé)
- 192.168.0.0/16    (RFC1918 privé)
- 169.254.0.0/16    (link-local, incl. metadata cloud 169.254.169.254)
- ::1, fc00::/7, fe80::/10 (IPv6 équivalents)

Ce module résout le hostname et rejette l'URL si l'IP tombe dans ces
plages. Aucune I/O réseau sortante ne doit se faire sans passer par
`resoudre_url_publique_ou_lever()`.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UrlInterneRefuseeError(ValueError):
    """L'URL résout vers une IP interne (RFC1918 / loopback / link-local)."""

    def __init__(self, url: str, ip: str) -> None:  # noqa: D107
        super().__init__(f"URL refusée (IP interne {ip}) : {url}")
        self.url = url
        self.ip = ip


def _est_ip_interne(ip_str: str) -> bool:
    """True si `ip_str` (IPv4 ou IPv6) est dans une plage privée/loopback/link-local."""
    ip = ipaddress.ip_address(ip_str)
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast


def _extraire_hostname(url: str) -> str:
    """Extrait le hostname d'une URL, lève UrlSansHostnameError si vide."""
    from src.errors import UrlSansHostnameError

    hostname = urlparse(url).hostname
    if not hostname:
        raise UrlSansHostnameError(url)
    return hostname


def _resoudre_toutes_les_ips(hostname: str) -> list[str]:
    """Retourne toutes les IPs (v4 et v6) auxquelles `hostname` résout."""
    infos = socket.getaddrinfo(hostname, None)
    return list({info[4][0] for info in infos})


def resoudre_url_publique_ou_lever(url: str) -> None:
    """Résout `url` et lève UrlInterneRefuseeError si l'IP est interne.

    Vérifie **toutes** les IPs retournées par le DNS (dual-stack v4/v6,
    round-robin) — une seule IP privée dans la liste et l'URL est refusée
    (protection contre DNS rebinding partiel).
    """
    hostname = _extraire_hostname(url)
    try:
        ips = _resoudre_toutes_les_ips(hostname)
    except socket.gaierror as exc:
        # Hostname non résoluble = pas de risque SSRF immédiat, on laisse
        # httpx échouer normalement avec un message clair.
        from src.errors import DnsIrresoluError

        raise DnsIrresoluError(hostname, str(exc)) from exc
    for ip in ips:
        if _est_ip_interne(ip):
            raise UrlInterneRefuseeError(url, ip)
