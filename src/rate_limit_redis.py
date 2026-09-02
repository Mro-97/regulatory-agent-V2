"""src/rate_limit_redis.py — Rate limiter distribué via Redis (fallback mémoire).

`LimiteurDebit` (src/api_security.py) vit dans la mémoire du process : sur
un déploiement multi-worker (`gunicorn -w N`) chaque worker a son propre
compteur et la limite effective devient * N. Ce module déporte le comptage
dans Redis (`INCR` + `EXPIRE`) pour une limite partagée entre workers.

La clé est composite — `rl:{api_key}:{client_ip}` — afin que deux clients
derrière la même IP mais porteurs de clés API distinctes conservent chacun
leur quota (utile en multi-tenant à venir).

Si Redis est injoignable (timeout, réseau coupé, panne), `is_allowed`
retombe sur le limiteur mémoire : préférable à un fail-open (tout accepter)
qui ouvrirait la porte à un DoS pendant l'incident.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import cfg

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from src.api_security import LimiteurDebit

logger = logging.getLogger(__name__)


def get_redis_client() -> Redis:
    """Construit un client Redis asynchrone paramétré depuis `cfg`.

    Les timeouts sont volontairement courts : un Redis lent ne doit pas
    figer l'API, le fallback mémoire prend alors le relais.
    """
    import redis.asyncio as aioredis

    return aioredis.Redis(
        host=cfg.redis_host,
        port=cfg.redis_port,
        password=cfg.redis_password or None,
        db=cfg.redis_db,
        decode_responses=True,
        socket_timeout=0.5,
        socket_connect_timeout=0.5,
    )


def _composer_cle(api_key: str, client_ip: str) -> str:
    """Compose la clé Redis `rl:{api_key}:{client_ip}` avec valeurs de repli."""
    return f"rl:{api_key or 'no-api-key'}:{client_ip or 'unknown'}"


class RateLimiterRedis:
    """Rate limiter partagé entre workers via Redis, avec fallback mémoire."""

    def __init__(
        self,
        redis_client: Redis,
        max_requests: int = 30,
        window_seconds: int = 60,
        fallback: LimiteurDebit | None = None,
    ) -> None:
        """Mémorise le client, les seuils et le limiteur mémoire de secours."""
        self._redis = redis_client
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._fallback = fallback

    async def is_allowed(self, api_key: str, client_ip: str) -> bool:
        """`True` si le couple `{api_key}:{ip}` est sous quota.

        Incrémente le compteur Redis ; pose le TTL à la première requête de
        la fenêtre. En cas d'erreur Redis, délègue au limiteur mémoire.
        """
        cle = _composer_cle(api_key, client_ip)
        try:
            compteur: int = await self._redis.incr(cle)
            if compteur == 1:
                await self._redis.expire(cle, self._window_seconds)
        except Exception as exc:  # noqa: BLE001 — frontière externe : dégradation gracieuse
            logger.warning("Redis rate limit KO, bascule mémoire : %s", exc)
            return self._autoriser_via_fallback(cle)
        return compteur <= self._max_requests

    def _autoriser_via_fallback(self, cle: str) -> bool:
        """Délègue au limiteur mémoire (celui de `src.api_security` si non injecté)."""
        limiteur = self._fallback
        if limiteur is None:
            from src.api_security import _limiteur

            limiteur = _limiteur
        return limiteur.autoriser(cle)


_singleton: RateLimiterRedis | None = None


def get_rate_limiter() -> RateLimiterRedis:
    """Retourne le limiteur Redis partagé (créé au premier appel)."""
    global _singleton
    if _singleton is None:
        _singleton = RateLimiterRedis(
            get_redis_client(),
            max_requests=cfg.redis_rate_limit_max_requests,
            window_seconds=cfg.redis_rate_limit_window_seconds,
        )
    return _singleton


def reinitialiser_pour_tests() -> None:
    """Efface le singleton pour forcer sa reconstruction (tests uniquement)."""
    global _singleton
    _singleton = None
