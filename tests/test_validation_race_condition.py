"""tests/test_validation_race_condition.py — course sur /approve vs /reject.

Deux validateurs (ou un double-clic / retry réseau) peuvent traiter la même
tâche en même temps : les deux appels lisent la liste `pending_*` via
`_appliquer_decision_sur_files` AVANT qu'aucun des deux n'ait écrit, donc
arrivent dans `_essayer_appliquer_a_cle` avec la même entrée en main. Seul
le retour de `LREM` (atomique côté Redis) permet de savoir qui a gagné.

Avant correctif : l'appel perdant ignorait ce retour et poussait quand même
sa décision dans `traite_*` → tâche dupliquée avec deux statuts
potentiellement contradictoires, et 200 (faux succès) au lieu d'un 404.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest

from src.models import StatutValidation, TacheValidation, TypeFilePendante
from src.orchestrator_validation import _essayer_appliquer_a_cle, valider_tache


class FauxRedisMutable:
    """lrange/lrem/lpush sur des listes JSON en mémoire (simule Redis)."""

    def __init__(self, listes: dict[str, list[str]]) -> None:
        self.listes = listes

    async def lrange(self, nom: str, _a: int, _b: int) -> list[str]:
        return list(self.listes.get(nom, []))

    async def lrem(self, nom: str, _count: int, valeur: str) -> int:
        liste = self.listes.setdefault(nom, [])
        if valeur in liste:
            liste.remove(valeur)
            return 1
        return 0

    async def lpush(self, nom: str, valeur: str) -> int:
        self.listes.setdefault(nom, []).insert(0, valeur)
        return 1

    async def aclose(self) -> None:
        return None


def _tache_json(tid: uuid.UUID) -> str:
    return TacheValidation(
        tache_id=tid,
        type_file=TypeFilePendante.REPONSES,
        statut=StatutValidation.EN_ATTENTE,
        horodatage_creation=datetime.now(UTC),
    ).model_dump_json()


def test_essayer_appliquer_a_cle_course_perdue_ne_duplique_pas() -> None:
    """Deux appels concurrents sur la même `cle` : le second doit échouer proprement."""
    tid = uuid.uuid4()
    cle = _tache_json(tid)
    faux = FauxRedisMutable({"pending_responses": [cle]})

    async def scenario() -> tuple[bool, bool]:
        gagnant = await _essayer_appliquer_a_cle(
            faux,
            "pending_responses",
            cle,
            tid,
            StatutValidation.APPROUVE,
            None,
            datetime.now(UTC),
        )
        # Le perdant arrive avec la MÊME `cle` (lue avant que le gagnant
        # n'écrive) — exactement le scénario d'une vraie course.
        perdant = await _essayer_appliquer_a_cle(
            faux,
            "pending_responses",
            cle,
            tid,
            StatutValidation.REJETE,
            None,
            datetime.now(UTC),
        )
        return gagnant, perdant

    gagnant, perdant = asyncio.run(scenario())

    assert gagnant is True
    assert perdant is False, "le perdant de la course ne doit pas pousser sa décision"
    assert faux.listes["pending_responses"] == []
    traitees = faux.listes["traite_pending_responses"]
    assert len(traitees) == 1, "aucune tâche dupliquée dans traite_*"
    assert json.loads(traitees[0])["statut"] == StatutValidation.APPROUVE.value


def test_valider_tache_perdant_de_la_course_leve_task_not_found() -> None:
    """Perdant de la course : TaskNotFoundError (→ 404), pas un faux succès."""
    from src.errors import TaskNotFoundError

    tid = uuid.uuid4()
    cle = _tache_json(tid)
    faux = FauxRedisMutable({"pending_responses": [cle]})

    async def factory() -> FauxRedisMutable:
        return faux

    async def scenario() -> None:
        # Le 1er appel gagne et déplace la tâche.
        await valider_tache(factory, tid, StatutValidation.APPROUVE)
        # Un 2e appel réel (retry / double-clic) voit la liste déjà vidée :
        # comportement déjà correct AVANT le fix (cas non concurrent), on le
        # garde en couverture de non-régression.
        with pytest.raises(TaskNotFoundError):
            await valider_tache(factory, tid, StatutValidation.REJETE)

    asyncio.run(scenario())
