"""tests/test_rate_limit_redis.py — Rate limiter Redis (comptage, TTL, fallback).

Hermétique : aucun serveur Redis requis. `FakeAsyncRedis` reproduit
`INCR` / `EXPIRE` avec une horloge injectable pour tester l'expiration
sans attendre 60 s réelles.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from src.api_security import LimiteurDebit
from src.rate_limit_redis import RateLimiterRedis, _composer_cle


class Horloge:
    """Horloge fictive avançable, appelable comme `time.monotonic`."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def avancer(self, secondes: float) -> None:
        self.t += secondes


class FakeAsyncRedis:
    """Sous-ensemble asynchrone de Redis : `incr` + `expire` avec TTL."""

    def __init__(self, horloge: Horloge) -> None:
        self._horloge = horloge
        self._compteurs: dict[str, int] = {}
        self._echeances: dict[str, float] = {}
        self.appels_expire: list[tuple[str, int]] = []

    def _purger_expires(self) -> None:
        maintenant = self._horloge()
        for cle, echeance in list(self._echeances.items()):
            if echeance <= maintenant:
                self._compteurs.pop(cle, None)
                self._echeances.pop(cle, None)

    async def incr(self, cle: str) -> int:
        self._purger_expires()
        self._compteurs[cle] = self._compteurs.get(cle, 0) + 1
        return self._compteurs[cle]

    async def expire(self, cle: str, secondes: int) -> bool:
        self.appels_expire.append((cle, secondes))
        self._echeances[cle] = self._horloge() + secondes
        return True


class RedisEnPanne:
    """Client Redis qui échoue systématiquement (simule une panne)."""

    async def incr(self, cle: str) -> int:  # noqa: ARG002
        raise ConnectionError("redis injoignable")  # noqa: TRY003

    async def expire(self, cle: str, secondes: int) -> bool:  # noqa: ARG002
        raise ConnectionError("redis injoignable")  # noqa: TRY003


def test_composer_cle_avec_valeurs_de_repli() -> None:
    assert _composer_cle("cle-a", "10.0.0.1") == "rl:cle-a:10.0.0.1"
    assert _composer_cle("", "") == "rl:no-api-key:unknown"


def test_comptage_30_autorisees_31e_bloquee() -> None:
    limiteur = RateLimiterRedis(
        FakeAsyncRedis(Horloge()), max_requests=30, window_seconds=60
    )

    async def scenario() -> tuple[list[bool], bool]:
        vagues = [await limiteur.is_allowed("cle-a", "10.0.0.1") for _ in range(30)]
        return vagues, await limiteur.is_allowed("cle-a", "10.0.0.1")

    autorisees, depassement = asyncio.run(scenario())
    assert all(autorisees)
    assert depassement is False


def test_quota_par_couple_api_key_ip() -> None:
    limiteur = RateLimiterRedis(
        FakeAsyncRedis(Horloge()), max_requests=2, window_seconds=60
    )

    async def scenario() -> tuple[bool, bool, bool, bool]:
        a1 = await limiteur.is_allowed("cle-a", "10.0.0.1")
        a2 = await limiteur.is_allowed("cle-a", "10.0.0.1")
        a3 = await limiteur.is_allowed("cle-a", "10.0.0.1")
        autre_cle = await limiteur.is_allowed("cle-b", "10.0.0.1")
        return a1, a2, a3, autre_cle

    a1, a2, a3, autre_cle = asyncio.run(scenario())
    assert (a1, a2, a3) == (True, True, False)
    assert autre_cle is True


def test_ttl_pose_une_seule_fois_puis_expiration_reinitialise() -> None:
    horloge = Horloge()
    faux = FakeAsyncRedis(horloge)
    limiteur = RateLimiterRedis(faux, max_requests=1, window_seconds=60)

    async def scenario() -> tuple[bool, bool, bool, int]:
        premier = await limiteur.is_allowed("cle-a", "10.0.0.1")
        second = await limiteur.is_allowed("cle-a", "10.0.0.1")
        expire_apres_fenetre_active = len(faux.appels_expire)
        horloge.avancer(61)
        apres_fenetre = await limiteur.is_allowed("cle-a", "10.0.0.1")
        return premier, second, apres_fenetre, expire_apres_fenetre_active

    premier, second, apres_fenetre, expire_pose_une_fois = asyncio.run(scenario())
    assert premier is True
    assert second is False
    assert apres_fenetre is True
    # TTL posé une seule fois pour la fenêtre (pas à la 2e requête)...
    assert expire_pose_une_fois == 1
    # ...puis reposé sur la nouvelle fenêtre après expiration.
    assert faux.appels_expire == [("rl:cle-a:10.0.0.1", 60)] * 2


def test_fallback_memoire_quand_redis_est_ko(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fallback = LimiteurDebit(max_requetes=2, fenetre_secondes=60)
    limiteur = RateLimiterRedis(
        RedisEnPanne(), max_requests=999, window_seconds=60, fallback=fallback
    )

    async def scenario() -> list[bool]:
        return [await limiteur.is_allowed("cle-a", "10.0.0.1") for _ in range(3)]

    with caplog.at_level(logging.WARNING, logger="src.rate_limit_redis"):
        resultats = asyncio.run(scenario())

    assert resultats == [True, True, False]
    assert any("bascule mémoire" in message for message in caplog.messages)
