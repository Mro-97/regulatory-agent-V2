"""tests/test_validation_redis_robustesse.py — file HITL : robustesse Redis.

Couvre H1 (une panne Redis ne fait plus mentir `en_attente_validation`),
M1 (clé JSON non-objet), M1bis (entrée partielle tolérée) et L3 (LREM
retire toutes les occurrences d'une entrée dupliquée).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest
from src.errors import QueueBackendError
from src.models import (
    NiveauConfiance,
    RequeteQuestion,
    StatutValidation,
    TacheValidation,
    TypeFilePendante,
)
from src.orchestrator import Orchestrateur
from src.orchestrator_validation import (
    _charger_json_tache,
    _chercher_tache_par_id,
    _essayer_appliquer_a_cle,
    enregistrer_tache_redis,
)


class FauxRedisEnPanne:
    """Client Redis dont toute écriture échoue."""

    async def lpush(self, _nom: str, _valeur: str) -> int:
        """Échoue comme un Redis injoignable."""
        raise ConnectionError("indisponible")

    async def aclose(self) -> None:
        """No-op."""


class FauxRedisDuplique:
    """Listes Redis en mémoire, avec un `lrem` fidèle au comportement Redis."""

    def __init__(self, listes: dict[str, list[str]]) -> None:
        self.listes = listes

    async def lrange(self, nom: str, _debut: int, _fin: int) -> list[str]:
        """Retourne la liste demandée."""
        return list(self.listes.get(nom, []))

    async def lrem(self, nom: str, compte: int, valeur: str) -> int:
        """Retire `compte` occurrences (0 = toutes) et retourne le nb retiré."""
        liste = self.listes.setdefault(nom, [])
        restantes: list[str] = []
        retires = 0
        for element in liste:
            if element == valeur and (compte == 0 or retires < compte):
                retires += 1
                continue
            restantes.append(element)
        self.listes[nom] = restantes
        return retires

    async def lpush(self, nom: str, valeur: str) -> int:
        """Insère en tête et retourne la taille."""
        liste = self.listes.setdefault(nom, [])
        liste.insert(0, valeur)
        return len(liste)

    async def aclose(self) -> None:
        """No-op."""


def _tache_json(tid: uuid.UUID) -> str:
    return TacheValidation(
        tache_id=tid,
        type_file=TypeFilePendante.REPONSES,
        statut=StatutValidation.EN_ATTENTE,
        horodatage_creation=datetime.now(UTC),
    ).model_dump_json()


class TestChargementJsonTache:
    def test_non_objet_rejete(self) -> None:
        assert _charger_json_tache("[1, 2, 3]") is None
        assert _charger_json_tache('"texte"') is None
        assert _charger_json_tache("3") is None

    def test_json_invalide_rejete(self) -> None:
        assert _charger_json_tache("{pas du json") is None

    def test_objet_conserve(self) -> None:
        assert _charger_json_tache('{"tache_id": "abc"}') == {"tache_id": "abc"}


class TestChercherTacheParId:
    def test_entree_partielle_ignoree(self) -> None:
        tid = uuid.uuid4()
        partielle = json.dumps({"tache_id": str(tid)})
        faux = FauxRedisDuplique({"pending_responses": [partielle]})

        async def scenario() -> TacheValidation | None:
            return await _chercher_tache_par_id(faux, tid)

        assert asyncio.run(scenario()) is None

    def test_tache_valide_retrouvee(self) -> None:
        tid = uuid.uuid4()
        faux = FauxRedisDuplique({"pending_responses": [_tache_json(tid)]})

        async def scenario() -> TacheValidation | None:
            return await _chercher_tache_par_id(faux, tid)

        tache = asyncio.run(scenario())
        assert tache is not None
        assert tache.tache_id == tid


class TestEnregistrerTache:
    def test_panne_redis_leve_queue_backend_error(self) -> None:
        tache = TacheValidation(type_file=TypeFilePendante.REPONSES)

        async def factory() -> FauxRedisEnPanne:
            return FauxRedisEnPanne()

        with pytest.raises(QueueBackendError):
            asyncio.run(enregistrer_tache_redis(factory, tache))

    def test_soumission_effective_false_si_redis_ko(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """H1 : la réponse ne prétend pas qu'une validation est en attente."""
        orchestrateur = Orchestrateur(mode="mock")

        async def panne(_tache: TacheValidation) -> None:
            raise QueueBackendError("indisponible")

        monkeypatch.setattr(orchestrateur, "_enregistrer_tache_redis", panne)

        async def scenario() -> tuple[bool, uuid.UUID | None]:
            return await orchestrateur._soumettre_validation_si_besoin(
                RequeteQuestion(question="Obligations ?"),
                uuid.uuid4(),
                "réponse",
                NiveauConfiance.MOYEN,
                True,
            )

        assert asyncio.run(scenario()) == (False, None)

    def test_soumission_effective_true_si_redis_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orchestrateur = Orchestrateur(mode="mock")
        enregistrees: list[TacheValidation] = []

        async def ok(tache: TacheValidation) -> None:
            enregistrees.append(tache)

        monkeypatch.setattr(orchestrateur, "_enregistrer_tache_redis", ok)

        async def scenario() -> tuple[bool, uuid.UUID | None]:
            return await orchestrateur._soumettre_validation_si_besoin(
                RequeteQuestion(question="Obligations ?"),
                uuid.uuid4(),
                "réponse",
                NiveauConfiance.MOYEN,
                True,
            )

        soumis, tache_id = asyncio.run(scenario())
        assert soumis is True
        assert tache_id is not None
        assert enregistrees[0].tache_id == tache_id

    def test_pas_de_soumission_si_non_demande(self) -> None:
        orchestrateur = Orchestrateur(mode="mock")

        async def scenario() -> tuple[bool, uuid.UUID | None]:
            return await orchestrateur._soumettre_validation_si_besoin(
                RequeteQuestion(question="Obligations ?"),
                uuid.uuid4(),
                "réponse",
                NiveauConfiance.ELEVE,
                False,
            )

        assert asyncio.run(scenario()) == (False, None)


class TestLremToutesOccurrences:
    def test_entree_dupliquee_un_seul_gagnant(self) -> None:
        tid = uuid.uuid4()
        cle = _tache_json(tid)
        faux = FauxRedisDuplique({"pending_responses": [cle, cle]})
        horodatage = datetime.now(UTC)

        async def scenario() -> tuple[bool, bool]:
            gagnant = await _essayer_appliquer_a_cle(
                faux,
                "pending_responses",
                cle,
                tid,
                StatutValidation.APPROUVE,
                None,
                horodatage,
            )
            perdant = await _essayer_appliquer_a_cle(
                faux,
                "pending_responses",
                cle,
                tid,
                StatutValidation.REJETE,
                None,
                horodatage,
            )
            return gagnant, perdant

        gagnant, perdant = asyncio.run(scenario())
        assert gagnant is True
        assert perdant is False
        assert faux.listes["pending_responses"] == []
        assert len(faux.listes["traite_pending_responses"]) == 1
