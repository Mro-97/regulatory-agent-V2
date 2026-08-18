"""
src/audit.py — Audit trail de Regulatory Agent V2
==================================================

Persistance des enregistrements d'audit dans PostgreSQL (Mac C).
Chaînage SHA-256 pour détecter toute altération de l'historique.

En phase 1 (développement mono-machine) : log structuré + fichier JSONL local.
En phase 2 (déploiement) : INSERT PostgreSQL via asyncpg.

Pipeline d'audit :
  EnregistrementAudit → calculer_hash() → chaîner avec hash précédent
  → persister (JSONL local + PostgreSQL si disponible)

Dépendances : asyncpg (optionnel), stdlib uniquement pour le fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)

# Chemin du fichier JSONL local (fallback si PostgreSQL indisponible)
CHEMIN_AUDIT_LOCAL = Path(__file__).parent.parent / "data" / "audit.jsonl"

# Hash du dernier enregistrement — maintenu en mémoire pour le chaînage
_hash_precedent: Optional[str] = None
_hash_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Schéma PostgreSQL (exécuté une seule fois au démarrage)
# ---------------------------------------------------------------------------

SQL_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_trail (
    id              SERIAL PRIMARY KEY,
    request_id      UUID NOT NULL,
    horodatage      TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_query      TEXT NOT NULL,
    date_contexte   DATE,
    documents       JSONB DEFAULT '[]',
    agents          JSONB DEFAULT '[]',
    reponse         TEXT,
    niveau_confiance TEXT,
    validation_humaine BOOLEAN DEFAULT FALSE,
    hash_precedent  CHAR(64),
    hash_courant    CHAR(64) NOT NULL,
    CONSTRAINT audit_hash_unique UNIQUE (hash_courant)
);

CREATE INDEX IF NOT EXISTS idx_audit_request_id ON audit_trail (request_id);
CREATE INDEX IF NOT EXISTS idx_audit_horodatage  ON audit_trail (horodatage DESC);
"""


# ---------------------------------------------------------------------------
# Gestionnaire d'audit
# ---------------------------------------------------------------------------


class GestionnaireAudit:
    """
    Persiste les enregistrements d'audit avec chaînage SHA-256.

    Deux modes :
      - local  : écriture dans data/audit.jsonl (toujours actif)
      - postgres : INSERT dans PostgreSQL (actif si DSN configuré)

    Le chaînage garantit que toute modification d'un enregistrement
    rend les hash suivants invalides — détection de falsification.
    """

    def __init__(self, postgres_dsn: Optional[str] = None) -> None:
        self.postgres_dsn = postgres_dsn
        self._pool = None
        self._postgres_ok = False
        CHEMIN_AUDIT_LOCAL.parent.mkdir(parents=True, exist_ok=True)

    async def initialiser(self) -> None:
        """
        Initialise la connexion PostgreSQL et crée la table si nécessaire.
        Si PostgreSQL est indisponible, continue en mode local uniquement.
        """
        if not self.postgres_dsn:
            logger.info("Audit : mode local uniquement (pas de DSN PostgreSQL).")
            return

        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                self.postgres_dsn,
                min_size=1,
                max_size=5,
                command_timeout=10,
            )
            async with self._pool.acquire() as conn:
                await conn.execute(SQL_CREATE_TABLE)
                # Récupérer le dernier hash pour le chaînage
                row = await conn.fetchrow(
                    "SELECT hash_courant FROM audit_trail ORDER BY id DESC LIMIT 1"
                )
                if row:
                    global _hash_precedent
                    _hash_precedent = row["hash_courant"]

            self._postgres_ok = True
            logger.info("Audit PostgreSQL initialisé. Dernier hash : %s",
                        (_hash_precedent or "aucun")[:16])

        except Exception as exc:
            logger.warning("PostgreSQL indisponible, mode local uniquement : %s", exc)
            self._postgres_ok = False

    async def persister(self, audit) -> str:
        """
        Persiste un EnregistrementAudit et retourne son hash.

        Args:
            audit: EnregistrementAudit (depuis src/models.py).

        Returns:
            Hash SHA-256 de l'enregistrement.
        """
        global _hash_precedent

        async with _hash_lock:
            # Chaînage : injecter le hash précédent avant de calculer
            audit.hash_precedent = _hash_precedent
            hash_courant = audit.calculer_hash()
            audit.hash_courant = hash_courant

            # Persistance locale (toujours)
            await self._persister_local(audit)

            # Persistance PostgreSQL (si disponible)
            if self._postgres_ok:
                await self._persister_postgres(audit)

            _hash_precedent = hash_courant

        logger.info(
            "Audit — request_id=%s hash=%s…",
            audit.request_id, hash_courant[:16],
        )
        return hash_courant

    async def _persister_local(self, audit) -> None:
        """Écrit l'enregistrement dans le fichier JSONL local."""
        try:
            ligne = audit.model_dump_json() + "\n"
            with open(CHEMIN_AUDIT_LOCAL, "a", encoding="utf-8") as f:
                f.write(ligne)
        except Exception as exc:
            logger.error("Écriture audit local échouée : %s", exc)

    async def _persister_postgres(self, audit) -> None:
        """INSERT dans la table audit_trail PostgreSQL."""
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO audit_trail
                        (request_id, horodatage, user_query, date_contexte,
                         documents, agents, reponse, niveau_confiance,
                         validation_humaine, hash_precedent, hash_courant)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    ON CONFLICT (hash_courant) DO NOTHING
                    """,
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
        except Exception as exc:
            logger.error("INSERT audit PostgreSQL échoué : %s", exc)

    async def verifier_integrite(self, limite: int = 100) -> dict:
        """
        Vérifie l'intégrité de la chaîne d'audit locale.

        Relit les N derniers enregistrements du fichier JSONL et
        vérifie que chaque hash_courant correspond bien au contenu.

        Args:
            limite: Nombre maximum d'enregistrements à vérifier.

        Returns:
            Dict avec total, valides, invalides, et détails des erreurs.
        """
        from src.models import EnregistrementAudit

        resultats = {"total": 0, "valides": 0, "invalides": 0, "erreurs": []}

        if not CHEMIN_AUDIT_LOCAL.exists():
            return resultats

        lignes = CHEMIN_AUDIT_LOCAL.read_text(encoding="utf-8").strip().splitlines()
        lignes = lignes[-limite:]

        for i, ligne in enumerate(lignes):
            resultats["total"] += 1
            try:
                donnees = json.loads(ligne)
                hash_attendu = donnees.get("hash_courant")
                audit = EnregistrementAudit(**donnees)
                hash_calcule = audit.calculer_hash()

                if hash_calcule == hash_attendu:
                    resultats["valides"] += 1
                else:
                    resultats["invalides"] += 1
                    resultats["erreurs"].append({
                        "ligne": i + 1,
                        "request_id": donnees.get("request_id"),
                        "attendu": hash_attendu[:16] if hash_attendu else None,
                        "calcule": hash_calcule[:16],
                    })
            except Exception as exc:
                resultats["invalides"] += 1
                resultats["erreurs"].append({"ligne": i + 1, "erreur": str(exc)})

        return resultats

    async def fermer(self) -> None:
        """Ferme le pool PostgreSQL proprement."""
        if self._pool:
            await self._pool.close()
            logger.info("Pool PostgreSQL audit fermé.")


# Instance globale — utilisée par l'orchestrateur
_gestionnaire: Optional[GestionnaireAudit] = None


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
