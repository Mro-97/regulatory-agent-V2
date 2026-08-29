"""scripts/pdf_parsing.py — Extraction et découpage PDF.

Extraits de scripts/pdf_to_json.py (§12 étape 6). Regroupe les patterns
de détection (articles, chapitres) et les primitives de découpage : le
script `pdf_to_json.py` ne conserve que la construction du document
canonique et le point d'entrée CLI.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patterns de détection des articles
# ---------------------------------------------------------------------------

# Patterns pour détecter les débuts d'articles
PATTERNS_ARTICLE = [
    re.compile(r"^Article\s+(\d+)\s*[:\-–]?\s*(.*)$", re.IGNORECASE | re.MULTILINE),  # noqa: RUF001 — caractère typographique français légitime
    re.compile(r"^Art\.\s*(\d+)\s*[:\-–]?\s*(.*)$", re.IGNORECASE | re.MULTILINE),  # noqa: RUF001 — caractère typographique français légitime
    re.compile(r"^§\s*(\d+)\s*[:\-–]?\s*(.*)$", re.MULTILINE),  # noqa: RUF001 — caractère typographique français légitime
]

# Patterns pour détecter les chapitres
PATTERNS_CHAPITRE = [
    re.compile(
        r"^CHAPITRE\s+(I{1,4}V?|[IVX]+|\d+)\s*[:\-–]?\s*(.*)$",  # noqa: RUF001 — caractère typographique français légitime
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"^TITRE\s+(I{1,4}V?|[IVX]+|\d+)\s*[:\-–]?\s*(.*)$",  # noqa: RUF001 — caractère typographique français légitime
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"^SECTION\s+(I{1,4}V?|[IVX]+|\d+)\s*[:\-–]?\s*(.*)$",  # noqa: RUF001 — caractère typographique français légitime
        re.IGNORECASE | re.MULTILINE,
    ),
]


# ---------------------------------------------------------------------------
# Extraction PDF
# ---------------------------------------------------------------------------


def extraire_texte_pdf(chemin: Path) -> str:
    """Extrait le texte brut d'un PDF via pdfplumber.

    Args:
        chemin: Chemin vers le fichier PDF.

    Returns:
        Texte brut extrait, pages séparées par des sauts de ligne doubles.

    Raises:
        ImportError: Si pdfplumber n'est pas installé.
        FileNotFoundError: Si le fichier n'existe pas.
    """
    try:
        import pdfplumber
    except ImportError:
        logger.exception(
            "pdfplumber requis : pip install pdfplumber --break-system-packages"
        )
        sys.exit(1)

    if not chemin.exists():
        from src.errors import ExtractionFailedError

        raise ExtractionFailedError(str(chemin), reason="fichier introuvable")

    logger.info("Extraction texte : %s", chemin.name)
    pages_texte: list[str] = []

    with pdfplumber.open(chemin) as pdf:
        logger.info("Nombre de pages : %d", len(pdf.pages))
        for i, page in enumerate(pdf.pages, 1):
            texte = page.extract_text()
            if texte:
                pages_texte.append(texte.strip())
            if i % 20 == 0:
                logger.info("  Pages traitées : %d/%d", i, len(pdf.pages))

    texte_complet = "\n\n".join(pages_texte)
    logger.info("Extraction terminée — %d caractères", len(texte_complet))
    return texte_complet


# ---------------------------------------------------------------------------
# Détection des articles
# ---------------------------------------------------------------------------


def detecter_articles(texte: str) -> list[dict[str, Any]]:
    """Détecte les articles dans le texte extrait.

    Stratégie : chercher les patterns "Article N" et découper le texte
    entre chaque occurrence.

    Args:
        texte: Texte brut du document.

    Returns:
        Liste de dicts {numero, titre, texte, debut} — "debut" est la position
        du début de l'article dans le texte source, utilisée pour l'attribution
        au bon chapitre dans construire_document().
    """
    articles = []
    positions = []

    for pattern in PATTERNS_ARTICLE:
        for match in pattern.finditer(texte):
            positions.append(
                {
                    "debut": match.start(),
                    "numero": match.group(1),
                    "titre_ligne": match.group(2).strip()
                    if match.lastindex is not None and match.lastindex >= 2
                    else "",
                }
            )

    if not positions:
        logger.warning("Aucun article détecté — document traité comme un seul bloc.")
        return [
            {
                "numero": "1",
                "titre": "Document complet",
                "texte": texte.strip(),
                "debut": 0,
            }
        ]

    # Trier par position
    positions.sort(key=lambda p: p["debut"])

    # Découper le texte entre les articles
    for i, pos in enumerate(positions):
        debut_texte = int(pos["debut"])
        fin_texte = (
            int(positions[i + 1]["debut"]) if i + 1 < len(positions) else len(texte)
        )
        bloc = texte[debut_texte:fin_texte].strip()

        # Séparer le titre du corps
        lignes = bloc.split("\n", 1)
        titre = pos["titre_ligne"] or f"Article {pos['numero']}"
        corps = lignes[1].strip() if len(lignes) > 1 else ""

        if not corps:
            continue

        articles.append(
            {
                "numero": pos["numero"],
                "titre": titre,
                "texte": corps,
                "debut": debut_texte,
            }
        )

    logger.info("%d article(s) détecté(s)", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Détection des chapitres
# ---------------------------------------------------------------------------


def detecter_chapitres(texte: str) -> list[dict[str, Any]]:
    """Détecte les chapitres et sections dans le texte.

    Args:
        texte: Texte brut.

    Returns:
        Liste de dicts {id, titre, debut}.
    """
    chapitres = []
    for pattern in PATTERNS_CHAPITRE:
        for match in pattern.finditer(texte):
            chapitres.append(
                {
                    "id": f"chap_{match.group(1).lower()}",
                    "titre": (
                        match.group(2).strip()
                        if match.lastindex is not None and match.lastindex >= 2
                        else ""
                    ),
                    "debut": match.start(),
                }
            )

    chapitres.sort(key=lambda c: c["debut"])
    return chapitres
