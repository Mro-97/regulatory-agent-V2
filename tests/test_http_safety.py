"""tests/test_http_safety.py — garde-fou SSRF des clients HTTP sortants.

Couvre la classification d'adresses (critère `is_global`, plus large qu'une
liste de plages RFC1918), le contrôle schéma/port, la résolution DNS validée
et la valeur retournée (les IPs effectivement contactées).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.http_safety import (
    UrlInterneRefuseeError,
    UrlRefuseeError,
    _est_ip_interne,
    valider_url,
)


class TestEstIpInterne:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "10.0.0.5",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",  # metadata cloud
            "::1",
            "fe80::1",
            # Cas que l'ancien critère (is_private/is_loopback/is_link_local)
            # laissait passer :
            "0.0.0.0",  # noqa: S104 — adresse testée, pas un bind
            "0.1.2.3",  # réseau « this host »
            "100.64.0.1",  # CGNAT
            "192.0.0.1",  # IETF protocol assignments
            "198.18.0.1",  # bancs de test
            "::ffff:127.0.0.1",  # IPv4 mappée
        ],
    )
    def test_ips_internes_detectees(self, ip):  # noqa: ANN001, ANN201
        """Toute adresse non publique routable est refusée."""
        assert _est_ip_interne(ip) is True

    @pytest.mark.parametrize(
        "ip",
        [
            "8.8.8.8",
            "1.1.1.1",
            "142.250.185.46",  # google.com
            "2606:4700:4700::1111",  # cloudflare
        ],
    )
    def test_ips_publiques_acceptees(self, ip):  # noqa: ANN001, ANN201
        """Les IPs publiques ne sont pas signalées comme internes."""
        assert _est_ip_interne(ip) is False

    def test_valeur_non_ip_refusee(self):  # noqa: ANN201
        """Une valeur non parsable ne doit jamais servir d'adresse cible."""
        assert _est_ip_interne("pas-une-ip") is True


def _mock_getaddrinfo(ips: list[str]):  # noqa: ANN202
    """Fabrique un mock getaddrinfo qui renvoie les IPs listées."""

    def _fake(*_args: object, **_kwargs: object) -> list:
        # Format identique à socket.getaddrinfo : le 5e tuple contient (host, port).
        return [(0, 0, 0, "", (ip, 0)) for ip in ips]

    return _fake


class TestSchemaEtPort:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "gopher://example.com/",
            "ftp://example.com/fichier",
            "data:text/plain;base64,SGVsbG8=",
        ],
    )
    def test_schema_non_autorise(self, url):  # noqa: ANN001, ANN201
        """Seuls http/https sont acceptés : les autres schémas sont refusés."""
        with pytest.raises(UrlRefuseeError, match="schéma"):
            valider_url(url)

    @pytest.mark.parametrize("port", ["22", "6379", "9200", "8080"])
    def test_port_non_autorise(self, port):  # noqa: ANN001, ANN201
        """Un port hors 80/443 est refusé (pivot interne classique)."""
        with pytest.raises(UrlRefuseeError, match="port"):
            valider_url(f"http://example.com:{port}/")

    @pytest.mark.parametrize(
        "url",
        ["https://example.com/x", "http://example.com/", "https://example.com:443/x"],
    )
    def test_schema_et_port_autorises(self, url):  # noqa: ANN001, ANN201
        """http/https sur 80/443 (ou implicite) passent le contrôle de forme."""
        with patch("socket.getaddrinfo", _mock_getaddrinfo(["8.8.8.8"])):
            assert valider_url(url) == ["8.8.8.8"]


class TestResoudreUrlPubliqueOuLever:
    def test_url_publique_passe(self):  # noqa: ANN201
        """URL vers IP publique : aucune exception, IP retournée."""
        with patch("socket.getaddrinfo", _mock_getaddrinfo(["8.8.8.8"])):
            assert valider_url("https://example.com/x") == [
                "8.8.8.8"
            ]

    def test_url_localhost_refusee(self):  # noqa: ANN201
        """URL résolvant vers 127.0.0.1 → UrlInterneRefuseeError."""
        with (
            patch("socket.getaddrinfo", _mock_getaddrinfo(["127.0.0.1"])),
            pytest.raises(UrlInterneRefuseeError),
        ):
            valider_url("http://internal-service/")

    def test_url_metadata_cloud_refusee(self):  # noqa: ANN201
        """URL vers 169.254.169.254 (AWS/GCP metadata) refusée."""
        with (
            patch("socket.getaddrinfo", _mock_getaddrinfo(["169.254.169.254"])),
            pytest.raises(UrlInterneRefuseeError),
        ):
            valider_url("http://169.254.169.254/latest/meta-data/")

    def test_ip_litterale_interne_refusee_sans_dns(self):  # noqa: ANN201
        """Une IP interne écrite en dur est refusée SANS résolution DNS."""
        with pytest.raises(UrlInterneRefuseeError):
            valider_url("http://127.0.0.1:80/")

    def test_dns_dual_stack_avec_une_ip_interne_refuse(self):  # noqa: ANN201
        """Si le DNS renvoie plusieurs IPs et qu'UNE est interne → refus."""
        with (
            patch(
                "socket.getaddrinfo",
                _mock_getaddrinfo(["8.8.8.8", "10.0.0.5"]),
            ),
            pytest.raises(UrlInterneRefuseeError),
        ):
            valider_url("http://dual-stack.example/")

    def test_dns_sans_reponse_refuse(self):  # noqa: ANN201
        """Un résolveur qui ne retourne rien est un refus, pas un laissez-passer."""
        from src.errors import DnsIrresoluError

        with (
            patch("socket.getaddrinfo", _mock_getaddrinfo([])),
            pytest.raises(DnsIrresoluError),
        ):
            valider_url("http://vide.example/")

    def test_url_sans_schema_refusee(self):  # noqa: ANN201
        """Une chaîne sans schéma http(s) est refusée avant toute résolution."""
        with pytest.raises(UrlRefuseeError, match="schéma"):
            valider_url("not-a-url")

    def test_url_sans_hostname_refusee(self):  # noqa: ANN201
        """Une URL http sans hostname est refusée explicitement."""
        with pytest.raises(UrlRefuseeError, match="hostname"):
            valider_url("http:///chemin")
