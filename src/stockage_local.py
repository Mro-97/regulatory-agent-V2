"""src/stockage_local.py — écriture de fichiers locaux sensibles (perms restreintes).

`data/audit.jsonl` et `data/feedback.jsonl` contiennent les questions des
utilisateurs en clair. Sur un hôte partagé (ou une sauvegarde trop large)
un mode 0644 les expose. Ce helper garantit :

- répertoire parent en 0700 (rwx pour le seul propriétaire) ;
- fichier en 0600 (rw pour le seul propriétaire) ;
- append atomique en UTF-8.

Le `chmod` est « best-effort » : sur un système de fichiers qui ne gère
pas les permissions POSIX (rare en prod Linux) l'écriture se fait quand
même, mais un `logger.warning` est émis.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_MODE_FICHIER = 0o600
_MODE_DOSSIER = 0o700


def _chmod_best_effort(chemin: Path, mode: int) -> None:
    """Applique `mode` à `chemin`, en journalisant sans lever si ça échoue."""
    try:
        os.chmod(chemin, mode)  # noqa: PTH101 — chmod sur un Path déjà résolu
    except OSError as exc:
        logger.warning("chmod %o impossible sur %s : %s", mode, chemin, exc)


def ecrire_ligne_protegee(chemin: Path, ligne: str) -> None:
    """Ajoute `ligne` à `chemin` en garantissant des permissions restreintes.

    Le fichier est créé directement en 0600 via `os.open(..., O_CREAT, 0o600)`
    puis écrit par son descripteur : `Path.open("a")` le créait en
    `0666 & ~umask` (typiquement 0644) et le `chmod` n'arrivait qu'après —
    fenêtre pendant laquelle `data/audit.jsonl` et `data/feedback.jsonl`
    (questions utilisateur en clair) étaient lisibles par le groupe et les
    autres. Le `umask` ne s'applique pas au mode passé à `os.open` lors de la
    création : 0600 est donc effectif dès la première écriture.

    Le `chmod` du dossier parent n'est appliqué que si c'est nous qui venons
    de le créer — on ne touche jamais aux permissions d'un dossier
    préexistant (ex. `/tmp` en test).
    """
    parent = chemin.parent
    parent_cree = not parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    if parent_cree:
        _chmod_best_effort(parent, _MODE_DOSSIER)
    descripteur = os.open(chemin, os.O_APPEND | os.O_CREAT | os.O_WRONLY, _MODE_FICHIER)
    with os.fdopen(descripteur, "a", encoding="utf-8") as fichier:
        fichier.write(ligne)
