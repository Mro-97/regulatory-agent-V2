"""
tests/test_bug7_watcher_retry.py — B7

Avant le fix, `Watcher.verifier_url` renonçait au premier échec réseau.
Une source momentanément indisponible restait ignorée jusqu'au cycle
suivant (par défaut 6 h). `_fetch_avec_retry` applique désormais
`cfg.watcher_max_essais` tentatives avec backoff exponentiel, sauf sur
erreur 4xx (permanente, pas de retry).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from config import cfg
from src.models import SourceReglementaire
from src.watcher import Watcher


class _ClientMock:
    """Client HTTP simulé qui fait échouer les N premières requêtes."""

    def __init__(self, sequence: list):
        # sequence : liste d'items — soit une exception à lever, soit un
        # tuple (statut, corps) à retourner.
        self.sequence = list(sequence)
        self.appels: list[str] = []
        self.is_closed = False

    async def get(self, url: str):
        self.appels.append(url)
        if not self.sequence:
            raise RuntimeError("Séquence mock épuisée")
        prochain = self.sequence.pop(0)
        if isinstance(prochain, Exception):
            raise prochain
        statut, corps = prochain
        request = httpx.Request("GET", url)
        return httpx.Response(status_code=statut, text=corps, request=request)


def _watcher_avec_client(monkeypatch, tmp_path, sequence):
    from src import watcher as watcher_module

    monkeypatch.setattr(watcher_module, "CHEMIN_HASHES", tmp_path / "hashes.json")
    monkeypatch.setattr(cfg, "watcher_backoff_secondes", 0.0)
    w = Watcher()
    client = _ClientMock(sequence)

    async def _http_mock() -> _ClientMock:
        return client

    w._http = _http_mock  # type: ignore[method-assign]
    return w, client


class TestB7WatcherRetry:
    def test_reprise_apres_echec_reseau_transitoire(self, tmp_path, monkeypatch):
        """Deux échecs réseau puis succès → verifier_url doit atteindre la 3e."""
        monkeypatch.setattr(cfg, "watcher_max_essais", 3)
        w, client = _watcher_avec_client(
            monkeypatch,
            tmp_path,
            [
                httpx.ConnectError("boom1"),
                httpx.ConnectError("boom2"),
                (200, "<html>Nouveau contenu</html>"),
            ],
        )

        async def _run():
            # 1er passage : première indexation, pas d'alerte, hash enregistré.
            alerte = await w.verifier_url("https://ex/1", SourceReglementaire.EUR_LEX)
            assert alerte is None
            assert len(client.appels) == 3  # 2 échecs + 1 succès

        asyncio.run(_run())

    def test_5xx_reessaye_puis_echoue(self, tmp_path, monkeypatch):
        """Trois 5xx consécutifs → renonce, sans exception."""
        monkeypatch.setattr(cfg, "watcher_max_essais", 3)
        w, client = _watcher_avec_client(
            monkeypatch,
            tmp_path,
            [(503, ""), (502, ""), (500, "")],
        )

        async def _run():
            alerte = await w.verifier_url("https://ex/2", SourceReglementaire.EUR_LEX)
            assert alerte is None
            assert len(client.appels) == 3

        asyncio.run(_run())

    def test_4xx_pas_de_retry(self, tmp_path, monkeypatch):
        """404 est permanent — pas de retry."""
        monkeypatch.setattr(cfg, "watcher_max_essais", 5)
        w, client = _watcher_avec_client(
            monkeypatch,
            tmp_path,
            [(404, ""), (200, "ne devrait pas être atteint")],
        )

        async def _run():
            alerte = await w.verifier_url("https://ex/3", SourceReglementaire.CNIL)
            assert alerte is None
            assert len(client.appels) == 1  # pas de deuxième tentative

        asyncio.run(_run())

    def test_max_essais_1_conserve_comportement_ancien(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "watcher_max_essais", 1)
        w, client = _watcher_avec_client(
            monkeypatch,
            tmp_path,
            [httpx.ConnectError("no net"), (200, "peu importe")],
        )

        async def _run():
            alerte = await w.verifier_url("https://ex/4", SourceReglementaire.ANSSI)
            assert alerte is None
            assert len(client.appels) == 1

        asyncio.run(_run())
