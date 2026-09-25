"""src/agents/sources.py — Sources réellement citées, et section déterministe.

Module FEUILLE partagé par `explainer` (section « Sources utilisées » de la
réponse) et `citation` (vérification d'ancrage), qui le ré-exporte.

Deux constats du pentest du 2026-09-24 :

1. Le LLM écrivait lui-même la section « Sources utilisées ». Elle citait des
   documents ABSENTS des preuves — jusqu'à des URL fournies dans la question —
   dupliquait des références, et ne correspondait donc pas aux sources
   réellement retrouvées par le retriever. La section est désormais RECONSTRUITE
   à partir des preuves, et de celles-là seulement.

2. Une URL présente dans la réponse est lue comme une source par l'utilisateur,
   alors qu'aucune source du corpus n'est un site web (le prompt l'interdit).
   Les URL sont neutralisées et journalisées.
"""

from __future__ import annotations

import logging
import re

from src.agents.reponse_fondee import reponse_est_non_fondee
from src.models import EvidenceRecuperee

logger = logging.getLogger(__name__)

# URL ou domaine nu dans la réponse. Volontairement large : mieux vaut
# neutraliser une chaîne qui ressemble à un lien que laisser passer une source
# inventée. Les parenthèses/crochets terminaux ne font pas partie du lien.
_RE_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)

_MARQUEUR_LIEN_RETIRE = "[lien retiré]"

# En-tête de la section des sources. Tolérant sur la décoration Markdown et sur
# le contenu qui suit sur la même ligne (« **3) Sources utilisées :** [X / Y] »),
# parce qu'on coupe à partir de l'en-tête : c'est le LLM qui décidait du reste.
_RE_SECTION_SOURCES = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?\*{0,2}[ \t]*(?:3\)|iii\))?[ \t]*"
    r"sources?[ \t]+utilis[ée]es?\b",
    re.IGNORECASE | re.MULTILINE,
)


def _numero_article(article_id: str) -> str:
    """Extrait la première suite de chiffres d'un `article_id` (`art_33` → `33`)."""
    trouve = re.search(r"\d+", article_id or "")
    return trouve.group(0) if trouve else ""


def sources_referencees(
    reponse_texte: str,
    evidences: list[EvidenceRecuperee],
) -> list[EvidenceRecuperee]:
    """Sous-ensemble des `evidences` effectivement mentionnées dans la réponse.

    Une preuve est retenue si son `article_id` brut (`art_33`) ou son numéro
    cité en toutes lettres (« article 33 », « art. 33 ») apparaît dans le texte
    — pas le `document_id` seul, trop large (nommer le RGPD une fois n'implique
    pas les 15 articles).

    Deux cas particuliers : une réponse de refus / « aucune information » ne
    cite RIEN → liste vide, et une réponse sans citation reconnaissable renvoie
    AUSSI une liste vide (M10) : attribuer toutes les preuves à une réponse qui
    n'en nomme aucune gonflait artificiellement les sources et la traçabilité
    d'audit. Les appelants (`orchestrator._executer_etapes_pipeline`,
    `_stream_pipeline_reel`) traitent déjà la liste vide.
    """
    if reponse_est_non_fondee(reponse_texte or ""):
        return []
    texte = (reponse_texte or "").lower()
    gardees: list[EvidenceRecuperee] = []
    for ev in evidences:
        identifiant = ev.article_id.lower()
        cite = identifiant in texte
        # « article 33 » ne vaut que pour un vrai article (préfixe art_),
        # pas pour la 33e section d'un guide (sec_33) ou un contrôle.
        if not cite and identifiant.startswith("art_"):
            numero = _numero_article(ev.article_id)
            if numero:
                cite = re.search(rf"\bart(?:icle|\.)?\s*{numero}\b", texte) is not None
        if cite:
            gardees.append(ev)
    return gardees


def neutraliser_urls(texte: str) -> tuple[str, list[str]]:
    """Remplace les URL de `texte` par un marqueur ; retourne aussi la liste."""
    urls = _RE_URL.findall(texte or "")
    if not urls:
        return texte or "", []
    return _RE_URL.sub(_MARQUEUR_LIEN_RETIRE, texte), urls


def _retirer_section_sources(texte: str) -> str:
    """Texte privé de sa section « Sources utilisées » (elle sera reconstruite)."""
    correspondance = _RE_SECTION_SOURCES.search(texte)
    if correspondance is None:
        return texte
    return texte[: correspondance.start()].rstrip()


def _references_dedupliquees(
    evidences: list[EvidenceRecuperee],
) -> list[EvidenceRecuperee]:
    """Preuves citées, dédupliquées par (document, article), ordre du retrieval."""
    vues: set[tuple[str, str]] = set()
    uniques: list[EvidenceRecuperee] = []
    for evidence in evidences:
        cle = (evidence.document_id, evidence.article_id)
        if cle in vues:
            continue
        vues.add(cle)
        uniques.append(evidence)
    return uniques


def reconstruire_section_sources(
    texte: str,
    evidences: list[EvidenceRecuperee],
) -> tuple[str, list[str]]:
    """Rend `(texte, références)` avec une section de sources fiable.

    Les URL sont neutralisées, la section écrite par le LLM est remplacée par la
    liste des preuves du retriever réellement citées (dédupliquées), et cette
    section disparaît s'il n'y en a aucune — refus, abstention, question hors
    corpus. Le texte des parties 1 et 2 n'est jamais réécrit.
    """
    sans_urls, urls = neutraliser_urls(texte)
    if urls:
        logger.warning(
            "Réponse : %d URL neutralisée(s) (source inventée ?) : %s",
            len(urls),
            ", ".join(urls[:3]),
        )
    corps = _retirer_section_sources(sans_urls)
    citees = _references_dedupliquees(sources_referencees(corps, evidences))
    references = [f"[{ev.document_id} / {ev.article_id}]" for ev in citees]
    if not references:
        return corps, []
    return (
        f"{corps}\n\n3) Sources utilisées : {', '.join(references)}",
        references,
    )
