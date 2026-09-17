"""src/http_safety.py — garde-fous SSRF pour les clients HTTP sortants.

Préventif : une URL de source peut viser l'intérieur du réseau ou la
métadonnée cloud (169.254.169.254). Trois protections, dans cet ordre :

1. **schéma et port** — seuls `http`/`https` sur les ports 80/443 (ou
   explicitement autorisés) sont acceptés : sans ce contrôle, `file://` ou
   `gopher://` passeraient dès lors qu'ils portent un hostname ;
2. **résolution validée** — le hostname est résolu UNE fois et chaque IP
   retournée doit être globale. Une seule IP interne dans la liste fait
   refuser l'URL (round-robin et dual-stack hostiles) ;
3. **connexion sur l'IP validée** — `src/http_client.py` se connecte à l'IP
   que ce module a validée, en conservant le `Host` et le SNI d'origine.
   Sans cela `httpx` résolvait le nom une SECONDE fois : un domaine dont la
   réponse DNS change entre les deux requêtes (« DNS rebinding ») passait le
   contrôle puis était contacté sur une adresse interne.

Les redirections ne sont jamais suivies automatiquement : chaque saut
repasse par `valider_url` (cf. `src/http_client.py`).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

# Ports sortants autorisés. Le corpus réglementaire est servi en 80/443 ; un
# port supplémentaire doit être ajouté ici en connaissance de cause.
PORTS_AUTORISES: frozenset[int] = frozenset({80, 443})
_SCHEMAS_AUTORISES: frozenset[str] = frozenset({"http", "https"})


class UrlRefuseeError(ValueError):
    """L'URL sortante est refusée (schéma, port ou adresse non publique)."""

    def __init__(self, url: str, *, raison: str, ip: str | None = None) -> None:  # noqa: D107 — documenté par la classe
        detail = f"{raison} (IP {ip})" if ip else raison
        super().__init__(f"URL refusée — {detail} : {url}")
        self.url = url
        self.raison = raison
        self.ip = ip


# Nom historique conservé : le code et les tests existants l'importent.
UrlInterneRefuseeError = UrlRefuseeError


def _est_ip_interne(ip_str: str) -> bool:
    """True si `ip_str` n'est PAS une adresse publique routable.

    Le critère est négatif (`not is_global`) plutôt qu'une liste de plages :
    `is_private`/`is_loopback`/`is_link_local` laissent passer des cas réels —
    `0.0.0.0` et `0.1.2.3` (« this host »), `100.64.0.0/10` (CGNAT),
    `192.0.0.0/24`, `198.18.0.0/15`, les adresses IPv4-mappées et les plages
    réservées IETF. `is_global` les couvre toutes.
    """
    try:
        return not ipaddress.ip_address(ip_str).is_global
    except ValueError:
        # Valeur non parsable : elle ne doit jamais servir d'adresse cible.
        return True


def _extraire_hostname(url: str) -> str:
    """Extrait le hostname d'une URL, lève UrlRefuseeError si vide."""
    hostname = urlsplit(url).hostname
    if not hostname:
        raise UrlRefuseeError(url, raison="URL sans hostname")
    return hostname


def _verifier_schema_et_port(url: str) -> None:
    """Refuse tout schéma autre que http/https et tout port hors liste."""
    parties = urlsplit(url)
    if parties.scheme.lower() not in _SCHEMAS_AUTORISES:
        raise UrlRefuseeError(url, raison=f"schéma non autorisé ({parties.scheme})")
    try:
        port = parties.port
    except ValueError as exc:  # port non numérique ou hors bornes
        raise UrlRefuseeError(url, raison="port invalide") from exc
    if port is not None and port not in PORTS_AUTORISES:
        raise UrlRefuseeError(url, raison=f"port non autorisé ({port})")


def _resoudre_toutes_les_ips(hostname: str) -> list[str]:
    """Retourne toutes les IPs (v4 et v6) auxquelles `hostname` résout."""
    infos = socket.getaddrinfo(hostname, None)
    return list({str(info[4][0]) for info in infos})


def _est_ip_litterale(valeur: str) -> bool:
    """True si `valeur` est déjà une adresse IP (et non un nom à résoudre)."""
    try:
        ipaddress.ip_address(valeur)
    except ValueError:
        return False
    return True


def _resoudre_et_valider(hostname: str, url: str) -> list[str]:
    """Résout `hostname` et refuse l'URL si UNE des IP n'est pas publique.

    Une IP écrite en dur est validée directement, SANS résolution DNS (un
    `getaddrinfo("127.0.0.1")` renverrait l'adresse telle quelle, mais surtout
    un nom de domaine doit être résolu — et `ip_address("example.com")` lève,
    ce qui ne doit pas être confondu avec « adresse interne »).
    """
    if _est_ip_litterale(hostname):
        if _est_ip_interne(hostname):
            raise UrlRefuseeError(url, raison="adresse non publique", ip=hostname)
        return [hostname]
    try:
        ips = _resoudre_toutes_les_ips(hostname)
    except socket.gaierror as exc:
        # Hostname non résoluble : on laisse remonter une erreur claire plutôt
        # qu'un échec de connexion opaque.
        from src.errors import DnsIrresoluError

        raise DnsIrresoluError(hostname, str(exc)) from exc
    if not ips:
        from src.errors import DnsIrresoluError

        raise DnsIrresoluError(hostname, "aucune adresse retournée")
    for ip in ips:
        if _est_ip_interne(ip):
            raise UrlRefuseeError(url, raison="adresse non publique", ip=ip)
    return ips


def valider_url(url: str) -> list[str]:
    """Valide `url` et retourne les IPs publiques auxquelles se connecter.

    Args:
        url: URL sortante à valider.

    Returns:
        Les adresses validées (toutes publiques), dans l'ordre du résolveur.

    Raises:
        UrlRefuseeError: schéma, port ou adresse non publique.
        DnsIrresoluError: hostname non résoluble.
    """
    _verifier_schema_et_port(url)
    return _resoudre_et_valider(_extraire_hostname(url), url)
