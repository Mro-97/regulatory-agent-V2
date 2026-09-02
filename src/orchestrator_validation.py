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

from src.errors import QueueBackendError, TaskNotFoundError
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
    """Retourne les TacheValidation présentes dans les files Redis pendantes.

    Lève `QueueBackendError` si Redis est injoignable — un opérateur qui
    consulte `/pending` doit distinguer « aucune tâche » d'un backend HS
    (auparavant on renvoyait `total=0`, masquant la panne d'infra).
    """
    try:
        client = await client_factory()
        taches, par_file = await _lister_toutes_les_files(client)
        await client.aclose()
    except Exception as exc:
        logger.exception("Redis inaccessible pour /pending")
        raise QueueBackendError(str(exc)) from exc
    return ReponseTachesPendantes(
        total=sum(par_file.values()),
        par_file=par_file,
        taches=taches,
    )


async def _lister_toutes_les_files(
    client: aioredis.Redis,
) -> tuple[list[TacheValidation], dict[str, int]]:
    """Itère sur chaque `TypeFilePendante` et agrège les taches parsées."""
    taches: list[TacheValidation] = []
    par_file: dict[str, int] = {}
    for file in TypeFilePendante:
        cles = await cast("Awaitable[list[Any]]", client.lrange(file.value, 0, -1))
        par_file[file.value] = len(cles)
        for cle in cles:
            try:
                taches.append(TacheValidation(**json.loads(cle)))
            except Exception as exc:  # noqa: BLE001 — cf. skill §8
                logger.warning("Tâche non parsable : %s", exc)
    return taches, par_file


async def obtenir_tache(
    client_factory: ClientFactory,
    tache_id: UUID,
) -> TacheValidation | None:
    """Cherche une tâche par id dans les files pendantes ET traitées.

    Retourne `None` si introuvable ; lève `QueueBackendError` si Redis KO.
    Sert le suivi côté demandeur (`GET /tache/{id}`).
    """
    try:
        client = await client_factory()
        try:
            return await _chercher_tache_par_id(client, tache_id)
        finally:
            await client.aclose()
    except Exception as exc:
        logger.exception("Redis inaccessible pour le suivi de tâche")
        raise QueueBackendError(str(exc)) from exc


async def _chercher_tache_par_id(
    client: aioredis.Redis,
    tache_id: UUID,
) -> TacheValidation | None:
    """Parcourt `<file>` et `traite_<file>` de chaque `TypeFilePendante`."""
    cible = str(tache_id)
    for file in TypeFilePendante:
        for nom in (file.value, f"traite_{file.value}"):
            cles = await cast("Awaitable[list[Any]]", client.lrange(nom, 0, -1))
            for cle in cles:
                donnees = _charger_json_tache(cle)
                if donnees and str(donnees.get("tache_id")) == cible:
                    return TacheValidation(**donnees)
    return None


async def valider_tache(
    client_factory: ClientFactory,
    tache_id: UUID,
    decision: StatutValidation,
    commentaire: str | None = None,
) -> ReponseDecisionValidation:
    """Marque la tâche `tache_id` comme APPROUVE/REJETE et la déplace en Redis."""
    horodatage = datetime.now(UTC)
    try:
        return await _appliquer_et_repondre(
            client_factory,
            tache_id,
            decision,
            commentaire,
            horodatage,
        )
    except TaskNotFoundError:
        raise
    except Exception as exc:
        raise QueueBackendError(str(exc)) from exc


async def _appliquer_et_repondre(
    client_factory: ClientFactory,
    tache_id: UUID,
    decision: StatutValidation,
    commentaire: str | None,
    horodatage: datetime,
) -> ReponseDecisionValidation:
    """Applique la décision Redis puis retourne la ReponseDecisionValidation."""
    client = await client_factory()
    trouvee = await _appliquer_decision_sur_files(
        client,
        tache_id,
        decision,
        commentaire,
        horodatage,
    )
    await client.aclose()
    if not trouvee:
        raise TaskNotFoundError(tache_id)
    return ReponseDecisionValidation(
        tache_id=tache_id,
        nouveau_statut=decision,
        horodatage_traitement=horodatage,
    )


async def _appliquer_decision_sur_files(
    client: aioredis.Redis,
    tache_id: UUID,
    decision: StatutValidation,
    commentaire: str | None,
    horodatage: datetime,
) -> bool:
    """Cherche `tache_id` dans toutes les files ; déplace vers `traite_*` si trouvée."""
    for file in TypeFilePendante:
        cles = await cast("Awaitable[list[Any]]", client.lrange(file.value, 0, -1))
        for cle in cles:
            if await _essayer_appliquer_a_cle(
                client,
                file.value,
                cle,
                tache_id,
                decision,
                commentaire,
                horodatage,
            ):
                return True
    return False


async def _essayer_appliquer_a_cle(
    client: aioredis.Redis,
    nom_file: str,
    cle: str,
    tache_id: UUID,
    decision: StatutValidation,
    commentaire: str | None,
    horodatage: datetime,
) -> bool:
    """Retire une clé du pending et la pousse dans `traite_*` si son id matche."""
    donnees = _charger_json_tache(cle)
    if donnees is None or str(donnees.get("tache_id")) != str(tache_id):
        return False
    donnees["statut"] = decision.value
    donnees["horodatage_traitement"] = horodatage.isoformat()
    donnees["commentaire_validateur"] = commentaire
    await cast("Awaitable[int]", client.lrem(nom_file, 1, cle))
    await cast(
        "Awaitable[int]",
        client.lpush(f"traite_{nom_file}", json.dumps(donnees, ensure_ascii=False)),
    )
    return True


def _charger_json_tache(cle: str) -> dict[str, Any] | None:
    """Parse une clé JSON de tâche ; retourne None (et log) si illisible."""
    try:
        return cast("dict[str, Any]", json.loads(cle))
    except Exception as exc:  # noqa: BLE001 — cf. skill §8
        logger.warning("Erreur parsing tâche : %s", exc)
        return None


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
