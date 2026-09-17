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
from src.redis_client import nouveau_client

if TYPE_CHECKING:
    from src.api_security import LimiteurDebit
    from src.redis_client import ClientRedis

logger = logging.getLogger(__name__)


def get_redis_client() -> ClientRedis:
    """Construit un client Redis (maison) paramétré depuis `cfg`.

    Le délai reste court — un Redis lent ne doit pas figer l'API, le repli
    mémoire prend le relais — mais pas trop : à 0,5 s, un Redis momentanément
    chargé déclenchait des bascules alors qu'il allait répondre. Le délai est
    configurable (`REDIS_TIMEOUT_SECONDES`).
    """
    return nouveau_client(
        host=cfg.redis_host,
        port=cfg.redis_port,
        password=cfg.redis_password,
        db=cfg.redis_db,
        timeout_secondes=float(cfg.redis_timeout_secondes),
    )


def _composer_cle(api_key: str, client_ip: str) -> str:
    """Compose la clé Redis `rl:{api_key}:{client_ip}` avec valeurs de repli."""
    return f"rl:{api_key or 'no-api-key'}:{client_ip or 'unknown'}"


class RateLimiterRedis:
    """Rate limiter partagé entre workers via Redis, avec fallback mémoire."""

    def __init__(
        self,
        redis_client: ClientRedis,
        max_requests: int = 30,
        window_seconds: int = 60,
        fallback: LimiteurDebit | None = None,
    ) -> None:
        """Mémorise le client, les seuils et le limiteur mémoire de secours."""
        self._redis = redis_client
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._fallback = fallback
        # Bascule sur le repli mémoire : invisible dans la réponse HTTP, et
        # donc uniquement dans les logs. Ce compteur la rend mesurable
        # (`statut()`), une panne Redis lente changeant la portée du quota.
        self.bascule_memoire = 0

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
            self.bascule_memoire += 1
            logger.warning(
                "Redis rate limit KO (bascule mémoire n°%d) : %s",
                self.bascule_memoire,
                exc,
            )
            return self._autoriser_via_fallback(cle)
        return compteur <= self._max_requests

    def statut(self) -> dict[str, int]:
        """Diagnostic : nombre de bascules mémoire depuis le démarrage.

        `bascule_memoire` > 0 signifie que Redis n'a pas servi au moins une
        requête : le quota a alors été appliqué par le compteur local, dont la
        portée est le processus (et non le couple clé/IP partagé par Redis).
        """
        return {"bascule_memoire": self.bascule_memoire}

    def _autoriser_via_fallback(self, cle: str) -> bool:
        """Délègue au limiteur mémoire (celui de `src.api_security` si non injecté).

        `cle` est la clé composite `{empreinte_cle}:{ip}` déjà composée par
        `is_allowed` : le repli mémoire applique donc EXACTEMENT le même
        découpage que Redis. Auparavant le middleware ne transmettait que
        l'empreinte de clé, si bien qu'une panne Redis changeait
        silencieusement la sémantique du quota (tous les clients d'une même
        clé partageaient un seau, quelle que soit leur IP).
        """
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

