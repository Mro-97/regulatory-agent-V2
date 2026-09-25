"""tests/test_imports_acycliques.py — aucun cycle d'imports dans le dépôt.

Le dépôt en comptait six : les agents `citation`/`conflit`/`temporal` avec leur
module LLM, `api_security` avec `rate_limit_redis`, `main` avec `src.api`, et
`orchestrator` avec `orchestrator_pipeline`. Un cycle ne casse pas toujours au
démarrage — un import différé ou sous `TYPE_CHECKING` le masque — mais il rend
l'ordre de chargement fragile : `python -c "import src.agents.citation_llm"`
pouvait échouer selon le point d'entrée, et toute réorganisation devenait
risquée.

Le test reconstruit le graphe par analyse AST et refuse toute composante
fortement connexe de plus d'un module. Les imports différés et `TYPE_CHECKING`
sont comptés comme des arêtes : ce sont eux qui referment les cycles les plus
discrets, et les types partagés ont leur place dans un module feuille
(`citation_types.py`, `conflit_types.py`, `temporal_types.py`,
`rate_limit_memory.py`, `demarrage.py`, `orchestrator_types.py`).
"""

from __future__ import annotations

import ast
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DOSSIERS = ("src", "scripts")
MODULES_RACINE = ("config", "main")


def _modules() -> dict[str, Path]:
    """Nom de module pleinement qualifié → fichier, pour tout le dépôt."""
    trouves: dict[str, Path] = {}
    for dossier in DOSSIERS:
        for chemin in (RACINE / dossier).rglob("*.py"):
            if "__pycache__" in chemin.parts:
                continue
            nom = ".".join(chemin.relative_to(RACINE).with_suffix("").parts)
            if nom.endswith(".__init__"):
                nom = nom[: -len(".__init__")]
            trouves[nom] = chemin
    for nom in MODULES_RACINE:
        chemin = RACINE / f"{nom}.py"
        if chemin.exists():
            trouves[nom] = chemin
    return trouves


def _cibles(noeud: ast.Import | ast.ImportFrom) -> list[str]:
    """Modules visés par une instruction d'import (relatifs exclus).

    `from paquet import nom` peut viser un SOUS-MODULE (`from src import api`)
    et pas seulement un symbole : les deux candidats sont proposés, et la
    résolution garde le plus long qui soit un module connu. Sans cela, un cycle
    écrit sous cette forme passait inaperçu.
    """
    if isinstance(noeud, ast.Import):
        return [alias.name for alias in noeud.names]
    if noeud.level:  # import relatif : hors périmètre (le dépôt est absolu)
        return []
    base = noeud.module or ""
    return [
        base,
        *(f"{base}.{alias.name}" if base else alias.name for alias in noeud.names),
    ]


def _aretes(chemin: Path, connus: set[str]) -> set[str]:
    """Modules du dépôt importés par `chemin`, quelle que soit la portée."""
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    bruts = [
        cible
        for noeud in ast.walk(arbre)
        if isinstance(noeud, (ast.Import, ast.ImportFrom))
        for cible in _cibles(noeud)
    ]
    cibles: set[str] = set()
    for brut in bruts:
        morceaux = brut.split(".")
        for i in range(len(morceaux), 0, -1):
            candidat = ".".join(morceaux[:i])
            if candidat in connus:
                cibles.add(candidat)
                break
    return cibles


def _composantes_connexes(graphe: dict[str, set[str]]) -> list[list[str]]:
    """Composantes fortement connexes (Tarjan), cycles uniquement."""
    index: dict[str, int] = {}
    bas: dict[str, int] = {}
    pile: list[str] = []
    sur_pile: set[str] = set()
    compteur = 0
    composantes: list[list[str]] = []

    def explorer(sommet: str) -> None:
        nonlocal compteur
        index[sommet] = bas[sommet] = compteur
        compteur += 1
        pile.append(sommet)
        sur_pile.add(sommet)
        for voisin in graphe.get(sommet, ()):
            if voisin not in index:
                explorer(voisin)
                bas[sommet] = min(bas[sommet], bas[voisin])
            elif voisin in sur_pile:
                bas[sommet] = min(bas[sommet], index[voisin])
        if bas[sommet] != index[sommet]:
            return
        composante: list[str] = []
        while True:
            noeud = pile.pop()
            sur_pile.discard(noeud)
            composante.append(noeud)
            if noeud == sommet:
                break
        composantes.append(composante)

    for sommet in list(graphe):
        if sommet not in index:
            explorer(sommet)
    return [c for c in composantes if len(c) > 1]


def test_aucun_cycle_d_imports() -> None:
    """Aucun groupe de modules ne doit s'importer mutuellement."""
    modules = _modules()
    connus = set(modules)
    graphe = {nom: _aretes(chemin, connus) for nom, chemin in modules.items()}

    cycles = _composantes_connexes(graphe)
    if cycles:
        details = []
        for composante in cycles:
            membres = set(composante)
            aretes = [
                f"{nom} → {cible}"
                for nom in sorted(composante)
                for cible in sorted(graphe[nom] & membres)
            ]
            details.append("  " + " ; ".join(aretes))
        raise AssertionError(
            "cycles d'imports détectés :\n"
            + "\n".join(details)
            + "\nDéplacer les types partagés dans un module feuille "
            "(cf. src/agents/citation_types.py)."
        )
