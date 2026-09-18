"""tests/test_cors_origines.py — validation des origines CORS au démarrage (O-3).

`CORS_ORIGINS=*` serait une régression de sécurité silencieuse : combiné au
cookie de session (que le navigateur envoie automatiquement), il autoriserait
toute origine tierce à porter l'authentification. Ces tests verrouillent le
refus au démarrage, dans l'esprit des autres invariants de `main.py`.
"""

from __future__ import annotations

import pytest

from config import cfg


def _erreurs(monkeypatch: pytest.MonkeyPatch, origines: str) -> list[str]:
    """Applique la règle CORS à `CORS_ORIGINS` donné."""
    import main

    monkeypatch.setattr(cfg, "cors_origins_str", origines)
    erreurs: list[str] = []
    main._erreur_cors_origines_invalides(erreurs)  # noqa: SLF001 — test unitaire
    return erreurs


class TestOriginesRefusees:
    def test_joker_refuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`*` est refusé : il ouvrirait le cookie de session à toute origine."""
        erreurs = _erreurs(monkeypatch, "*")
        assert erreurs
        assert "joker" in erreurs[0]

    def test_joker_parmi_origines_valides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Une seule entrée joker suffit à tout autoriser : elle est signalée."""
        erreurs = _erreurs(monkeypatch, "https://monsite.fr,*")
        assert len(erreurs) == 1
        assert "'*'" in erreurs[0]

    def test_origine_sans_schema_refusee(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`monsite.fr` n'est jamais envoyé par un navigateur : erreur silencieuse."""
        erreurs = _erreurs(monkeypatch, "monsite.fr")
        assert erreurs
        assert "scheme://" in erreurs[0]

    def test_schema_non_http_refuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un schéma exotique (`ftp://`) n'est pas une origine web."""
        assert _erreurs(monkeypatch, "ftp://monsite.fr")


class TestOriginesAcceptees:
    @pytest.mark.parametrize(
        "origine",
        [
            "http://localhost",
            "http://127.0.0.1:8002",
            "https://monsite.fr",
            "https://monsite.fr:8443",
            "http://localhost:8000,http://127.0.0.1:8002",
        ],
    )
    def test_origines_valides(
        self, monkeypatch: pytest.MonkeyPatch, origine: str
    ) -> None:
        assert _erreurs(monkeypatch, origine) == []

    def test_liste_vide_acceptee(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Aucune origine déclarée = aucun trafic navigateur cross-origin attendu."""
        assert _erreurs(monkeypatch, "") == []


class TestIntegrationDemarrage:
    def test_regle_branchee_dans_la_validation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La règle est bien appelée par `valider_configuration_demarrage`."""
        import main

        monkeypatch.setattr(cfg, "api_key", "a" * 32)
        monkeypatch.setattr(cfg, "cors_origins_str", "*")
        erreurs = main.valider_configuration_demarrage()
        assert any("CORS_ORIGINS" in e for e in erreurs)
