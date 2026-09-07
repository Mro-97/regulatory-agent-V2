"""src/auth.py — Clés API hachées + contrôle d'accès par rôle (RBAC).

Principe : **aucune clé API en clair n'est stockée côté serveur.** Seul le
SHA-256 hexadécimal de chaque clé vit sur disque (`data/api_keys.json` ou
`API_KEYS_HACHEES`). La clé en clair n'existe qu'à deux instants :

- sa génération, par `scripts/gerer_cles.py` (affichée une seule fois) ;
- côté client, saisie dans le navigateur → `sessionStorage` (jamais sur disque).

Trois rôles hiérarchiques : `user` < `validateur` < `admin`.
- user       : /ask, /ask/stream, /feedback, /whoami
- validateur : + /pending, /tache, /approve, /reject
- admin      : + /ingest

Voie dépréciée : `API_KEY` / `API_KEYS` en clair dans l'environnement — hachées
à la volée, rôle `admin`, avec un warning. **Refusées si `environnement=prod`**
(cf. `main.valider_configuration_demarrage`).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from enum import IntEnum
from functools import lru_cache

from config import cfg

logger = logging.getLogger(__name__)

# Préfixe des clés générées — repérable dans un log/commit en cas de fuite
# (comme `ghp_` chez GitHub). N'entre PAS dans le hash côté vérification :
# le hash porte sur la valeur complète, préfixe compris.
PREFIXE_CLE = "rak_"


class Role(IntEnum):
    """Rôles RBAC, ordonnés : un rôle couvre tous les rôles inférieurs."""

    USER = 10
    VALIDATEUR = 20
    ADMIN = 30

    @classmethod
    def depuis_texte(cls, valeur: str) -> Role:
        """Convertit 'user'/'validateur'/'admin' (casse libre) en `Role`."""
        try:
            return cls[valeur.strip().upper()]
        except KeyError:
            choix = ", ".join(r.name.lower() for r in cls)
            msg = f"rôle inconnu : {valeur!r} (attendu : {choix})"
            raise ValueError(msg) from None


@dataclass(frozen=True)
class EntreeCle:
    """Une clé API telle que stockée : hash + rôle + libellé humain."""

    hash: str
    role: Role
    label: str


def hacher_cle(cle: str) -> str:
    """SHA-256 hexadécimal d'une clé API — le seul format persisté."""
    return hashlib.sha256(cle.strip().encode("utf-8")).hexdigest()


def _entree_depuis_triplet(triplet: str) -> EntreeCle | None:
    """Parse `sha256hex:role:label` (format `API_KEYS_HACHEES`)."""
    morceaux = triplet.split(":")
    if len(morceaux) < 2:
        return None
    h = morceaux[0].strip().lower()
    if len(h) != 64:
        logger.warning("API_KEYS_HACHEES : hash invalide ignoré (%r)", h[:12])
        return None
    label = ":".join(morceaux[2:]).strip() or "sans-label"
    try:
        role = Role.depuis_texte(morceaux[1])
    except ValueError as exc:
        logger.warning("API_KEYS_HACHEES : %s", exc)
        return None
    return EntreeCle(hash=h, role=role, label=label)


def _charger_env_hachees() -> list[EntreeCle]:
    """Entrées issues de la variable `API_KEYS_HACHEES`."""
    brut = cfg.api_keys_hachees_str.strip()
    if not brut:
        return []
    return [e for t in brut.split(",") if (e := _entree_depuis_triplet(t))]


def _charger_fichier() -> list[EntreeCle]:
    """Entrées issues de `cfg.api_keys_file` (JSON : liste d'objets)."""
    chemin = cfg.api_keys_file
    if not chemin.exists():
        return []
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Lecture %s impossible", chemin)
        return []
    lignes = donnees.get("keys", donnees) if isinstance(donnees, dict) else donnees
    entrees: list[EntreeCle] = []
    for ligne in lignes or []:
        h = str(ligne.get("hash", "")).strip().lower()
        if len(h) != 64:
            continue
        try:
            role = Role.depuis_texte(str(ligne.get("role", "user")))
        except ValueError:
            continue
        entrees.append(
            EntreeCle(hash=h, role=role, label=str(ligne.get("label", "sans-label")))
        )
    return entrees


def _charger_env_clair() -> list[EntreeCle]:
    """Voie dépréciée : `API_KEY` / `API_KEYS` en clair → hash + rôle admin."""
    clair = cfg.cles_api_clair
    if not clair:
        return []
    if cfg.environnement == "prod":
        logger.error(
            "environnement=prod : %d clé(s) API en clair dans l'environnement "
            "IGNORÉE(s). Migrer vers data/api_keys.json (scripts/gerer_cles.py).",
            len(clair),
        )
        return []
    logger.warning(
        "%d clé(s) API en clair (API_KEY/API_KEYS) — voie DÉPRÉCIÉE, rôle admin. "
        "Migrer vers data/api_keys.json.",
        len(clair),
    )
    return [
        EntreeCle(hash=hacher_cle(c), role=Role.ADMIN, label=f"env-clair-{i}")
        for i, c in enumerate(clair)
    ]


@lru_cache(maxsize=1)
def _magasin() -> tuple[EntreeCle, ...]:
    """Toutes les entrées de clés, tous canaux confondus (mémoïsé)."""
    entrees = [*_charger_fichier(), *_charger_env_hachees(), *_charger_env_clair()]
    if entrees:
        par_role = ", ".join(
            f"{sum(1 for e in entrees if e.role is r)} {r.name.lower()}" for r in Role
        )
        logger.info(
            "Magasin de clés API chargé : %d clé(s) (%s).", len(entrees), par_role
        )
    return tuple(entrees)


def recharger_magasin() -> None:
    """Vide le cache du magasin (après un `gerer_cles.py`, ou en test)."""
    _magasin.cache_clear()


def magasin_configure() -> bool:
    """True si au moins une clé est configurée (sinon l'API répond 503)."""
    return bool(_magasin())


def compte_par_role() -> dict[Role, int]:
    """Nombre de clés par rôle (diagnostic démarrage)."""
    magasin = _magasin()
    return {r: sum(1 for e in magasin if e.role is r) for r in Role}


def identifier(cle_fournie: str | None) -> tuple[Role, str] | None:
    """`(role, label)` si `cle_fournie` correspond à une entrée, sinon `None`.

    Comparaison en temps constant sur TOUTES les entrées, sans court-circuit :
    le temps de réponse ne révèle ni le nombre de clés ni laquelle a matché.
    """
    proposee = (cle_fournie or "").strip()
    h = hacher_cle(proposee)
    trouve: EntreeCle | None = None
    for entree in _magasin():
        if hmac.compare_digest(h, entree.hash):
            trouve = entree
    if trouve is None or not proposee:
        return None
    return (trouve.role, trouve.label)
