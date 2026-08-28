"""scripts/pdf_to_json.py — Conversion PDF → JSON canonique
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
"""  # noqa: D205, D301, D415

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

# Patterns et primitives de découpage extraits dans scripts/pdf_parsing.py
# (§12 étape 6). Ré-exportés pour compatibilité descendante.
# fmt: off
from scripts.pdf_parsing import PATTERNS_ARTICLE as PATTERNS_ARTICLE
from scripts.pdf_parsing import PATTERNS_CHAPITRE as PATTERNS_CHAPITRE
from scripts.pdf_parsing import detecter_articles as detecter_articles
from scripts.pdf_parsing import detecter_chapitres as detecter_chapitres
from scripts.pdf_parsing import extraire_texte_pdf as extraire_texte_pdf
from src.models import (
    Chapitre,
    DocumentReglementaire,
    IntervalleValidite,
    SourceReglementaire,
    VersionArticle,
)

# fmt: on

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


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
    url_source: str | None = None,
) -> DocumentReglementaire:
    """Construit un DocumentReglementaire depuis le texte extrait.

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

    # Si tous les marqueurs de chapitre suivent tous les articles, on est
    # face à une TOC/pied de document, pas à une vraie partition : on tombe
    # en mode chapitre unique. Idem s'il n'y a aucun chapitre détecté.
    max_art_debut = max((a.get("debut", 0) for a in articles_bruts), default=0)
    min_chap_debut = min((c["debut"] for c in chapitres_bruts), default=0)  # noqa: F841 - TODO 12 etape 4/6 : revue ciblee au moment du typage / de l extraction
    chapitres_structurels = [c for c in chapitres_bruts if c["debut"] <= max_art_debut]

    if chapitres_bruts and chapitres_structurels:
        # Dédupliquer les marqueurs répétés (headers de page) en conservant
        # la première occurrence de chaque id, dans l'ordre du texte.
        vus: set[str] = set()
        chapitres_uniques: list[dict[str, Any]] = []
        for c in chapitres_structurels:
            if c["id"] not in vus:
                vus.add(c["id"])
                chapitres_uniques.append(c)

        # Attribution de chaque article au chapitre dont le début précède
        # immédiatement le sien (le dernier chapitre dont "debut" <= article["debut"]).
        chapitres_map: dict[str, list[Any]] = {c["id"]: [] for c in chapitres_uniques}
        chap_ids = [c["id"] for c in chapitres_uniques]
        chap_debuts = [c["debut"] for c in chapitres_uniques]

        for art in articles_bruts:
            art_debut = art.get("debut", 0)
            chap_id = chap_ids[0]  # défaut : avant le premier chapitre détecté
            for cid, debut in zip(chap_ids, chap_debuts):  # noqa: B905 - TODO 12 etape 4/6 : revue ciblee au moment du typage / de l extraction
                if debut <= art_debut:
                    chap_id = cid
                else:
                    break
            chapitres_map[chap_id].append(art)

        chapitres: list[Chapitre] = []
        for c in chapitres_uniques:
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
            chapitres.append(
                Chapitre(
                    id=c["id"],
                    titre=c["titre"] or c["id"],
                    articles=versions,
                )
            )
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
        chapitres = [
            Chapitre(
                id="chap_principal",
                titre="Dispositions",
                articles=versions,
            )
        ]

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


def main() -> None:  # noqa: D103 — TODO §12 étape 4 : compléter docstrings
    parser = argparse.ArgumentParser(
        description="Convertit un PDF réglementaire en JSON canonique.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--fichier", required=True, type=Path, help="PDF source.")
    parser.add_argument(
        "--id",
        required=True,
        dest="doc_id",
        help="Identifiant du document (ex: RGPD_2016_679).",
    )
    parser.add_argument("--titre", default="", help="Titre officiel du document.")
    parser.add_argument(
        "--source",
        required=True,
        choices=[s.value for s in SourceReglementaire],
        help="Source institutionnelle.",
    )
    parser.add_argument(
        "--publication", required=True, help="Date de publication (YYYY-MM-DD)."
    )
    parser.add_argument(
        "--vigueur", required=True, help="Date d'entrée en vigueur (YYYY-MM-DD)."
    )
    parser.add_argument("--themes", default="", help="Thèmes séparés par des virgules.")
    parser.add_argument("--url", default=None, help="URL canonique de la source.")
    parser.add_argument(
        "--sortie",
        default=None,
        type=Path,
        help="Fichier JSON de sortie (défaut : data/raw/<id>.json).",
    )

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
        chemin_sortie,
        len(doc.chapitres),
        total_articles,
    )
    logger.info(
        "Prochaine étape : python3 scripts/ingest.py --fichier %s", chemin_sortie
    )


if __name__ == "__main__":
    main()
