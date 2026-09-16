"""tests/test_security_headers.py — en-têtes de sécurité sur TOUTE réponse.

Deux régressions couvertes, toutes deux constatées en live sur l'API de
`m4pro2` :

- un `429` (et par construction un `413`/`411`) produit par un middleware
  plus EXTERNE que celui des en-têtes sortait sans CSP, sans `X-Frame-Options`,
  sans HSTS ni masquage du `Server` ;
- `ip_client` reprenait tel quel le premier maillon d'un `X-Forwarded-For`
  (chaîne arbitraire) au lieu de n'accepter qu'une IP littérale.
"""

from __future__ import annotations

from fastapi import Request
from src.api_security import _controler_taille_et_encoding
from src.net import ip_client, schema_origine
from src.rate_limit_middleware import _reponse_429
from src.security_headers import appliquer_entetes_securite

_ENTETES_ATTENDUS = (
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "strict-transport-security",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
    "server",
)


def _assert_entetes(reponse) -> None:  # noqa: ANN001
    for entete in _ENTETES_ATTENDUS:
        assert entete in reponse.headers, f"en-tête manquant : {entete}"
    assert reponse.headers["server"] == "regulatory-agent"
    # `unsafe-inline` reste toléré pour les STYLES (SVG inline du template) ;
    # ce qui compte est qu'il n'apparaisse jamais dans `script-src`.
    csp = reponse.headers["content-security-policy"]
    script_src = csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "unsafe-inline" not in script_src, f"script-src trop permissif : {csp}"


class TestReponsesCourtCircuitees:
    """Les réponses fabriquées par un middleware externe portent aussi les en-têtes."""

    def test_429_du_rate_limit(self) -> None:
        _assert_entetes(_reponse_429())

    def test_413_corps_trop_gros(self) -> None:
        # Appel direct de la fabrique : httpx (`TestClient`) recalcule
        # `Content-Length` et ne transmettrait pas une valeur mensongère.
        reponse = _controler_taille_et_encoding(
            _FausseRequete("POST", {"Content-Length": "999999999999"})
        )
        assert reponse is not None
        assert reponse.status_code == 413
        _assert_entetes(reponse)

    def test_411_transfer_encoding(self) -> None:
        reponse = _controler_taille_et_encoding(
            _FausseRequete("POST", {"Transfer-Encoding": "chunked"})
        )
        assert reponse is not None
        assert reponse.status_code == 411
        _assert_entetes(reponse)

    def test_fonction_idempotente(self) -> None:
        from starlette.responses import JSONResponse

        reponse = appliquer_entetes_securite(JSONResponse({"a": 1}))
        reponse2 = appliquer_entetes_securite(reponse)
        assert reponse2.headers["server"] == "regulatory-agent"


class _FausseRequete:
    """Requête minimale pour `_controler_taille_et_encoding` (method + headers)."""

    def __init__(self, method: str, entetes: dict[str, str]) -> None:
        self.method = method
        self.headers = entetes


class TestControleTailleEtEncoding:
    """La fabrique de refus ne doit pas dépendre du middleware d'en-têtes."""

    def test_content_length_non_numerique_ignore(self) -> None:
        assert (
            _controler_taille_et_encoding(
                _FausseRequete("POST", {"Content-Length": "abc"})
            )
            is None
        )

    def test_get_avec_transfer_encoding_accepte(self) -> None:
        """Seules les méthodes à corps sont concernées par le refus 411."""
        assert (
            _controler_taille_et_encoding(
                _FausseRequete("GET", {"Transfer-Encoding": "chunked"})
            )
            is None
        )

    def test_transfer_encoding_identity_accepte(self) -> None:
        assert (
            _controler_taille_et_encoding(
                _FausseRequete("POST", {"Transfer-Encoding": "identity"})
            )
            is None
        )


class TestIpClient:
    """`X-Forwarded-For` / `X-Real-IP` : lus derrière un proxy de confiance."""

    @staticmethod
    def _requete(entetes: dict[str, str], pair: str = "203.0.113.9") -> Request:
        portee = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in entetes.items()],
            "client": (pair, 12345),
            "scheme": "http",
            "server": ("127.0.0.1", 8002),
            "query_string": b"",
        }
        return Request(portee)

    def test_pair_non_fiable_ignore_les_entetes(self) -> None:
        requete = self._requete(
            {"X-Forwarded-For": "10.1.2.3", "X-Real-IP": "10.1.2.3"}
        )
        assert ip_client(requete) == "203.0.113.9"

    def test_pair_de_confiance_lit_le_premier_maillon_valide(self, monkeypatch) -> None:  # noqa: ANN001
        from config import cfg

        monkeypatch.setattr(cfg, "trusted_proxies_str", "203.0.113.9", raising=False)
        requete = self._requete({"X-Forwarded-For": "198.51.100.7, 203.0.113.9"})
        assert ip_client(requete) == "198.51.100.7"

    def test_valeur_non_ip_rejetee(self, monkeypatch) -> None:  # noqa: ANN001
        """Une chaîne arbitraire ne doit pas devenir la clé de rate-limit."""
        from config import cfg

        monkeypatch.setattr(cfg, "trusted_proxies_str", "203.0.113.9", raising=False)
        requete = self._requete({"X-Forwarded-For": "pas-une-ip; rm -rf /"})
        assert ip_client(requete) == "203.0.113.9"

    def test_x_real_ip_litteral_accepte(self, monkeypatch) -> None:  # noqa: ANN001
        from config import cfg

        monkeypatch.setattr(cfg, "trusted_proxies_str", "203.0.113.9", raising=False)
        requete = self._requete({"X-Real-IP": "198.51.100.42"})
        assert ip_client(requete) == "198.51.100.42"

    def test_schema_origine_refuse_une_valeur_arbitraire(self, monkeypatch) -> None:  # noqa: ANN001
        from config import cfg

        monkeypatch.setattr(cfg, "trusted_proxies_str", "203.0.113.9", raising=False)
        requete = self._requete({"X-Forwarded-Proto": "javascript"})
        assert schema_origine(requete) == "http"
        requete_https = self._requete({"X-Forwarded-Proto": "https"})
        assert schema_origine(requete_https) == "https"
