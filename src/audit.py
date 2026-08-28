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
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from config import cfg

from src.models import EnregistrementAudit

logger = logging.getLogger(__name__)

# Chemin du fichier JSONL local (fallback si PostgreSQL indisponible)
CHEMIN_AUDIT_LOCAL = Path(cfg.audit_local_path)

# Hash du dernier enregistrement — maintenu en mémoire pour le chaînage
_hash_precedent: str | None = None
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
    """Persiste les enregistrements d'audit avec chaînage SHA-256.

    Deux modes :
      - local  : écriture dans data/audit.jsonl (toujours actif)
      - postgres : INSERT dans PostgreSQL (actif si DSN configuré)

    Le chaînage garantit que toute modification d'un enregistrement
    rend les hash suivants invalides — détection de falsification.
    """

    def __init__(self, postgres_dsn: str | None = None) -> None:
        self.postgres_dsn = postgres_dsn
        self._pool = None
        self._postgres_ok = False
        # Compte les persistances où PostgreSQL est censé être actif mais où
        # l'INSERT a échoué — signal explicite de divergence local/PostgreSQL,
        # là où l'échec était auparavant seulement loggé et ignoré.
        self.desynchronisations = 0
        CHEMIN_AUDIT_LOCAL.parent.mkdir(parents=True, exist_ok=True)

    async def initialiser(self) -> None:
        """Initialise la connexion PostgreSQL et crée la table si nécessaire.
        Si PostgreSQL est indisponible, continue en mode local uniquement.

        Le chaînage reprend au dernier hash connu (PostgreSQL ou JSONL local).
        """
        global _hash_precedent

        if _hash_precedent is None:
            _hash_precedent = await asyncio.to_thread(self._charger_dernier_hash_local)

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
                    _hash_precedent = row["hash_courant"]

            self._postgres_ok = True
            logger.info(
                "Audit PostgreSQL initialisé. Dernier hash : %s",
                (_hash_precedent or "aucun")[:16],
            )

        except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
            logger.warning("PostgreSQL indisponible, mode local uniquement : %s", exc)
            self._postgres_ok = False

    def _charger_dernier_hash_local(self) -> str | None:
        """Retourne le dernier hash_courant du fichier JSONL local, ou None."""
        try:
            if not CHEMIN_AUDIT_LOCAL.exists():
                return None
            lignes = CHEMIN_AUDIT_LOCAL.read_text(encoding="utf-8").strip().splitlines()
            if not lignes:
                return None
            dernier = json.loads(lignes[-1])
            return dernier.get("hash_courant")
        except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
            logger.warning("Lecture du dernier hash local échouée : %s", exc)
            return None

    async def persister(self, audit: EnregistrementAudit) -> str:
        """Persiste un EnregistrementAudit et retourne son hash.

        Si PostgreSQL est censé être actif (self._postgres_ok) mais que
        l'INSERT échoue, la persistance locale JSONL reste la source de
        vérité — la divergence est comptabilisée (self.desynchronisations)
        et loggée en ERROR plutôt que silencieusement ignorée, afin qu'un
        écart entre les deux chaînes d'audit reste détectable (ex. via
        GestionnaireAudit.statut() / l'endpoint /health).

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
            local_ok = await self._persister_local(audit)
            if not local_ok:
                logger.error(
                    "Audit local NON persisté — request_id=%s hash=%s… "
                    "(la chaîne locale et le compteur interne divergent désormais)",
                    audit.request_id,
                    hash_courant[:16],
                )

            # Persistance PostgreSQL (si disponible)
            if self._postgres_ok:
                postgres_ok = await self._persister_postgres(audit)
                if not postgres_ok:
                    self.desynchronisations += 1
                    logger.error(
                        "Audit PostgreSQL NON persisté — request_id=%s hash=%s… "
                        "divergence local/PostgreSQL (total cumulé : %d)",
                        audit.request_id,
                        hash_courant[:16],
                        self.desynchronisations,
                    )

            _hash_precedent = hash_courant

        logger.info(
            "Audit — request_id=%s hash=%s…",
            audit.request_id,
            hash_courant[:16],
        )
        return hash_courant

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
            return True
        except Exception as exc:
            logger.exception("Écriture audit local échouée : %s", exc)
            return False

    def _ecrire_ligne_local(self, ligne: str) -> None:
        """Écriture synchrone déléguée au threadpool (évite de bloquer l'event loop)."""
        CHEMIN_AUDIT_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        with open(CHEMIN_AUDIT_LOCAL, "a", encoding="utf-8") as f:
            f.write(ligne)

    async def _persister_postgres(self, audit: EnregistrementAudit) -> bool:
        """INSERT dans la table audit_trail PostgreSQL. Retourne le succès."""
        if not self._pool:
            return False
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
                    json.dumps(
                        [a.model_dump(mode="json") for a in audit.agents_executes]
                    ),
                    audit.reponse_finale,
                    audit.niveau_confiance.value,
                    audit.necessite_validation_humaine,
                    audit.hash_precedent,
                    audit.hash_courant,
                )
            return True
        except Exception as exc:
            logger.exception("INSERT audit PostgreSQL échoué : %s", exc)
            return False

    async def verifier_integrite(
        self, limite: int = 100
    ) -> dict[str, int | list[dict[str, object]]]:
        """Vérifie l'intégrité de la chaîne d'audit locale.

        Relit les N derniers enregistrements du fichier JSONL et vérifie,
        pour chacun :
          1. Auto-cohérence : hash_courant correspond bien au contenu.
          2. Liaison de chaîne : hash_precedent correspond au hash_courant
             de l'enregistrement qui le précède dans le fichier.
        Le contrôle (2) est ce qui détecte la suppression, le réordonnancement
        ou le remplacement d'un enregistrement au milieu du fichier — un
        contrôle (1) seul ne le détecterait pas, puisqu'un enregistrement
        retiré reste individuellement auto-cohérent.

        Args:
            limite: Nombre maximum d'enregistrements à vérifier.

        Returns:
            Dict avec total, valides, invalides, et détails des erreurs.
            Chaque erreur précise "type" : "hash_auto_incoherent" (contenu
            modifié) ou "chaine_rompue" (enregistrement manquant/réordonné).
        """
        total = 0
        valides = 0
        invalides = 0
        erreurs: list[dict[str, object]] = []

        if not CHEMIN_AUDIT_LOCAL.exists():
            return {
                "total": total,
                "valides": valides,
                "invalides": invalides,
                "erreurs": erreurs,
            }

        def _lire_dernieres_lignes() -> tuple[str | None, list[str]]:
            """Retourne (hash d'ancrage, lignes de la fenêtre).

            Si le fichier contient plus de `limite` lignes, la ligne juste
            avant la fenêtre est lue séparément pour extraire son
            hash_courant : c'est l'ancre qui permet de vérifier la liaison
            de chaîne de la PREMIÈRE ligne de la fenêtre. Sans ça, un bloc
            auto-cohérent inséré en tête de fenêtre passait inaperçu.
            """
            toutes = CHEMIN_AUDIT_LOCAL.read_text(encoding="utf-8").strip().splitlines()
            fenetre = toutes[-limite:]
            ancre: str | None = None
            if len(toutes) > limite:
                try:
                    ancre = json.loads(toutes[-limite - 1]).get("hash_courant")
                except Exception:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
                    ancre = None
            return ancre, fenetre

        hash_ancre, lignes = await asyncio.to_thread(_lire_dernieres_lignes)

        # hash_courant de l'enregistrement précédent dans la fenêtre lue.
        # Si un ancre a pu être lue (fichier plus long que la fenêtre), la
        # liaison de la première ligne de la fenêtre est vérifiée contre elle.
        hash_precedent_attendu: str | None = hash_ancre
        precedent_connu = hash_ancre is not None

        for i, ligne in enumerate(lignes):
            total += 1
            try:
                donnees = json.loads(ligne)
                hash_attendu = donnees.get("hash_courant")
                hash_precedent_declare = donnees.get("hash_precedent")
                audit = EnregistrementAudit(**donnees)
                hash_calcule = audit.calculer_hash()

                auto_coherent = hash_calcule == hash_attendu
                chaine_coherente = (
                    not precedent_connu
                    or hash_precedent_declare == hash_precedent_attendu
                )

                if auto_coherent and chaine_coherente:
                    valides += 1
                else:
                    invalides += 1
                    detail: dict[str, object] = {
                        "ligne": i + 1,
                        "request_id": donnees.get("request_id"),
                    }
                    if not auto_coherent:
                        detail["type"] = "hash_auto_incoherent"
                        detail["attendu"] = hash_attendu[:16] if hash_attendu else None
                        detail["calcule"] = hash_calcule[:16]
                    else:
                        detail["type"] = "chaine_rompue"
                        detail["hash_precedent_declare"] = (
                            hash_precedent_declare[:16]
                            if hash_precedent_declare
                            else None
                        )
                        detail["hash_precedent_attendu"] = (
                            hash_precedent_attendu[:16]
                            if hash_precedent_attendu
                            else None
                        )
                    erreurs.append(detail)

                hash_precedent_attendu = hash_attendu
                precedent_connu = True
            except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
                invalides += 1
                erreurs.append({"ligne": i + 1, "erreur": str(exc)})
                # Ligne illisible — le prédécesseur pour la suivante n'est plus fiable.
                precedent_connu = False

        return {
            "total": total,
            "valides": valides,
            "invalides": invalides,
            "erreurs": erreurs,
        }

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
