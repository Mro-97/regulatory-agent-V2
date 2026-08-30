"""src/audit.py — Audit trail de Regulatory Agent V2
==================================================

Persistance des enregistrements d'audit dans PostgreSQL (Mac C).
Chaînage SHA-256 pour détecter toute altération de l'historique.

En phase 1 (développement mono-machine) : log structuré + fichier JSONL local.
En phase 2 (déploiement) : INSERT PostgreSQL via asyncpg.

Pipeline d'audit :
  EnregistrementAudit → calculer_hash() → chaîner avec hash précédent
  → persister (JSONL local + PostgreSQL si disponible)

Dépendances : asyncpg (optionnel), stdlib uniquement pour le fallback.
"""  # noqa: D205, D415

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from config import cfg

from src.models import EnregistrementAudit

logger = logging.getLogger(__name__)

# Chemin du fichier JSONL local (fallback si PostgreSQL indisponible)
CHEMIN_AUDIT_LOCAL = Path(cfg.audit_local_path)

# Hash du dernier enregistrement — maintenu en mémoire pour le chaînage
_hash_precedent: str | None = None
_hash_lock = asyncio.Lock()


# Schéma SQL extrait dans src/audit_schema.py (§12 étape 6).
from src.audit_schema import SQL_CREATE_TABLE  # noqa: E402

_SQL_INSERT_AUDIT = """
INSERT INTO audit_trail
    (request_id, horodatage, user_query, date_contexte,
     documents, agents, reponse, niveau_confiance,
     validation_humaine, hash_precedent, hash_courant)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
ON CONFLICT (hash_courant) DO NOTHING
"""


def _valeurs_insert_audit(audit: EnregistrementAudit) -> tuple[Any, ...]:
    """Sérialise un EnregistrementAudit en n-uplet pour l'INSERT PostgreSQL."""
    return (
        audit.request_id,
        audit.horodatage,
        audit.user_query,
        audit.date_contexte,
        json.dumps([str(d) for d in audit.documents_recuperes]),
        json.dumps([a.model_dump(mode="json") for a in audit.agents_executes]),
        audit.reponse_finale,
        audit.niveau_confiance.value,
        audit.necessite_validation_humaine,
        audit.hash_precedent,
        audit.hash_courant,
    )


def _bilan_integrite_vide() -> dict[str, int | list[dict[str, object]]]:
    """Bilan vide (retourné quand le fichier JSONL n'existe pas)."""
    return {"total": 0, "valides": 0, "invalides": 0, "erreurs": []}


def _lire_fenetre_audit(limite: int) -> tuple[str | None, list[str]]:
    """Retourne (hash d'ancrage, fenêtre des `limite` dernières lignes).

    Le hash d'ancrage est celui de la ligne AVANT la fenêtre : sans lui,
    un bloc auto-cohérent injecté en tête de fenêtre passerait inaperçu.
    """
    toutes = CHEMIN_AUDIT_LOCAL.read_text(encoding="utf-8").strip().splitlines()
    fenetre = toutes[-limite:]
    if len(toutes) <= limite:
        return None, fenetre
    try:
        ancre = json.loads(toutes[-limite - 1]).get("hash_courant")
    except Exception:  # noqa: BLE001 — frontière externe : dégradation gracieuse, cf. skill §8
        ancre = None
    return ancre, fenetre


def _verifier_lignes_audit(
    lignes: list[str], hash_ancre: str | None
) -> dict[str, int | list[dict[str, object]]]:
    """Itère sur les lignes JSONL, compte valides/invalides, collecte les erreurs."""
    etat = _EtatVerification(hash_precedent_attendu=hash_ancre)
    etat.precedent_connu = hash_ancre is not None
    for i, ligne in enumerate(lignes):
        _traiter_ligne_audit(ligne, i, etat)
    return {
        "total": etat.total,
        "valides": etat.valides,
        "invalides": etat.invalides,
        "erreurs": etat.erreurs,
    }


@dataclass
class _EtatVerification:
    """État mutable accumulé pendant `_verifier_lignes_audit`."""

    hash_precedent_attendu: str | None = None
    precedent_connu: bool = False
    total: int = 0
    valides: int = 0
    invalides: int = 0
    erreurs: list[dict[str, object]] = field(default_factory=list)


def _traiter_ligne_audit(ligne: str, i: int, etat: _EtatVerification) -> None:
    """Analyse une ligne JSONL et met à jour `etat` (compteurs + hash attendu)."""
    etat.total += 1
    try:
        resultat = _verifier_une_ligne(
            ligne,
            etat.hash_precedent_attendu,
            etat.precedent_connu,
        )
    except Exception as exc:  # noqa: BLE001 — ligne illisible, cf. skill §8
        etat.invalides += 1
        etat.erreurs.append({"ligne": i + 1, "erreur": str(exc)})
        etat.precedent_connu = False
        return
    if resultat.detail is None:
        etat.valides += 1
    else:
        etat.invalides += 1
        etat.erreurs.append({**resultat.detail, "ligne": i + 1})
    etat.hash_precedent_attendu = resultat.hash_attendu
    etat.precedent_connu = True


@dataclass(frozen=True)
class _ResultatVerifLigne:
    """Résultat de la vérification d'une ligne JSONL d'audit."""

    hash_attendu: str | None
    detail: dict[str, object] | None  # None si valide, sinon dict de diagnostic


def _verifier_une_ligne(
    ligne: str, hash_precedent_attendu: str | None, precedent_connu: bool
) -> _ResultatVerifLigne:
    """Vérifie auto-cohérence et liaison de chaîne d'une ligne, sans effet de bord."""
    donnees = json.loads(ligne)
    hash_attendu = donnees.get("hash_courant")
    hash_precedent_declare = donnees.get("hash_precedent")
    audit = EnregistrementAudit(**donnees)
    hash_calcule = audit.calculer_hash()
    auto_coherent = hash_calcule == hash_attendu
    chaine_coherente = (
        not precedent_connu or hash_precedent_declare == hash_precedent_attendu
    )
    if auto_coherent and chaine_coherente:
        return _ResultatVerifLigne(hash_attendu=hash_attendu, detail=None)
    detail = _detail_erreur_ligne(
        donnees,
        auto_coherent,
        hash_attendu,
        hash_calcule,
        hash_precedent_declare,
        hash_precedent_attendu,
    )
    return _ResultatVerifLigne(hash_attendu=hash_attendu, detail=detail)


def _detail_erreur_ligne(
    donnees: dict[str, object],
    auto_coherent: bool,
    hash_attendu: str | None,
    hash_calcule: str,
    hash_precedent_declare: str | None,
    hash_precedent_attendu: str | None,
) -> dict[str, object]:
    """Construit le dict de diagnostic (hash_auto_incoherent vs chaine_rompue)."""
    detail: dict[str, object] = {"request_id": donnees.get("request_id")}
    if not auto_coherent:
        detail["type"] = "hash_auto_incoherent"
        detail["attendu"] = hash_attendu[:16] if hash_attendu else None
        detail["calcule"] = hash_calcule[:16]
        return detail
    detail["type"] = "chaine_rompue"
    detail["hash_precedent_declare"] = (
        hash_precedent_declare[:16] if hash_precedent_declare else None
    )
    detail["hash_precedent_attendu"] = (
        hash_precedent_attendu[:16] if hash_precedent_attendu else None
    )
    return detail


# ---------------------------------------------------------------------------
# Gestionnaire d'audit
# ---------------------------------------------------------------------------


class GestionnaireAudit:
    """Persiste les enregistrements d'audit avec chaînage SHA-256.

    Deux modes :
      - local  : écriture dans data/audit.jsonl (toujours actif)
      - postgres : INSERT dans PostgreSQL (actif si DSN configuré)

    Le chaînage garantit que toute modification d'un enregistrement
    rend les hash suivants invalides — détection de falsification.
    """

    def __init__(self, postgres_dsn: str | None = None) -> None:
        """Initialise le gestionnaire (la connexion PG est lazy, voir `initialiser`)."""
        self.postgres_dsn = postgres_dsn
        # `asyncpg` ne fournit pas de stubs typés : le pool est manipulé en
        # `Any` pour laisser mypy en paix sans surcast à chaque usage.
        self._pool: Any = None
        self._postgres_ok = False
        # Compte les persistances où PostgreSQL est censé être actif mais où
        # l'INSERT a échoué — signal explicite de divergence local/PostgreSQL,
        # là où l'échec était auparavant seulement loggé et ignoré.
        self.desynchronisations = 0
        CHEMIN_AUDIT_LOCAL.parent.mkdir(parents=True, exist_ok=True)

    async def initialiser(self) -> None:
        """Ouvre le pool PostgreSQL si DSN fourni ; sinon reste en mode local."""
        global _hash_precedent

        if _hash_precedent is None:
            _hash_precedent = await asyncio.to_thread(self._charger_dernier_hash_local)
        if not self.postgres_dsn:
            logger.info("Audit : mode local uniquement (pas de DSN PostgreSQL).")
            return
        try:
            await self._initialiser_postgres()
        except Exception as exc:  # noqa: BLE001 — frontière externe : dégradation gracieuse, cf. §8
            logger.warning("PostgreSQL indisponible, mode local uniquement : %s", exc)
            self._postgres_ok = False

    async def _initialiser_postgres(self) -> None:
        """Ouvre le pool asyncpg, applique le DDL, récupère le dernier hash."""
        global _hash_precedent
        import asyncpg  # type: ignore[import-untyped]

        self._pool = await asyncpg.create_pool(
            self.postgres_dsn,
            min_size=cfg.postgres_pool_min_size,
            max_size=cfg.postgres_pool_max_size,
            command_timeout=cfg.postgres_command_timeout,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(SQL_CREATE_TABLE)
            row = await conn.fetchrow(
                "SELECT hash_courant FROM audit_trail ORDER BY id DESC LIMIT 1"
            )
            if row:
                _hash_precedent = row["hash_courant"]
        self._postgres_ok = True
        logger.info(
            "Audit PostgreSQL initialisé. Dernier hash : %s",
            (_hash_precedent or "aucun")[:16],
        )

    def _charger_dernier_hash_local(self) -> str | None:
        """Retourne le dernier hash_courant du fichier JSONL local, ou None."""
        try:
            if not CHEMIN_AUDIT_LOCAL.exists():
                return None
            lignes = CHEMIN_AUDIT_LOCAL.read_text(encoding="utf-8").strip().splitlines()
            if not lignes:
                return None
            dernier = json.loads(lignes[-1])
            return cast("str | None", dernier.get("hash_courant"))
        except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
            logger.warning("Lecture du dernier hash local échouée : %s", exc)
            return None

    async def persister(self, audit: EnregistrementAudit) -> str:
        """Persiste (local + PostgreSQL) sous verrou, chaîné au hash précédent."""
        global _hash_precedent

        async with _hash_lock:
            audit.hash_precedent = _hash_precedent
            hash_courant = audit.calculer_hash()
            audit.hash_courant = hash_courant
            await self._persister_local_avec_log(audit, hash_courant)
            if self._postgres_ok:
                await self._persister_postgres_avec_log(audit, hash_courant)
            _hash_precedent = hash_courant
        logger.info(
            "Audit — request_id=%s hash=%s…", audit.request_id, hash_courant[:16]
        )
        return hash_courant

    async def _persister_local_avec_log(
        self, audit: EnregistrementAudit, hash_courant: str
    ) -> None:
        """Écrit le JSONL local ; log ERROR sur échec (divergence interne)."""
        if await self._persister_local(audit):
            return
        logger.error(
            "Audit local NON persisté — request_id=%s hash=%s… "
            "(la chaîne locale et le compteur interne divergent désormais)",
            audit.request_id,
            hash_courant[:16],
        )

    async def _persister_postgres_avec_log(
        self, audit: EnregistrementAudit, hash_courant: str
    ) -> None:
        """INSERT PostgreSQL ; sur échec, incrémente desynchronisations + log ERROR."""
        if await self._persister_postgres(audit):
            return
        self.desynchronisations += 1
        logger.error(
            "Audit PostgreSQL NON persisté — request_id=%s hash=%s… "
            "divergence local/PostgreSQL (total cumulé : %d)",
            audit.request_id,
            hash_courant[:16],
            self.desynchronisations,
        )

    def statut(self) -> dict[str, object]:
        """État de synchronisation de l'audit trail, exposable via /health.

        Returns:
            Dict avec postgres_actif et desynchronisations (nombre
            d'INSERT PostgreSQL échoués depuis le démarrage alors que
            PostgreSQL était censé être disponible).
        """
        return {
            "postgres_actif": self._postgres_ok,
            "desynchronisations": self.desynchronisations,
        }

    async def _persister_local(self, audit: EnregistrementAudit) -> bool:
        """Écrit l'enregistrement dans le fichier JSONL local. Retourne le succès."""
        try:
            ligne = audit.model_dump_json() + "\n"
            await asyncio.to_thread(self._ecrire_ligne_local, ligne)
            return True  # noqa: TRY300 — sortie normale du bloc try
        except Exception:
            logger.exception("Écriture audit local échouée")
            return False

    def _ecrire_ligne_local(self, ligne: str) -> None:
        """Écriture synchrone déléguée au threadpool (évite de bloquer l'event loop)."""
        CHEMIN_AUDIT_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        with CHEMIN_AUDIT_LOCAL.open("a", encoding="utf-8") as f:
            f.write(ligne)

    async def _persister_postgres(self, audit: EnregistrementAudit) -> bool:
        """INSERT `audit_trail` (ON CONFLICT DO NOTHING) ; False si pool absent."""
        if not self._pool:
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(_SQL_INSERT_AUDIT, *_valeurs_insert_audit(audit))
            return True  # noqa: TRY300 — sortie normale du bloc try
        except Exception:
            logger.exception("INSERT audit PostgreSQL échoué")
            return False

    async def verifier_integrite(
        self, limite: int = 100
    ) -> dict[str, int | list[dict[str, object]]]:
        """Vérifie auto-cohérence hash et liaison de chaîne des N derniers audits."""
        if not CHEMIN_AUDIT_LOCAL.exists():
            return _bilan_integrite_vide()
        hash_ancre, lignes = await asyncio.to_thread(_lire_fenetre_audit, limite)
        return _verifier_lignes_audit(lignes, hash_ancre)

    async def fermer(self) -> None:
        """Ferme le pool PostgreSQL proprement."""
        if self._pool:
            await self._pool.close()
            logger.info("Pool PostgreSQL audit fermé.")


# Instance globale — utilisée par l'orchestrateur
_gestionnaire: GestionnaireAudit | None = None


async def obtenir_gestionnaire() -> GestionnaireAudit:
    """Retourne l'instance globale, initialisée au premier appel."""
    global _gestionnaire
    if _gestionnaire is None:
        from config import cfg

        _gestionnaire = GestionnaireAudit(
            postgres_dsn=cfg.postgres_dsn if cfg.postgres_dsn != "" else None
        )
        await _gestionnaire.initialiser()
    return _gestionnaire
