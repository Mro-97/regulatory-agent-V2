#!/usr/bin/env python3
"""scripts/corpus_converters.py — brut téléchargé → DocumentReglementaire JSON.

Un convertisseur par famille de format (`SourceReg.convertisseur`) :
- `eurlex`      : HTML EUR-Lex, découpage article par article ;
- `pdf_prose`   : PDF de guide (ANSSI/CNIL/ENISA/NIST), découpage best-effort
                  en sections numérotées / recommandations ;
- `nist_oscal`  : catalogue OSCAL JSON (NIST SP 800-53), un contrôle = un article.

Chaque convertisseur retourne un dict validable par
`src.models.DocumentReglementaire`.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

_RE_TAG = re.compile(r"<[^>]+>")
_RE_WS = re.compile("[ \t\u00a0]+")
_RE_ARTICLE_EURLEX = re.compile(r"^\s*Article\s+(?:premier|\d+[a-z]?)\b", re.IGNORECASE)
_RE_NUM_ARTICLE = re.compile(r"Article\s+(premier|\d+[a-z]?)", re.IGNORECASE)


def _texte_depuis_html(html: str) -> str:
    """HTML → texte brut : retire les balises, normalise les blancs."""
    sans_script = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    avec_sauts = re.sub(r"(?i)</(p|div|br|li|h[1-6]|tr)>", "\n", sans_script)
    texte = _RE_TAG.sub("", avec_sauts)
    texte = (
        texte.replace("&nbsp;", " ")
        .replace("&#160;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#8217;", "'")
        .replace("&rsquo;", "'")
    )
    lignes = [_RE_WS.sub(" ", ligne).strip() for ligne in texte.splitlines()]
    return "\n".join(ligne for ligne in lignes if ligne)


def _num_article(entete: str) -> str:
    """« Article 33 » → « 33 » ; « Article premier » → « 1 »."""
    m = _RE_NUM_ARTICLE.search(entete)
    if not m:
        return "0"
    brut = m.group(1).lower()
    return "1" if brut == "premier" else brut


def _enveloppe(
    src: Any, convertisseur: str, chapitres: list[dict[str, Any]]
) -> dict[str, Any]:
    """Assemble le dict DocumentReglementaire commun à tous les convertisseurs."""
    return {
        "id": src.id,
        "titre": src.titre,
        "source": src.source,
        "url_source": src.url,
        "publication_date": src.publication_date,
        "entry_into_force": src.entry_into_force,
        "version": src.version,
        "themes": list(src.themes),
        "chapitres": chapitres,
        "textes_lies": [],
        "metadonnees_supplementaires": {
            "corpus": {
                "url": src.url,
                "convertisseur": convertisseur,
                "recupere_le": datetime.now(UTC).isoformat(),
            }
        },
    }


def _article(num: str, titre: str, texte: str, valid_from: str) -> dict[str, Any]:
    return {
        "id": f"art_{num}",
        "titre": titre[:200] or f"Article {num}",
        "texte": texte.strip(),
        "validite": {"valid_from": valid_from, "valid_to": None},
        "citations": [],
    }


# ---------------------------------------------------------------------------
# EUR-Lex
# ---------------------------------------------------------------------------
def convertir_eurlex(brut: bytes, src: Any) -> dict[str, Any]:
    """HTML EUR-Lex → un article par bloc « Article N … »."""
    texte = _texte_depuis_html(brut.decode("utf-8", errors="replace"))
    lignes = texte.splitlines()
    articles: list[dict[str, Any]] = []
    courant: list[str] | None = None
    entete = ""
    for ligne in lignes:
        if _RE_ARTICLE_EURLEX.match(ligne):
            if courant is not None:
                articles.append(_bloc_eurlex(entete, courant, src.entry_into_force))
            entete, courant = ligne, []
            continue
        if courant is not None:
            courant.append(ligne)
    if courant is not None:
        articles.append(_bloc_eurlex(entete, courant, src.entry_into_force))
    articles = [a for a in articles if len(a["texte"]) >= 40]
    if not articles:
        raise ValueError(f"{src.id} : aucun article extrait du HTML EUR-Lex")  # noqa: TRY003
    return _enveloppe(
        src, "eurlex", [{"id": "chap1", "titre": None, "articles": articles}]
    )


def _bloc_eurlex(entete: str, corps: list[str], valid_from: str) -> dict[str, Any]:
    num = _num_article(entete)
    titre = corps[0] if corps and len(corps[0]) < 160 else entete
    return _article(num, titre, "\n".join(corps), valid_from)


# ---------------------------------------------------------------------------
# PDF de guide (ANSSI / CNIL / ENISA / NIST) — best effort
# ---------------------------------------------------------------------------
_RE_SECTION = re.compile(
    r"^(?:"
    r"(?P<num>\d+(?:\.\d+){0,2})[.)]?\s+(?P<t1>[A-ZÀ-Ý].{3,120})"  # 1.  / 2.3
    r"|(?P<reco>R\d{1,3})\s*[-\u2013:]?\s*(?P<t2>.{3,140})"  # R31
    r"|FICHE\s+N[°o]\s*(?P<fiche>\d{1,2})\s*[:\-\u2013]?\s*(?P<t3>.{3,140})"  # FICHE
    r")\s*$"
)


def convertir_pdf_prose(brut: bytes, src: Any) -> dict[str, Any]:
    """PDF → sections. Détecte titres numérotés, recommandations Rxx, fiches."""
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(brut)) as pdf:
        lignes = [
            ligne.strip()
            for page in pdf.pages
            for ligne in (page.extract_text() or "").splitlines()
        ]

    sections: list[tuple[str, str, list[str]]] = [("0", "Préambule", [])]
    for ligne in lignes:
        if not ligne:
            continue
        m = _RE_SECTION.match(ligne)
        if m:
            ident, titre = _cle_section(m)
            sections.append((ident, titre, []))
        else:
            sections[-1][2].append(ligne)

    articles = [
        _article(_slug(ident), titre, "\n".join(corps), src.entry_into_force)
        for ident, titre, corps in sections
        if len("\n".join(corps).strip()) >= 40
    ]
    if not articles:
        raise ValueError(f"{src.id} : aucune section exploitable extraite du PDF")  # noqa: TRY003
    return _enveloppe(
        src, "pdf_prose", [{"id": "chap1", "titre": None, "articles": articles}]
    )


def _cle_section(m: re.Match[str]) -> tuple[str, str]:
    if m.group("num"):
        return m.group("num"), m.group("t1").strip()
    if m.group("reco"):
        return m.group("reco"), m.group("t2").strip()
    return f"fiche{m.group('fiche')}", m.group("t3").strip()


def _slug(ident: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", ident.lower()).strip("_") or "0"


# ---------------------------------------------------------------------------
# NIST OSCAL (SP 800-53)
# ---------------------------------------------------------------------------
def convertir_nist_oscal(brut: bytes, src: Any) -> dict[str, Any]:
    """Catalogue OSCAL JSON → un contrôle = un article (`article_id = ac_1`…)."""
    catalogue = json.loads(brut).get("catalog", {})
    articles: list[dict[str, Any]] = []
    for groupe in catalogue.get("groups", []):
        for ctrl in groupe.get("controls", []):
            texte = _texte_controle_oscal(ctrl)
            if len(texte.strip()) < 40:
                continue
            articles.append(
                _article(
                    _slug(str(ctrl.get("id", ""))),
                    str(ctrl.get("title", "")),
                    texte,
                    src.entry_into_force,
                )
            )
    if not articles:
        raise ValueError(f"{src.id} : aucun contrôle OSCAL exploitable")  # noqa: TRY003
    return _enveloppe(
        src, "nist_oscal", [{"id": "chap1", "titre": None, "articles": articles}]
    )


def _texte_controle_oscal(ctrl: dict[str, Any]) -> str:
    morceaux: list[str] = []
    for part in ctrl.get("parts", []):
        prose = part.get("prose")
        if prose:
            morceaux.append(str(prose))
        for sous in part.get("parts", []):
            if sous.get("prose"):
                morceaux.append(str(sous["prose"]))
    return "\n".join(morceaux)


CONVERTISSEURS = {
    "eurlex": convertir_eurlex,
    "pdf_prose": convertir_pdf_prose,
    "nist_oscal": convertir_nist_oscal,
}
