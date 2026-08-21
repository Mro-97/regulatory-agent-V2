"""
scripts/pdf_to_json.py — Conversion PDF → JSON canonique
=========================================================

Convertit un PDF réglementaire en JSON canonique DocumentReglementaire
prêt à être ingéré par scripts/ingest.py.

Usage :
    python3 scripts/pdf_to_json.py --fichier data/raw/mon_texte.pdf \\
        --id RGPD_2016_679 \\
        --titre "Règlement (UE) 2016/679" \\
        --source EUR-Lex \\
        --publication 2016-05-04 \\
        --vigueur 2018-05-25

    python3 scripts/pdf_to_json.py --fichier data/raw/anssi_guide.pdf \\
        --id ANSSI_GUIDE_NIS2_2023 \\
        --source ANSSI \\
        --publication 2023-01-01 \\
        --vigueur 2023-01-01

Le script détecte automatiquement les articles (pattern "Article N" ou
"Art. N") et les découpe en VersionArticle avec valid_from = vigueur.

Dépendances : pdfplumber (MIT), pydantic >= 2.7
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models import (
    Chapitre,
    DocumentReglementaire,
    IntervalleValidite,
    SourceReglementaire,
    VersionArticle,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patterns de détection des articles
# ---------------------------------------------------------------------------

# Patterns pour détecter les débuts d'articles
PATTERNS_ARTICLE = [
    re.compile(r"^Article\s+(\d+)\s*[:\-–]?\s*(.*)$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Art\.\s*(\d+)\s*[:\-–]?\s*(.*)$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^§\s*(\d+)\s*[:\-–]?\s*(.*)$", re.MULTILINE),
]

# Patterns pour détecter les chapitres
PATTERNS_CHAPITRE = [
    re.compile(r"^CHAPITRE\s+(I{1,4}V?|[IVX]+|\d+)\s*[:\-–]?\s*(.*)$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^TITRE\s+(I{1,4}V?|[IVX]+|\d+)\s*[:\-–]?\s*(.*)$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^SECTION\s+(I{1,4}V?|[IVX]+|\d+)\s*[:\-–]?\s*(.*)$", re.IGNORECASE | re.MULTILINE),
]


# ---------------------------------------------------------------------------
# Extraction PDF
# ---------------------------------------------------------------------------


def extraire_texte_pdf(chemin: Path) -> str:
    """
    Extrait le texte brut d'un PDF via pdfplumber.

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
        logger.error("pdfplumber requis : pip install pdfplumber --break-system-packages")
        sys.exit(1)

    if not chemin.exists():
        raise FileNotFoundError(f"Fichier introuvable : {chemin}")

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


def detecter_articles(texte: str) -> list[dict]:
    """
    Détecte les articles dans le texte extrait.

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
            positions.append({
                "debut": match.start(),
                "numero": match.group(1),
                "titre_ligne": match.group(2).strip() if match.lastindex >= 2 else "",
            })

    if not positions:
        logger.warning("Aucun article détecté — document traité comme un seul bloc.")
        return [{"numero": "1", "titre": "Document complet", "texte": texte.strip(), "debut": 0}]

    # Trier par position
    positions.sort(key=lambda p: p["debut"])

    # Découper le texte entre les articles
    for i, pos in enumerate(positions):
        debut_texte = pos["debut"]
        fin_texte = positions[i + 1]["debut"] if i + 1 < len(positions) else len(texte)
        bloc = texte[debut_texte:fin_texte].strip()

        # Séparer le titre du corps
        lignes = bloc.split("\n", 1)
        titre = pos["titre_ligne"] or f"Article {pos['numero']}"
        corps = lignes[1].strip() if len(lignes) > 1 else ""

        if not corps:
            continue

        articles.append({
            "numero": pos["numero"],
            "titre": titre,
            "texte": corps,
            "debut": debut_texte,
        })

    logger.info("%d article(s) détecté(s)", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Détection des chapitres
# ---------------------------------------------------------------------------


def detecter_chapitres(texte: str) -> list[dict]:
    """
    Détecte les chapitres et sections dans le texte.

    Args:
        texte: Texte brut.

    Returns:
        Liste de dicts {id, titre, debut}.
    """
    chapitres = []
    for pattern in PATTERNS_CHAPITRE:
        for match in pattern.finditer(texte):
            chapitres.append({
                "id": f"chap_{match.group(1).lower()}",
                "titre": match.group(2).strip() if match.lastindex >= 2 else "",
                "debut": match.start(),
            })

    chapitres.sort(key=lambda c: c["debut"])
    return chapitres


# ---------------------------------------------------------------------------
# Construction du DocumentReglementaire
# ---------------------------------------------------------------------------


def construire_document(
    texte: str,
    doc_id: str,
    titre: str,
    source: SourceReglementaire,
    publication_date: date,
    entry_into_force: date,
    themes: list[str],
    url_source: Optional[str] = None,
) -> DocumentReglementaire:
    """
    Construit un DocumentReglementaire depuis le texte extrait.

    Args:
        texte:             Texte brut du document.
        doc_id:            Identifiant du document.
        titre:             Titre officiel.
        source:            Source institutionnelle.
        publication_date:  Date de publication.
        entry_into_force:  Date d'entrée en vigueur.
        themes:            Tags thématiques.
        url_source:        URL canonique (optionnel).

    Returns:
        DocumentReglementaire prêt à l'ingestion.
    """
    articles_bruts = detecter_articles(texte)
    chapitres_bruts = detecter_chapitres(texte)

    # Si des chapitres ont été détectés, on regroupe les articles
    # dans leur chapitre respectif. Sinon, un seul chapitre.
    if chapitres_bruts:
        # Attribution de chaque article au chapitre dont le début précède
        # immédiatement le sien (le dernier chapitre dont "debut" <= article["debut"]).
        chapitres_map: dict[str, list] = {c["id"]: [] for c in chapitres_bruts}
        chap_ids = [c["id"] for c in chapitres_bruts]
        chap_debuts = [c["debut"] for c in chapitres_bruts]

        for art in articles_bruts:
            art_debut = art.get("debut", 0)
            chap_id = chap_ids[0]  # défaut : avant le premier chapitre détecté
            for cid, debut in zip(chap_ids, chap_debuts):
                if debut <= art_debut:
                    chap_id = cid
                else:
                    break
            chapitres_map[chap_id].append(art)

        chapitres: list[Chapitre] = []
        for c in chapitres_bruts:
            arts = chapitres_map.get(c["id"], [])
            if not arts:
                continue
            versions = [
                VersionArticle(
                    id=f"art_{a['numero']}",
                    titre=a["titre"] or f"Article {a['numero']}",
                    texte=a["texte"],
                    validite=IntervalleValidite(valid_from=entry_into_force),
                )
                for a in arts
            ]
            chapitres.append(Chapitre(
                id=c["id"],
                titre=c["titre"] or c["id"],
                articles=versions,
            ))
    else:
        # Un seul chapitre
        versions = [
            VersionArticle(
                id=f"art_{a['numero']}",
                titre=a["titre"] or f"Article {a['numero']}",
                texte=a["texte"],
                validite=IntervalleValidite(valid_from=entry_into_force),
            )
            for a in articles_bruts
        ]
        chapitres = [Chapitre(
            id="chap_principal",
            titre="Dispositions",
            articles=versions,
        )]

    doc = DocumentReglementaire(
        id=doc_id,
        titre=titre,
        source=source,
        url_source=url_source,
        publication_date=publication_date,
        entry_into_force=entry_into_force,
        version=entry_into_force.isoformat(),
        themes=themes,
        chapitres=chapitres,
    )
    doc.hash_document = doc.calculer_hash()
    return doc


# ---------------------------------------------------------------------------
# Point d'entrée CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convertit un PDF réglementaire en JSON canonique.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--fichier", required=True, type=Path, help="PDF source.")
    parser.add_argument("--id", required=True, dest="doc_id", help="Identifiant du document (ex: RGPD_2016_679).")
    parser.add_argument("--titre", default="", help="Titre officiel du document.")
    parser.add_argument("--source", required=True,
        choices=[s.value for s in SourceReglementaire],
        help="Source institutionnelle.")
    parser.add_argument("--publication", required=True, help="Date de publication (YYYY-MM-DD).")
    parser.add_argument("--vigueur", required=True, help="Date d'entrée en vigueur (YYYY-MM-DD).")
    parser.add_argument("--themes", default="", help="Thèmes séparés par des virgules.")
    parser.add_argument("--url", default=None, help="URL canonique de la source.")
    parser.add_argument("--sortie", default=None, type=Path,
        help="Fichier JSON de sortie (défaut : data/raw/<id>.json).")

    args = parser.parse_args()

    # Validation
    if not args.fichier.exists():
        logger.error("Fichier introuvable : %s", args.fichier)
        sys.exit(1)

    pub_date = date.fromisoformat(args.publication)
    vig_date = date.fromisoformat(args.vigueur)
    themes = [t.strip() for t in args.themes.split(",") if t.strip()]
    source = SourceReglementaire(args.source)

    chemin_sortie = args.sortie or (
        Path(__file__).parent.parent / "data" / "raw" / f"{args.doc_id}.json"
    )
    chemin_sortie.parent.mkdir(parents=True, exist_ok=True)

    # Extraction
    texte = extraire_texte_pdf(args.fichier)

    # Construction
    doc = construire_document(
        texte=texte,
        doc_id=args.doc_id,
        titre=args.titre or args.doc_id,
        source=source,
        publication_date=pub_date,
        entry_into_force=vig_date,
        themes=themes,
        url_source=args.url,
    )

    # Sauvegarde
    chemin_sortie.write_text(
        doc.model_dump_json(indent=2),
        encoding="utf-8",
    )

    total_articles = sum(len(c.articles) for c in doc.chapitres)
    logger.info(
        "JSON généré : %s | chapitres=%d articles=%d",
        chemin_sortie, len(doc.chapitres), total_articles,
    )
    logger.info("Prochaine étape : python3 scripts/ingest.py --fichier %s", chemin_sortie)


from typing import Optional

if __name__ == "__main__":
    main()
