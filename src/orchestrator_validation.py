"""src/orchestrator_validation.py — Gestion Redis de la file human-in-the-loop.

Extraite de src/orchestrator.py (§12 étape 6). Regroupe les trois I/O
Redis (lister, valider, enregistrer une tâche) sous forme de fonctions
module-level. L'`Orchestrateur` fournit son propre factory de client
Redis en paramètre — aucune dépendance sur la classe.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from src.models import (
    ReponseDecisionValidation,
    ReponseTachesPendantes,
    StatutValidation,
    TacheValidation,
    TypeFilePendante,
)

if TYPE_CHECKING:
    from uuid import UUID

    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

ClientFactory = Callable[[], Awaitable["aioredis.Redis"]]


async def lister_taches_pendantes(
    client_factory: ClientFactory,
) -> ReponseTachesPendantes:
    """Récupère les tâches en attente depuis Redis."""
    try:
        client = await client_factory()
        taches: list[TacheValidation] = []
        par_file: dict[str, int] = {}

        for file in TypeFilePendante:
            # `redis.asyncio.Redis.lrange` a la même signature générique
            # `Awaitable[list[Any]] | list[Any]` — cast au moment d'awaiter.
            cles = await cast(
                "Awaitable[list[Any]]",
                client.lrange(file.value, 0, -1),
            )
            par_file[file.value] = len(cles)
            for cle in cles:
                try:
                    taches.append(TacheValidation(**json.loads(cle)))
                except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
                    logger.warning("Tâche non parsable : %s", exc)

        await client.aclose()
        return ReponseTachesPendantes(
            total=sum(par_file.values()),
            par_file=par_file,
            taches=taches,
        )
    except Exception:
        logger.exception("Redis inaccessible")
        return ReponseTachesPendantes(total=0, par_file={}, taches=[])


async def valider_tache(
    client_factory: ClientFactory,
    tache_id: UUID,
    decision: StatutValidation,
    commentaire: str | None = None,
) -> ReponseDecisionValidation:
    """Applique une décision humaine à une tâche Redis."""
    horodatage = datetime.now(UTC)
    try:
        client = await client_factory()
        tache_trouvee = False
        for file in TypeFilePendante:
            cles = await cast(
                "Awaitable[list[Any]]",
                client.lrange(file.value, 0, -1),
            )
            for cle in cles:
                try:
                    donnees = json.loads(cle)
                    if str(donnees.get("tache_id")) == str(tache_id):
                        donnees["statut"] = decision.value
                        donnees["horodatage_traitement"] = horodatage.isoformat()
                        donnees["commentaire_validateur"] = commentaire
                        await cast(
                            "Awaitable[int]",
                            client.lrem(file.value, 1, cle),
                        )
                        await cast(
                            "Awaitable[int]",
                            client.lpush(
                                f"traite_{file.value}",
                                json.dumps(donnees, ensure_ascii=False),
                            ),
                        )
                        tache_trouvee = True
                        break
                except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
                    logger.warning("Erreur parsing tâche : %s", exc)
            if tache_trouvee:
                break

        await client.aclose()
        if not tache_trouvee:
            raise ValueError(f"Tâche introuvable : {tache_id}")  # noqa: TRY003, TRY301

        return ReponseDecisionValidation(
            tache_id=tache_id,
            nouveau_statut=decision,
            horodatage_traitement=horodatage,
        )
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Validation échouée : {exc}") from exc  # noqa: TRY003 — message ponctuel, taxonomie d'erreurs dédiée à traiter en §8 skill


async def enregistrer_tache_redis(
    client_factory: ClientFactory,
    tache: TacheValidation,
) -> None:
    """Enregistre une tâche dans la file Redis appropriée."""
    try:
        client = await client_factory()
        await cast(
            "Awaitable[int]",
            client.lpush(
                tache.type_file.value,
                tache.model_dump_json(),
            ),
        )
        await client.aclose()
    except Exception:
        logger.exception("Redis inaccessible, tâche non enregistrée")
