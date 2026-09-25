#!/usr/bin/env python3
"""scripts/gerer_cles.py — gestion des clés API hachées (RBAC).

Aucune clé n'est stockée en clair : ce script génère une clé, l'affiche
**une seule fois**, et ne persiste que son SHA-256 dans `cfg.api_keys_file`
(`data/api_keys.json` par défaut, mode 0600).

Usage :
    python3 scripts/gerer_cles.py generer --role user --label testeur-alice
    python3 scripts/gerer_cles.py lister
    python3 scripts/gerer_cles.py revoquer --label testeur-alice
    python3 scripts/gerer_cles.py revoquer --hash 3f9a1c        # préfixe suffit

Rôles : user < validateur < admin. Livrer la clé au destinataire par un
canal sûr (gestionnaire de mots de passe partagé, messagerie chiffrée).
"""

from __future__ import annotations

import sys

# Garde-fou : ce script doit tourner avec le Python du venv (3.11+), pas le
# `python3` système (souvent 3.9 sur macOS). UP036 désactivé — la borne est
# volontairement plus basse que target-version pour ce message d'aide.
if sys.version_info < (3, 11):  # noqa: UP036
    sys.exit(
        "Python 3.11+ requis. Lancer avec le venv du projet :\n"
        "    venv/bin/python scripts/gerer_cles.py ..."
    )

import argparse
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

# Le script est lancé depuis la racine du repo ; `src` et `config` importables.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import cfg
from src.auth import PREFIXE_CLE, Role, hacher_cle

_LONGUEUR_ALEA = 48  # octets d'entropie (token_urlsafe) — ~64 caractères


def _lire(chemin: Path) -> list[dict[str, str]]:
    """Charge la liste d'entrées du fichier (vide s'il n'existe pas)."""
    if not chemin.exists():
        return []
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    lignes = donnees.get("keys", []) if isinstance(donnees, dict) else donnees
    return list(lignes or [])


def _ecrire(chemin: Path, entrees: list[dict[str, str]]) -> None:
    """Réécrit le fichier en 0600 (dossier parent en 0700)."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(chemin.parent, 0o700)  # noqa: PTH101
    contenu = json.dumps({"keys": entrees}, ensure_ascii=False, indent=2) + "\n"
    chemin.write_text(contenu, encoding="utf-8")
    os.chmod(chemin, 0o600)  # noqa: PTH101


def _ecrire_stdout(texte: str) -> None:
    """Sortie CLI (évite `print`, banni par ruff T20 hors per-file-ignore)."""
    sys.stdout.write(texte + "\n")


def generer(role: str, label: str) -> int:
    """Crée une clé du rôle donné, persiste son hash, affiche la clé une fois."""
    role_enum = Role.depuis_texte(role)
    label = label.strip()
    if not label:
        _ecrire_stdout("Erreur : --label vide.")
        return 2
    entrees = _lire(cfg.api_keys_file)
    if any(e.get("label") == label for e in entrees):
        _ecrire_stdout(f"Erreur : le label {label!r} existe déjà.")
        return 2
    cle = PREFIXE_CLE + secrets.token_urlsafe(_LONGUEUR_ALEA)
    entrees.append(
        {
            "hash": hacher_cle(cle),
            "role": role_enum.name.lower(),
            "label": label,
            "cree": datetime.now(UTC).date().isoformat(),
        }
    )
    _ecrire(cfg.api_keys_file, entrees)
    _ecrire_stdout("")
    _ecrire_stdout(f"  Clé générée pour {label!r} (rôle {role_enum.name.lower()}) :")
    _ecrire_stdout("")
    _ecrire_stdout(f"      {cle}")
    _ecrire_stdout("")
    _ecrire_stdout("  À copier MAINTENANT — elle n'est plus jamais affichée.")
    _ecrire_stdout(
        f"  Hash enregistré dans {cfg.api_keys_file}. "
        "Prise en compte immédiate, sans redémarrage."
    )
    return 0


def lister() -> int:
    """Affiche les entrées (label, rôle, préfixe de hash, date) — jamais la clé."""
    entrees = _lire(cfg.api_keys_file)
    if not entrees:
        _ecrire_stdout(f"(aucune clé dans {cfg.api_keys_file})")
        return 0
    _ecrire_stdout(f"{'LABEL':<24} {'ROLE':<11} {'HASH':<14} CREE")
    for e in entrees:
        h = str(e.get("hash", ""))[:12]
        _ecrire_stdout(
            f"{e.get('label', '?'):<24} {e.get('role', '?'):<11} {h:<14} "
            f"{e.get('cree', '?')}"
        )
    return 0


def revoquer(label: str | None, prefixe_hash: str | None) -> int:
    """Retire les entrées par label exact ou par préfixe de hash."""
    if not label and not prefixe_hash:
        _ecrire_stdout("Erreur : fournir --label ou --hash.")
        return 2
    entrees = _lire(cfg.api_keys_file)

    def _garde(e: dict[str, str]) -> bool:
        if label and e.get("label") == label:
            return False
        return not (prefixe_hash and str(e.get("hash", "")).startswith(prefixe_hash))

    restantes = [e for e in entrees if _garde(e)]
    retirees = len(entrees) - len(restantes)
    if retirees == 0:
        _ecrire_stdout("Aucune entrée ne correspond.")
        return 1
    _ecrire(cfg.api_keys_file, restantes)
    _ecrire_stdout(
        f"{retirees} clé(s) révoquée(s). Prise en compte immédiate, sans redémarrage."
    )
    return 0


def rotation(jours: int | None, simuler: bool) -> int:
    """Révoque les clés `user` plus anciennes que la durée de vie configurée.

    Les rôles `validateur` et `admin` ne sont JAMAIS touchés (cf.
    src/rotation_cles.py) : la même fonction est appelée par le watcher à
    chaque cycle, cette commande sert au controle manuel et a la simulation.
    """
    from datetime import UTC, datetime

    from src.rotation_cles import entrees_a_revoquer
    from src.rotation_cles import rotation as faire_rotation

    duree = cfg.cle_user_duree_vie_jours if jours is None else jours
    if simuler:
        fautives = entrees_a_revoquer(
            _lire(cfg.api_keys_file), duree, datetime.now(UTC).date()
        )
        if not fautives:
            _ecrire_stdout(f"Aucune clé user de plus de {duree} jours.")
            return 0
        for entree in fautives:
            _ecrire_stdout(
                f"  à révoquer : {entree.get('label')} (créée le {entree.get('cree')})"
            )
        return 0
    revoquees = faire_rotation(jours)
    _ecrire_stdout(
        f"{len(revoquees)} clé(s) user révoquée(s) après {duree} jours "
        "(les clés admin ne sont jamais révoquées automatiquement)."
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    """Construit le parseur d'arguments (sous-commandes)."""
    p = argparse.ArgumentParser(description="Gestion des clés API hachées (RBAC).")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generer", help="créer une clé")
    g.add_argument("--role", required=True, choices=[r.name.lower() for r in Role])
    g.add_argument("--label", required=True, help="nom lisible (unique)")
    sub.add_parser("lister", help="lister les clés (sans les valeurs)")
    r = sub.add_parser("revoquer", help="retirer une clé")
    r.add_argument("--label")
    r.add_argument("--hash", dest="prefixe_hash", help="préfixe du hash suffit")
    rot = sub.add_parser("rotation", help="révoquer les clés user trop anciennes")
    rot.add_argument(
        "--jours", type=int, default=None, help="durée de vie (défaut : config)"
    )
    rot.add_argument("--simuler", action="store_true", help="afficher sans révoquer")
    return p


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée CLI."""
    args = _parser().parse_args(argv)
    if args.cmd == "generer":
        return generer(args.role, args.label)
    if args.cmd == "lister":
        return lister()
    if args.cmd == "revoquer":
        return revoquer(args.label, args.prefixe_hash)
    if args.cmd == "rotation":
        return rotation(args.jours, args.simuler)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
