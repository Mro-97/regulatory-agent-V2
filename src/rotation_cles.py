"""src/rotation_cles.py — Révocation périodique des clés API de rôle `user`.

Politique demandée le 2026-09-24 : une clé `user` ne vit pas plus de 7 jours ;
une clé `admin` n'est JAMAIS révoquée automatiquement. La distinction est le
cœur du module : une rotation qui emporterait la dernière clé d'administration
rendrait `/ingest` et la gestion des clés inatteignables — l'API refuserait
même de démarrer (garde-fou de `valider_configuration_demarrage`).

Les rôles `validateur` et `admin` sont donc hors périmètre, et une entrée dont
la date de création est illisible est CONSERVÉE : on ne révoque pas sur une
donnée qu'on ne comprend pas.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from config import cfg

logger = logging.getLogger(__name__)

ROLE_ROTATIF = "user"


def _date_creation(entree: dict[str, str]) -> date | None:
    """Date de création d'une entrée, ou None si elle est illisible."""
    brut = str(entree.get("cree", "")).strip()
    if not brut:
        return None
    try:
        return date.fromisoformat(brut[:10])
    except ValueError:
        logger.warning(
            "Rotation : date de création illisible pour la clé %r (%r) — conservée.",
            entree.get("label"),
            brut,
        )
        return None


def entrees_a_revoquer(
    entrees: list[dict[str, str]],
    jours: int,
    aujourdhui: date,
) -> list[dict[str, str]]:
    """Entrées de rôle `user` créées il y a PLUS de `jours` jours (pure).

    Inégalité stricte : une clé créée il y a exactement `jours` jours a
    encore vécu sa durée de vie complète — elle part le lendemain.
    """
    limite = aujourdhui - timedelta(days=jours)
    revoquer: list[dict[str, str]] = []
    for entree in entrees:
        if str(entree.get("role", "")).strip().lower() != ROLE_ROTATIF:
            continue
        creation = _date_creation(entree)
        if creation is None:
            continue
        if creation < limite:
            revoquer.append(entree)
    return revoquer


def _lire(chemin: Path) -> list[dict[str, str]]:
    """Entrées du magasin (`{"keys": [...]}`), liste vide si illisible."""
    if not chemin.exists():
        return []
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception(
            "Rotation : lecture de %s impossible — aucune révocation.", chemin
        )
        return []
    lignes = donnees.get("keys", []) if isinstance(donnees, dict) else donnees
    return [ligne for ligne in lignes or [] if isinstance(ligne, dict)]


def rotation(
    jours: int | None = None,
    *,
    aujourdhui: date | None = None,
) -> list[dict[str, str]]:
    """Révoque les clés `user` trop anciennes et retourne celles révoquées.

    Écrit le magasin seulement s'il y a quelque chose à révoquer : une rotation
    qui ne change rien ne doit pas modifier `mtime` (le magasin de clés de l'API
    est invalidé par cette signature, et une écriture inutile ferait relire le
    fichier pour rien).
    """
    duree = cfg.cle_user_duree_vie_jours if jours is None else jours
    if duree <= 0:
        return []
    chemin = cfg.api_keys_file
    entrees = _lire(chemin)
    reference = aujourdhui or datetime.now(UTC).date()
    a_revoquer = entrees_a_revoquer(entrees, duree, reference)
    if not a_revoquer:
        return []
    hashes = {str(entree.get("hash")) for entree in a_revoquer}
    restantes = [entree for entree in entrees if str(entree.get("hash")) not in hashes]
    try:
        chemin.write_text(
            json.dumps({"keys": restantes}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.exception("Rotation : écriture de %s impossible.", chemin)
        return []
    for entree in a_revoquer:
        logger.warning(
            "Rotation : clé user révoquée — %s (créée le %s).",
            entree.get("label"),
            entree.get("cree"),
        )
    return a_revoquer
