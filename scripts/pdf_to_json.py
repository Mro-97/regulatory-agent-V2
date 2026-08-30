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
from src.models import (
    Chapitre,
    DocumentReglementaire,
    IntervalleValidite,
    SourceReglementaire,
    VersionArticle,
)

from scripts.pdf_parsing import PATTERNS_ARTICLE as PATTERNS_ARTICLE
from scripts.pdf_parsing import PATTERNS_CHAPITRE as PATTERNS_CHAPITRE
from scripts.pdf_parsing import detecter_articles as detecter_articles
from scripts.pdf_parsing import detecter_chapitres as detecter_chapitres
from scripts.pdf_parsing import extraire_texte_pdf as extraire_texte_pdf

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
    """Assemble un DocumentReglementaire (chapitres + articles) depuis le texte brut."""
    articles_bruts = detecter_articles(texte)
    chapitres_bruts = detecter_chapitres(texte)
    chapitres = _decouper_en_chapitres(
        articles_bruts, chapitres_bruts, entry_into_force
    )
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


def _decouper_en_chapitres(
    articles_bruts: list[dict[str, Any]],
    chapitres_bruts: list[dict[str, Any]],
    entry_into_force: date,
) -> list[Chapitre]:
    """Choisit le découpage : par chapitres détectés, sinon un chapitre unique."""
    max_art_debut = max((a.get("debut", 0) for a in articles_bruts), default=0)
    chapitres_structurels = [c for c in chapitres_bruts if c["debut"] <= max_art_debut]
    if chapitres_bruts and chapitres_structurels:
        return _construire_chapitres_multiples(
            articles_bruts, chapitres_structurels, entry_into_force
        )
    return [_construire_chapitre_unique(articles_bruts, entry_into_force)]


def _construire_chapitres_multiples(
    articles_bruts: list[dict[str, Any]],
    chapitres_structurels: list[dict[str, Any]],
    entry_into_force: date,
) -> list[Chapitre]:
    """Attribue chaque article au chapitre précédent (immédiatement inférieur)."""
    chapitres_uniques = _dedupliquer_par_id(chapitres_structurels)
    chapitres_map: dict[str, list[Any]] = {c["id"]: [] for c in chapitres_uniques}
    chap_ids = [c["id"] for c in chapitres_uniques]
    chap_debuts = [c["debut"] for c in chapitres_uniques]
    for art in articles_bruts:
        chap_id = _resoudre_chapitre_de(art, chap_ids, chap_debuts)
        chapitres_map[chap_id].append(art)
    return [
        Chapitre(
            id=c["id"],
            titre=c["titre"] or c["id"],
            articles=_construire_versions(chapitres_map[c["id"]], entry_into_force),
        )
        for c in chapitres_uniques
        if chapitres_map[c["id"]]
    ]


def _construire_chapitre_unique(
    articles_bruts: list[dict[str, Any]],
    entry_into_force: date,
) -> Chapitre:
    """Retourne un seul chapitre `chap_principal` contenant tous les articles."""
    return Chapitre(
        id="chap_principal",
        titre="Dispositions",
        articles=_construire_versions(articles_bruts, entry_into_force),
    )


def _construire_versions(
    articles_bruts: list[dict[str, Any]],
    entry_into_force: date,
) -> list[VersionArticle]:
    """Convertit chaque dict article en VersionArticle valide dès `entry_into_force`."""
    return [
        VersionArticle(
            id=f"art_{a['numero']}",
            titre=a["titre"] or f"Article {a['numero']}",
            texte=a["texte"],
            validite=IntervalleValidite(valid_from=entry_into_force),
        )
        for a in articles_bruts
    ]


def _dedupliquer_par_id(
    chapitres: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Conserve la première occurrence de chaque id (headers de page répétés)."""
    vus: set[str] = set()
    uniques: list[dict[str, Any]] = []
    for c in chapitres:
        if c["id"] not in vus:
            vus.add(c["id"])
            uniques.append(c)
    return uniques


def _resoudre_chapitre_de(
    art: dict[str, Any],
    chap_ids: list[str],
    chap_debuts: list[int],
) -> str:
    """Retourne le dernier chap_id dont le début précède le début de `art`."""
    art_debut = art.get("debut", 0)
    chap_id = chap_ids[0]
    for cid, debut in zip(chap_ids, chap_debuts, strict=True):
        if debut <= art_debut:
            chap_id = cid
        else:
            break
    return chap_id


# ---------------------------------------------------------------------------
# Point d'entrée CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Entrée CLI : parse args → extrait le PDF → construit le JSON canonique."""
    args = _parser_arguments().parse_args()
    if not args.fichier.exists():
        logger.error("Fichier introuvable : %s", args.fichier)
        sys.exit(1)
    chemin_sortie = _resoudre_chemin_sortie(args)
    doc = _extraire_et_construire(args)
    chemin_sortie.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    _journaliser_sortie(chemin_sortie, doc)


def _parser_arguments() -> argparse.ArgumentParser:
    """Configure et retourne l'ArgumentParser CLI de pdf_to_json."""
    parser = argparse.ArgumentParser(
        description="Convertit un PDF réglementaire en JSON canonique.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _ajouter_arguments_document(parser)
    _ajouter_arguments_dates_et_sortie(parser)
    return parser


def _ajouter_arguments_document(parser: argparse.ArgumentParser) -> None:
    """Groupe --fichier / --id / --titre / --source."""
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


def _ajouter_arguments_dates_et_sortie(parser: argparse.ArgumentParser) -> None:
    """Groupe --publication / --vigueur / --themes / --url / --sortie."""
    parser.add_argument(
        "--publication", required=True, help="Date publication (YYYY-MM-DD)."
    )
    parser.add_argument(
        "--vigueur", required=True, help="Date entrée en vigueur (YYYY-MM-DD)."
    )
    parser.add_argument("--themes", default="", help="Thèmes séparés par des virgules.")
    parser.add_argument("--url", default=None, help="URL canonique de la source.")
    parser.add_argument(
        "--sortie",
        default=None,
        type=Path,
        help="Fichier JSON de sortie (défaut : data/raw/<id>.json).",
    )


def _resoudre_chemin_sortie(args: argparse.Namespace) -> Path:
    """Retourne le chemin explicite (--sortie) ou le défaut `data/raw/<id>.json`."""
    chemin = args.sortie or (
        Path(__file__).parent.parent / "data" / "raw" / f"{args.doc_id}.json"
    )
    chemin.parent.mkdir(parents=True, exist_ok=True)
    return chemin


def _extraire_et_construire(args: argparse.Namespace) -> DocumentReglementaire:
    """Extrait le texte PDF puis assemble le DocumentReglementaire."""
    texte = extraire_texte_pdf(args.fichier)
    return construire_document(
        texte=texte,
        doc_id=args.doc_id,
        titre=args.titre or args.doc_id,
        source=SourceReglementaire(args.source),
        publication_date=date.fromisoformat(args.publication),
        entry_into_force=date.fromisoformat(args.vigueur),
        themes=[t.strip() for t in args.themes.split(",") if t.strip()],
        url_source=args.url,
    )


def _journaliser_sortie(chemin_sortie: Path, doc: DocumentReglementaire) -> None:
    """Trace le fichier produit et rappelle la commande suivante."""
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
