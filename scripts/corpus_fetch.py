#!/usr/bin/env python3
"""scripts/corpus_fetch.py — télécharge et convertit le corpus réglementaire.

Lit `scripts/corpus_sources.py`, télécharge chaque texte dans `corpus/raw/`,
consigne l'empreinte dans `corpus/MANIFEST.json`, puis (sauf `--raw-only`)
convertit en JSON canonique dans `corpus/json/`.

À lancer là où il y a un accès réseau (ex. m4pro2). N'abandonne pas sur
une source en échec — le récapitulatif final liste les KO.

    python scripts/corpus_fetch.py
    python scripts/corpus_fetch.py --only RGPD_2016_679 NIS2_2022_2555
    python scripts/corpus_fetch.py --raw-only
    python scripts/corpus_fetch.py --regenerer-json   # reconvertit depuis raw/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.corpus_converters import CONVERTISSEURS
from scripts.corpus_sources import SOURCES, SourceReg
from src.errors import TelechargementTropVolumineuxError

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("corpus_fetch")

RACINE = Path(__file__).parent.parent
DIR_RAW = RACINE / "corpus" / "raw"
DIR_JSON = RACINE / "corpus" / "json"
MANIFEST = RACINE / "corpus" / "MANIFEST.json"
INDEX = RACINE / "corpus" / "INDEX.md"

_UA = "regulatory-agent-corpus-fetch/1.0 (+usage interne, textes publics)"

# Plafond de téléchargement (flux) : 64 Mo couvrent largement les PDF
# réglementaires du corpus et bornent le risque de saturation disque/mémoire.
TAILLE_MAX_OCTETS = 64 * 1024 * 1024

_EXT = {
    "application/pdf": ".pdf",
    "text/html": ".html",
    "application/json": ".json",
    "application/xml": ".xml",
    "text/xml": ".xml",
}


def _verifier_plafond(source_id: str, octets: int) -> None:
    """Lève si le téléchargement dépasse `TAILLE_MAX_OCTETS`.

    Raises:
        TelechargementTropVolumineuxError: plafond dépassé.
    """
    if octets > TAILLE_MAX_OCTETS:
        raise TelechargementTropVolumineuxError(source_id, octets, TAILLE_MAX_OCTETS)


def _extension(url: str, content_type: str) -> str:
    """Extension déduite du Content-Type, sinon du suffixe d'URL."""
    for mime, ext in _EXT.items():
        if mime in content_type:
            return ext
    suffixe = Path(url.split("?")[0]).suffix.lower()
    return suffixe if suffixe in set(_EXT.values()) else ".bin"


def _telecharger(src: SourceReg) -> tuple[Path, dict[str, object]] | None:
    """Télécharge `src.url` dans corpus/raw/. Retourne (chemin, entrée manifest).

    Le corps est écrit en FLUX avec un plafond de taille : `client.get()`
    chargeait la réponse entière en mémoire avant de l'écrire, si bien qu'un
    fichier de plusieurs Go (ou une bombe de décompression, les redirections
    étant suivies) faisait tomber le processus en OOM. Au-delà de
    `TAILLE_MAX_OCTETS` le téléchargement est abandonné proprement.

    Les en-têtes de la source (`src.entetes`) sont transmis : les API à
    négociation de contenu — CELLAR — renvoient une notice de 41 Mo au lieu du
    PDF si `Accept: application/pdf` est absent.
    """
    chemin_partiel = DIR_RAW / f".{src.id}.part"
    entetes = {"User-Agent": _UA, **src.entetes}
    try:
        with (
            httpx.Client(follow_redirects=True, timeout=60.0) as client,
            client.stream("GET", src.url, headers=entetes) as rep,
        ):
            rep.raise_for_status()
            content_type = rep.headers.get("content-type", "")
            chemin = DIR_RAW / f"{src.id}{_extension(src.url, content_type)}"
            digest = hashlib.sha256()
            octets = 0
            with chemin_partiel.open("wb") as fichier:
                for morceau in rep.iter_bytes():
                    octets += len(morceau)
                    _verifier_plafond(src.id, octets)
                    digest.update(morceau)
                    fichier.write(morceau)
    except Exception as exc:  # noqa: BLE001 — frontière réseau, on continue
        logger.warning("  KO téléchargement %s : %s", src.id, exc)
        chemin_partiel.unlink(missing_ok=True)
        return None
    chemin_partiel.replace(chemin)
    entree = {
        "id": src.id,
        "url": src.url,
        "sha256": digest.hexdigest(),
        "octets": octets,
        "content_type": content_type,
        "recupere_le": datetime.now(UTC).isoformat(),
        "fichier": chemin.name,
    }
    logger.info("  OK %s — %d Ko (%s)", src.id, octets // 1024, chemin.name)
    return chemin, entree


def _convertir(src: SourceReg, chemin_raw: Path) -> bool:
    """Convertit `chemin_raw` en corpus/json/<id>.json ; valide le schéma."""
    convertisseur = CONVERTISSEURS.get(src.convertisseur)
    if convertisseur is None:
        logger.warning("  pas de convertisseur '%s' pour %s", src.convertisseur, src.id)
        return False
    try:
        doc = convertisseur(chemin_raw.read_bytes(), src)
        from src.models import DocumentReglementaire

        DocumentReglementaire.model_validate(doc)
    except Exception as exc:  # noqa: BLE001 — best-effort, on rapporte
        logger.warning("  KO conversion %s : %s", src.id, exc)
        return False
    (DIR_JSON / f"{src.id}.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    nb = sum(len(c["articles"]) for c in doc["chapitres"])
    logger.info("  → %s.json (%d articles/sections)", src.id, nb)
    return True


def _charger_manifest() -> dict[str, dict[str, object]]:
    """Charge `corpus/MANIFEST.json`, ou un manifeste vide s'il n'existe pas."""
    if MANIFEST.exists():
        charge: dict[str, dict[str, object]] = json.loads(MANIFEST.read_text())
        return charge
    return {}


def _ecrire_index() -> None:
    """Régénère `corpus/INDEX.md` (inventaire lisible des sources)."""
    lignes = [
        "# corpus/INDEX.md — inventaire des sources\n",
        "| id | source | convertisseur | à vérifier | JSON | URL |",
        "|---|---|---|---|---|---|",
    ]
    for src in SOURCES:
        a_json = "✓" if (DIR_JSON / f"{src.id}.json").exists() else "—"
        av = "⚠️" if src.a_verifier else ""
        lignes.append(
            f"| `{src.id}` | {src.source} | {src.convertisseur} | {av} | "
            f"{a_json} | <{src.url}> |"
        )
    lignes.append(f"\n_Généré le {datetime.now(UTC):%Y-%m-%d %H:%M} UTC._")
    INDEX.write_text("\n".join(lignes) + "\n", encoding="utf-8")


def main() -> None:  # noqa: D103
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="+", metavar="ID", help="restreindre à ces ids")
    ap.add_argument(
        "--raw-only", action="store_true", help="télécharger sans convertir"
    )
    ap.add_argument(
        "--regenerer-json",
        action="store_true",
        help="reconvertir depuis corpus/raw/ sans retélécharger",
    )
    args = ap.parse_args()

    DIR_RAW.mkdir(parents=True, exist_ok=True)
    DIR_JSON.mkdir(parents=True, exist_ok=True)
    cibles = [s for s in SOURCES if not args.only or s.id in args.only]
    manifest = _charger_manifest()
    ok_dl, ok_conv, echecs = 0, 0, []

    for src in cibles:
        logger.info("• %s", src.id)
        chemin_raw: Path | None = None
        if args.regenerer_json:
            trouve = list(DIR_RAW.glob(f"{src.id}.*"))
            chemin_raw = trouve[0] if trouve else None
            if chemin_raw is None:
                logger.warning(
                    "  raw absent pour %s — relancer sans --regenerer-json", src.id
                )
        else:
            res = _telecharger(src)
            if res is not None:
                chemin_raw, entree = res
                manifest[src.id] = entree
                ok_dl += 1
        if chemin_raw is None:
            echecs.append(src.id)
            continue
        if not args.raw_only and _convertir(src, chemin_raw):
            ok_conv += 1
        elif not args.raw_only:
            echecs.append(src.id)

    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _ecrire_index()
    logger.info(
        "\nTerminé : %d téléchargés, %d convertis, %d en échec%s",
        ok_dl,
        ok_conv,
        len(set(echecs)),
        f" ({', '.join(sorted(set(echecs)))})" if echecs else "",
    )


if __name__ == "__main__":
    main()
