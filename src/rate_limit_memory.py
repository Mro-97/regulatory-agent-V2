"""src/rate_limit_memory.py — Limiteur de débit en mémoire (repli et héritage).

Module FEUILLE : `api_security` (dépendance héritée `verifier_rate_limit`) et
`rate_limit_redis` (repli quand Redis est injoignable) ont besoin du même
limiteur. L'importer depuis `api_security` créait un cycle, car `api_security`
importe `rate_limit_redis` pour ré-exporter `get_rate_limiter`.

Les deux appelants passent par `limiteur_memoire()` : un seul seau en mémoire,
quel que soit le chemin — sinon une panne Redis changerait la sémantique du
quota.
"""

from __future__ import annotations

import time
from collections import defaultdict
from functools import lru_cache
from threading import Lock

from config import cfg


class LimiteurDebit:
    """Limiteur de débit en mémoire (fenêtre glissante).

    Deux appelants, deux découpages — c'est volontaire et documenté :

    - `RateLimiterRedis` (chemin nominal, `src/rate_limit_redis.py`) lui
      passe la clé composite `{empreinte_cle}:{ip}` : le repli mémoire
      applique alors EXACTEMENT le même découpage que Redis, y compris
      pendant une panne ;
    - `verifier_rate_limit` (dépendance héritée, non montée sur les routes)
      lui passe l'IP seule.

    Le suivi est plafonné à `max_cles` entrées distinctes ; les entrées dont
    tous les horodatages sont hors fenêtre sont purgées à chaque appel. Sans
    ce plafond, un port-scan ou un flood d'IP sources faisait croître
    `_horodatages` indéfiniment (fuite mémoire).

    Le limiteur est un objet mono-process : en multi-worker (gunicorn -w N),
    la limite effective est × N. `main.valider_configuration_demarrage()`
    signale ce cas au boot.
    """  # noqa: RUF002 — typographie française légitime dans la docstring

    def __init__(  # noqa: D107
        self,
        max_requetes: int,
        fenetre_secondes: int,
        max_cles: int = 10_000,
    ) -> None:
        self.max_requetes = max_requetes
        self.fenetre_secondes = fenetre_secondes
        self.max_cles = max_cles
        self._horodatages: defaultdict[str, list[float]] = defaultdict(list)
        self._verrou = Lock()

    def _purger(self, borne: float) -> None:
        """Retire les clés dont tous les horodatages sont hors fenêtre."""
        obsoletes = [
            k for k, ts in self._horodatages.items() if not ts or max(ts) <= borne
        ]
        for k in obsoletes:
            del self._horodatages[k]

    def autoriser(self, cle: str) -> bool:  # noqa: D102
        maintenant = time.monotonic()
        borne = maintenant - self.fenetre_secondes
        with self._verrou:
            # Purge opportuniste quand le dictionnaire dépasse le plafond.
            if len(self._horodatages) >= self.max_cles:
                self._purger(borne)
                if (
                    len(self._horodatages) >= self.max_cles
                    and cle not in self._horodatages
                ):
                    # Toujours saturé : on refuse la nouvelle clé plutôt que
                    # de laisser croître à l'infini.
                    return False
            valeurs = [t for t in self._horodatages[cle] if t > borne]
            self._horodatages[cle] = valeurs
            if len(valeurs) >= self.max_requetes:
                return False
            valeurs.append(maintenant)
            return True


@lru_cache(maxsize=1)
def limiteur_memoire() -> LimiteurDebit:
    """Limiteur mémoire partagé, construit depuis `cfg` au premier appel.

    Partagé par `api_security` (dépendance héritée) ET par le repli de
    `RateLimiterRedis` : les deux doivent compter dans le MÊME seau, sinon
    une panne Redis changerait la sémantique du quota.
    """
    return LimiteurDebit(
        max_requetes=cfg.rate_limit_max_requetes,
        fenetre_secondes=cfg.rate_limit_fenetre_secondes,
    )
