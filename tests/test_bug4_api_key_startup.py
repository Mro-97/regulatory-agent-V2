"""
tests/test_bug4_api_key_startup.py — B4

L'API refusait déjà chaque requête protégée (503) lorsque `API_KEY` était
vide, mais rien ne signalait le défaut au boot : un déploiement avec `.env`
incomplet démarrait en apparence sans erreur. `valider_configuration_demarrage()`
détecte le cas au démarrage — `main` refuse alors le boot (`sys.exit(2)`).
"""

from __future__ import annotations


class TestValidationConfigDemarrage:
    def test_signale_api_key_vide(self, monkeypatch):
        from config import cfg
        import main

        monkeypatch.setattr(cfg, "api_key", "")
        erreurs = main.valider_configuration_demarrage()
        assert erreurs, "API_KEY vide doit être signalée"
        assert any("API_KEY" in e for e in erreurs)

    def test_signale_api_key_blancs(self, monkeypatch):
        from config import cfg
        import main

        monkeypatch.setattr(cfg, "api_key", "   ")
        erreurs = main.valider_configuration_demarrage()
        assert erreurs, "API_KEY = espaces doit être signalée"

    def test_accepte_api_key_valide(self, monkeypatch):
        from config import cfg
        import main

        monkeypatch.setattr(cfg, "api_key", "clef-secrete-123")
        erreurs = main.valider_configuration_demarrage()
        assert erreurs == []
