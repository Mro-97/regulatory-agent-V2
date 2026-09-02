"""tests/test_http_safety.py — deny-list SSRF pour les clients HTTP sortants."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from src.http_safety import (
    UrlInterneRefuseeError,
    _est_ip_interne,
    resoudre_url_publique_ou_lever,
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
        ],
    )
    def test_ips_internes_detectees(self, ip):  # noqa: ANN001, ANN201
        """Toutes les plages RFC1918 / loopback / link-local sont refusées."""
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


def _mock_getaddrinfo(ips: list[str]):  # noqa: ANN202
    """Fabrique un mock getaddrinfo qui renvoie les IPs listées."""

    def _fake(*_args: object, **_kwargs: object) -> list:
        # Format identique à socket.getaddrinfo : le 5e tuple contient (host, port).
        return [(0, 0, 0, "", (ip, 0)) for ip in ips]

    return _fake


class TestResoudreUrlPubliqueOuLever:
    def test_url_publique_passe(self):  # noqa: ANN201
        """URL vers IP publique ne lève rien."""
        with patch("socket.getaddrinfo", _mock_getaddrinfo(["8.8.8.8"])):
            resoudre_url_publique_ou_lever("https://example.com/x")

    def test_url_localhost_refusee(self):  # noqa: ANN201
        """URL résolvant vers 127.0.0.1 → UrlInterneRefuseeError."""
        with (
            patch("socket.getaddrinfo", _mock_getaddrinfo(["127.0.0.1"])),
            pytest.raises(UrlInterneRefuseeError),
        ):
            resoudre_url_publique_ou_lever("http://internal-service/")

    def test_url_metadata_cloud_refusee(self):  # noqa: ANN201
        """URL vers 169.254.169.254 (AWS/GCP metadata) refusée."""
        with (
            patch("socket.getaddrinfo", _mock_getaddrinfo(["169.254.169.254"])),
            pytest.raises(UrlInterneRefuseeError),
        ):
            resoudre_url_publique_ou_lever("http://169.254.169.254/latest/meta-data/")

    def test_dns_dual_stack_avec_une_ip_interne_refuse(self):  # noqa: ANN201
        """Si le DNS renvoie plusieurs IPs et qu'UNE est interne → refus."""
        with (
            patch(
                "socket.getaddrinfo",
                _mock_getaddrinfo(["8.8.8.8", "10.0.0.5"]),
            ),
            pytest.raises(UrlInterneRefuseeError),
        ):
            resoudre_url_publique_ou_lever("http://dual-stack.example/")

    def test_url_sans_hostname_leve_valueerror(self):  # noqa: ANN201
        """URL malformée sans hostname = ValueError immédiate."""
        with pytest.raises(ValueError, match="hostname"):
            resoudre_url_publique_ou_lever("not-a-url")
