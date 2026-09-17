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

from pydantic import ValidationError

from src.errors import QueueBackendError, TaskNotFoundError
from src.models import (
    StatutValidation,
    TacheValidation,
    TypeFilePendante,
)
from src.schemas import (
    ReponseDecisionValidation,
    ReponseTachesPendantes,
)

if TYPE_CHECKING:
    from uuid import UUID

    from src.redis_client import ClientRedis

logger = logging.getLogger(__name__)

ClientFactory = Callable[[], Awaitable["ClientRedis"]]


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
        try:
            taches, par_file = await _lister_toutes_les_files(client)
        finally:
            # L1 : fermer le client même si la lecture a levé, sinon fuite
            # d'une connexion Redis à chaque /pending en erreur.
            await client.aclose()
    except Exception as exc:
        logger.exception("Redis inaccessible pour /pending")
        raise QueueBackendError(str(exc)) from exc
    return ReponseTachesPendantes(
        # L2 : `total` compte les tâches réellement renvoyées dans `taches`
        # (les entrées non parsables en sont exclues) ; `par_file` conserve
        # la taille brute de chaque liste Redis, utile au diagnostic.
        total=len(taches),
        par_file=par_file,
        taches=_trier_du_plus_recent(taches),
    )


def _trier_du_plus_recent(
    taches: list[TacheValidation],
) -> list[TacheValidation]:
    """Trie les tâches de la plus récente à la plus ancienne (ordre d'affichage).

    Les files sont alimentées par `LPUSH` (élément neuf en tête) mais lues
    par `LRANGE 0 -1` : l'ordre Redis va donc du plus récent au plus ancien,
    et la concaténation des quatre files n'a aucune raison d'être
    chronologique. L'UI (`rendrPendingPreview`) n'affiche que
    `taches.slice(0, 3)` : sans tri explicite l'opérateur voyait un mélange
    de tâches anciennes et récentes et pouvait manquer une alerte neuve.

    Le tri passe par `timestamp()` : comparer directement les `datetime`
    lèverait `TypeError` si une entrée ancienne (ou insérée à la main) était
    naïve alors que les autres sont en UTC, ce qui transformerait `/pending`
    en 500.
    """
    return sorted(taches, key=lambda t: t.horodatage_creation.timestamp(), reverse=True)


async def _lister_toutes_les_files(
    client: ClientRedis,
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
    client: ClientRedis,
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
                    try:
                        return TacheValidation(**donnees)
                    except (ValidationError, TypeError, ValueError) as exc:
                        # M1bis : une entrée partielle ne doit pas faire
                        # remonter une 503 sur le suivi — on l'ignore et on
                        # continue le parcours des files.
                        logger.warning(
                            "Tâche %s illisible/partielle ignorée : %s", cible, exc
                        )
                        continue
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
    try:
        trouvee = await _appliquer_decision_sur_files(
            client,
            tache_id,
            decision,
            commentaire,
            horodatage,
        )
    finally:
        # L1 : fermer le client même si l'application de la décision a levé.
        await client.aclose()
    if not trouvee:
        raise TaskNotFoundError(tache_id)
    return ReponseDecisionValidation(
        tache_id=tache_id,
        nouveau_statut=decision,
        horodatage_traitement=horodatage,
    )


async def _appliquer_decision_sur_files(
    client: ClientRedis,
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


def _serialiser_decision(
    donnees: dict[str, Any],
    decision: StatutValidation,
    commentaire: str | None,
    horodatage: datetime,
) -> str:
    """Écrit la décision dans `donnees` et la sérialise en JSON (synchrone)."""
    donnees["statut"] = decision.value
    donnees["horodatage_traitement"] = horodatage.isoformat()
    donnees["commentaire_validateur"] = commentaire
    return json.dumps(donnees, ensure_ascii=False)


async def _essayer_appliquer_a_cle(
    client: ClientRedis,
    nom_file: str,
    cle: str,
    tache_id: UUID,
    decision: StatutValidation,
    commentaire: str | None,
    horodatage: datetime,
) -> bool:
    """Retire une clé du pending et la pousse dans `traite_*` si son id matche.

    Deux validateurs (ou un double-clic / retry réseau) peuvent traiter la
    même tâche en même temps : les deux appels lisent la liste pending via
    `_appliquer_decision_sur_files` AVANT qu'aucun des deux n'écrive, et
    arrivent donc ici avec la même `cle` en main. `LREM` est atomique côté
    Redis — un seul des deux retirera réellement l'élément — mais seule sa
    valeur de retour permet de le savoir : sans la vérifier, l'appel perdant
    poussait quand même SA décision dans `traite_*`, dupliquant la tâche
    avec potentiellement deux statuts contradictoires (ex. approuvée ET
    rejetée) et renvoyant un faux succès (200) à l'appelant qui a perdu la
    course, au lieu d'un 404 « tâche introuvable / déjà traitée ».

    Le compte `0` de `LREM` (L3) retire TOUTES les occurrences de la clé :
    avec une entrée dupliquée dans la liste pending, un `count=1` laissait
    deux coureurs retirer chacun une copie et pousser chacun leur décision.
    """
    donnees = _charger_json_tache(cle)
    if donnees is None or str(donnees.get("tache_id")) != str(tache_id):
        return False
    retires = await cast("Awaitable[int]", client.lrem(nom_file, 0, cle))
    if retires == 0:
        # Course perdue : une autre requête a déjà retiré cette entrée entre
        # notre lecture et notre LREM. Ne pas pousser de décision dupliquée.
        return False
    await _pousser_decision(
        client, nom_file, donnees, decision, commentaire, horodatage
    )
    return True


async def _pousser_decision(
    client: ClientRedis,
    nom_file: str,
    donnees: dict[str, Any],
    decision: StatutValidation,
    commentaire: str | None,
    horodatage: datetime,
) -> None:
    """Pousse la décision dans `traite_*` (L4 : appelé juste après le LREM).

    `LREM` puis `LPUSH` ne sont pas atomiques : fenêtre de crash résiduelle
    assumée (ni script Lua ni pipeline transactionnel ne sont exposés par les
    doubles Redis des tests de course). On la réduit au minimum — aucun autre
    `await` entre les deux — et `_serialiser_decision` reste synchrone, donc
    n'ouvre aucun point de suspension supplémentaire.
    """
    await cast(
        "Awaitable[int]",
        client.lpush(
            f"traite_{nom_file}",
            _serialiser_decision(donnees, decision, commentaire, horodatage),
        ),
    )


def _charger_json_tache(cle: str) -> dict[str, Any] | None:
    """Parse une clé JSON de tâche ; retourne None (et log) si illisible.

    M1 : `json.loads` peut produire une liste, un nombre ou une chaîne ;
    seule une entrée objet (`dict`) est exploitable comme tâche.
    """
    try:
        donnees = json.loads(cle)
    except Exception as exc:  # noqa: BLE001 — cf. skill §8
        logger.warning("Erreur parsing tâche : %s", exc)
        return None
    if not isinstance(donnees, dict):
        logger.warning("Tâche JSON non-objet ignorée (%s)", type(donnees).__name__)
        return None
    return donnees


async def enregistrer_tache_redis(
    client_factory: ClientFactory,
    tache: TacheValidation,
) -> None:
    """Enregistre une tâche dans la file Redis appropriée.

    Raises:
        QueueBackendError: Redis injoignable ou écriture refusée. H1 : un
            échec silencieux faisait annoncer `en_attente_validation=true`
            sans qu'aucune tâche n'existe (fail-open).
    """
    try:
        client = await client_factory()
        try:
            await cast(
                "Awaitable[int]",
                client.lpush(
                    tache.type_file.value,
                    tache.model_dump_json(),
                ),
            )
        finally:
            # L1 : fermer le client même si l'écriture a levé.
            await client.aclose()
    except Exception as exc:
        logger.exception("Redis inaccessible, tâche non enregistrée")
        raise QueueBackendError(str(exc)) from exc
