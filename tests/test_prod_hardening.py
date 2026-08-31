"""tests/test_prod_hardening.py — durcissements pré-prod.

Couvre les fixes issus de la campagne de test pré-prod du 2026-08-31 :

- #2 CORS : la valeur par défaut inclut désormais les origines avec port
  (http://localhost:8000 / http://127.0.0.1:8000) qu'un vrai navigateur
  envoie systématiquement.
- #4 debug + docs : la combinaison DEBUG=true + EXPOSER_DOCS=true est
  refusée au démarrage (fuite de schémas et de tracebacks en prod).
- #7 Watcher : nouveau flag `watcher_actif` (désactivable en process
  séparé) et délai de warm-up avant le premier cycle.
"""

from __future__ import annotations


class TestCorsOriginsDefaut:
    """La conftest override `cors_origins_str` pour les tests, on inspecte donc
    la valeur par défaut du Field directement plutôt que `cfg.cors_origins`.
    """  # noqa: D205

    def _defaut(self) -> list[str]:
        from config import Parametres

        raw = Parametres.model_fields["cors_origins_str"].default
        return [o.strip() for o in str(raw).split(",") if o.strip()]

    def test_defaut_inclut_localhost_avec_port(self):  # noqa: ANN201
        """Un navigateur envoie l'Origin avec port — doit être accepté."""
        defaut = self._defaut()
        assert "http://localhost:8000" in defaut
        assert "http://127.0.0.1:8000" in defaut

    def test_defaut_conserve_origines_sans_port(self):  # noqa: ANN201
        """Rétro-compatibilité : origines sans port toujours acceptées."""
        defaut = self._defaut()
        assert "http://localhost" in defaut
        assert "http://127.0.0.1" in defaut


class TestValidationDebugDocs:
    def test_refuse_debug_et_docs_exposes(self, monkeypatch):  # noqa: ANN001, ANN201
        """DEBUG=true + EXPOSER_DOCS=true doit être refusé au boot."""
        import main
        from config import cfg

        monkeypatch.setattr(cfg, "api_key", "a" * 32)
        monkeypatch.setattr(cfg, "debug", True)
        monkeypatch.setattr(cfg, "exposer_docs", True)
        erreurs = main.valider_configuration_demarrage()
        assert erreurs, "DEBUG=true + EXPOSER_DOCS=true doit être signalé"
        assert any("DEBUG=true" in e and "EXPOSER_DOCS" in e for e in erreurs)

    def test_accepte_debug_seul(self, monkeypatch):  # noqa: ANN001, ANN201
        """DEBUG=true seul (docs désactivées) reste OK — usage dev."""
        import main
        from config import cfg

        monkeypatch.setattr(cfg, "api_key", "a" * 32)
        monkeypatch.setattr(cfg, "debug", True)
        monkeypatch.setattr(cfg, "exposer_docs", False)
        assert main.valider_configuration_demarrage() == []

    def test_accepte_docs_seuls(self, monkeypatch):  # noqa: ANN001, ANN201
        """EXPOSER_DOCS=true seul (debug off) reste OK — audit prod éphémère."""
        import main
        from config import cfg

        monkeypatch.setattr(cfg, "api_key", "a" * 32)
        monkeypatch.setattr(cfg, "debug", False)
        monkeypatch.setattr(cfg, "exposer_docs", True)
        assert main.valider_configuration_demarrage() == []


class TestWatcherFlags:
    def test_watcher_actif_defaut_true(self):  # noqa: ANN201
        """Défaut prudent : Watcher actif dans le process API."""
        from config import cfg

        assert cfg.watcher_actif is True

    def test_watcher_delai_demarrage_defaut_positif(self):  # noqa: ANN201
        """Délai de warm-up > 0 pour ne pas concurrencer le startup API."""
        from config import cfg

        assert cfg.watcher_delai_demarrage_secondes > 0

    def test_demarrer_watcher_court_circuite_si_inactif(
        self, monkeypatch
    ):  # noqa: ANN001, ANN201
        """`demarrer_watcher()` ne fait rien si `watcher_actif=false`."""
        import asyncio

        import main
        from config import cfg

        monkeypatch.setattr(cfg, "watcher_actif", False)
        # doit terminer sans importer src.watcher ni sleep
        asyncio.run(main.demarrer_watcher())
