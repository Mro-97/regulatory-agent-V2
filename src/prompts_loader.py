"""src/prompts_loader.py — Chargement des gabarits LLM versionnés (§11).

Le skill §11 impose : aucun prompt en dur dans le code, chaque prompt
vit dans un fichier `prompts/<agent>/<tache>.v<N>.md`, chargé par
identifiant. Le format d'un fichier est documenté dans `prompts/README.md` :
sections `# system` et `# user`, placeholders `string.Template` (`$var`).

Ce loader garde en cache les templates compilés — l'appelant est libre
d'appeler `charger_prompt()` à chaque tour, la lecture disque n'a lieu
qu'une fois par (identifiant, version).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Marqueurs de section reconnus dans un fichier .md — un par ligne, en début.
_MARQUEUR_SYSTEM = "# system"
_MARQUEUR_USER = "# user"


@dataclass(frozen=True)
class PromptIdentifiant:
    """Référence complète (identifiant, version) d'un prompt versionné.

    Utilisé pour l'audit — chaque appel LLM trace son `PromptIdentifiant`
    afin que l'on retrouve exactement quel gabarit a produit la réponse.
    """

    identifiant: str
    version: int

    def __str__(self) -> str:  # noqa: D105 — représentation triviale
        return f"{self.identifiant}@v{self.version}"


@dataclass(frozen=True)
class PromptTemplate:
    """Couple `(system, user)` compilé d'un prompt versionné."""

    identifiant: PromptIdentifiant
    system: Template
    user: Template

    def rendre(self, **variables: object) -> list[dict[str, str]]:
        """Rend le prompt en liste de messages OpenAI-like.

        Args:
            **variables: Substituées dans les templates via `string.Template`
                (syntaxe `$nom` / `${nom}`). Toute valeur non-str est
                convertie via `str()` avant substitution.

        Returns:
            Liste de dicts `{"role": ..., "content": ...}` prête à être
            passée à `MLXInference.generate_avec_messages()`.
        """
        subst = {k: str(v) for k, v in variables.items()}
        return [
            {"role": "system", "content": self.system.substitute(subst)},
            {"role": "user", "content": self.user.substitute(subst)},
        ]


def _decouper_sections(contenu: str) -> tuple[str, str]:
    """Découpe le contenu brut d'un .md en `(system, user)`.

    Les sections sont introduites par `# system` / `# user` en début de
    ligne (ordre libre, une seule occurrence chacune). Le préambule hors
    section est ignoré.
    """
    from src.errors import MalformedPromptError

    sections = _extraire_sections(contenu)
    if not sections["system"] or not sections["user"]:
        raise MalformedPromptError([k for k, v in sections.items() if not v])
    return (
        "\n".join(sections["system"]).strip() + "\n",
        "\n".join(sections["user"]).strip() + "\n",
    )


def _extraire_sections(contenu: str) -> dict[str, list[str]]:
    """Ventile chaque ligne du contenu vers la section active (`system`/`user`)."""
    sections: dict[str, list[str]] = {"system": [], "user": []}
    courante: str | None = None
    for ligne in contenu.splitlines():
        depouillee = ligne.strip()
        if depouillee == _MARQUEUR_SYSTEM:
            courante = "system"
        elif depouillee == _MARQUEUR_USER:
            courante = "user"
        elif courante is not None:
            sections[courante].append(ligne)
    return sections


@lru_cache(maxsize=32)
def charger_prompt(identifiant: str, version: int) -> PromptTemplate:
    """Charge un gabarit `<identifiant>.v<version>.md` depuis `prompts/`."""
    from src.errors import PromptNotFoundError

    chemin = _PROMPTS_DIR / f"{identifiant}.v{version}.md"
    if not chemin.exists():
        raise PromptNotFoundError(chemin)
    system_txt, user_txt = _decouper_sections(chemin.read_text(encoding="utf-8"))
    return PromptTemplate(
        identifiant=PromptIdentifiant(identifiant=identifiant, version=version),
        system=Template(system_txt),
        user=Template(user_txt),
    )
