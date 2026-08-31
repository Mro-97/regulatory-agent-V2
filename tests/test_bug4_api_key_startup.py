"""
tests/test_bug4_api_key_startup.py — B4

L'API refusait déjà chaque requête protégée (503) lorsque `API_KEY` était
vide, mais rien ne signalait le défaut au boot : un déploiement avec `.env`
incomplet démarrait en apparence sans erreur. `valider_configuration_demarrage()`
détecte le cas au démarrage — `main` refuse alors le boot (`sys.exit(2)`).
"""

from __future__ import annotations


class TestValidationConfigDemarrage:
    def test_signale_api_key_vide(self, monkeypatch):  # noqa: ANN001, ANN201
        import main
        from config import cfg

        monkeypatch.setattr(cfg, "api_key", "")
        erreurs = main.valider_configuration_demarrage()
        assert erreurs, "API_KEY vide doit être signalée"
        assert any("API_KEY" in e for e in erreurs)

    def test_signale_api_key_blancs(self, monkeypatch):  # noqa: ANN001, ANN201
        import main
        from config import cfg

        monkeypatch.setattr(cfg, "api_key", "   ")
        erreurs = main.valider_configuration_demarrage()
        assert erreurs, "API_KEY = espaces doit être signalée"

    def test_accepte_api_key_valide(self, monkeypatch):  # noqa: ANN001, ANN201
        import main
        from config import cfg

        monkeypatch.setattr(cfg, "api_key", "a" * 32)
        erreurs = main.valider_configuration_demarrage()
        assert erreurs == []

    def test_refuse_api_key_placeholder(self, monkeypatch):  # noqa: ANN001, ANN201
        """Refuse la valeur placeholder de .env.example (non configuré)."""
        import main
        from config import cfg

        monkeypatch.setattr(cfg, "api_key", "remplacez-par-une-cle-longue-et-aleatoire")
        erreurs = main.valider_configuration_demarrage()
        assert erreurs, "API_KEY = placeholder doit être signalée"
        assert any("placeholder" in e for e in erreurs)

    def test_refuse_api_key_trop_courte(self, monkeypatch):  # noqa: ANN001, ANN201
        """Refuse une clé < 32 caractères (risque de bruteforce)."""
        import main
        from config import cfg

        monkeypatch.setattr(cfg, "api_key", "a" * 31)
        erreurs = main.valider_configuration_demarrage()
        assert erreurs, "API_KEY trop courte doit être signalée"
        assert any("trop courte" in e for e in erreurs)
