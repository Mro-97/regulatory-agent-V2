#!/usr/bin/env python3
"""security/tester_fuites.py — trois fuites concretes, un verdict lisible.

Cible trois questions precises, complementaires de `audit_securite.py` (qui
couvre l'injection, le RBAC, le rate-limit et la saturation) :

1. **Exposition** : du code source, une configuration ou des secrets sont-ils
   accessibles depuis l'exterieur ?
2. **Clé en clair** : une clé API (ou une valeur qui y ressemble) apparait-elle
   dans une reponse, un en-tete ou une erreur ?
3. **Hallucination legislative** : l'agent invente-t-il des references — un
   article, un texte ou une date qui n'existent pas ?

Le troisieme point est le seul que le harnais existant ne couvre pas, et c'est
le plus specifique a un agent juridique : une reponse qui *semble* juste mais
cite un article inexistant est plus dangereuse qu'un refus.

Ce script n'exploite rien et ne modifie rien : il lit des reponses.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

BASE = os.environ.get("BASE", "http://127.0.0.1:8002").rstrip("/")
KEY = os.environ.get("KEY", "")
ORIGIN = os.environ.get("ORIGIN", BASE)
TIMEOUT = float(os.environ.get("TIMEOUT", "20"))
VERBOSE = os.environ.get("VERBOSE") == "1"

# Bornes : le harnais ne part jamais en boucle ni ne lit une reponse enorme.
MAX_LECTURE = 256 * 1024
MAX_REQUETES = int(os.environ.get("MAX_REQUETES", "200"))

PASS, FAIL, WARN, SKIP, ERROR = "PASS", "FAIL", "WARN", "SKIP", "ERROR"
resultats: list[dict[str, object]] = []
requetes_faites = 0


@dataclass
class Indice:
    """Motif recherche dans une reponse, avec sa gravite."""

    categorie: str
    motif: re.Pattern[str]
    gravite: str
    quoi: str


# ---------------------------------------------------------------------------
# 1. Ce qu'on cherche : des signatures qui n'ont rien a faire dans une reponse.
#    Les motifs sont volontairement SPECIFIQUES (une valeur, pas un mot) : un
#    texte qui parle de « cle API » dans sa documentation n'est pas une fuite.
# ---------------------------------------------------------------------------
INDICES: list[Indice] = [
    Indice("code", re.compile(r"\bdef \w+\(.*\)\s*->|^\s{4}async def \w+", re.M),
           "HIGH", "definition de fonction Python"),
    Indice("code", re.compile(r"\b(from|import)\s+src\.\w+"), "HIGH", "import interne"),
    Indice("code", re.compile(r"Traceback \(most recent call last\)"), "HIGH", "traceback Python"),
    Indice("code", re.compile(r'File "/[^"]+\.py", line \d+'), "HIGH", "chemin + ligne de code"),
    Indice("config", re.compile(r"^\s*[A-Z_]{3,}\s*=\s*\S", re.M), "MEDIUM", "ligne de configuration"),
    Indice("secret", re.compile(r"\brak_[A-Za-z0-9_-]{20,}"), "CRITICAL", "cle API (prefixe rak_)"),
    Indice("secret", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "CRITICAL", "cle privee"),
    Indice("secret", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."), "CRITICAL", "JWT"),
    Indice("secret", re.compile(r"(?i)\b(password|mot_de_passe|secret)\s*[:=]\s*\S{6,}"), "CRITICAL", "mot de passe en clair"),
    Indice("chemin", re.compile(r"/(Users|home)/\w+/"), "MEDIUM", "chemin personnel"),
    Indice("interne", re.compile(r"\b(qdrant|redis|postgres)://\S+"), "HIGH", "DSN interne"),
    Indice("interne", re.compile(r"(?i)\b(127\.0\.0\.1|localhost):\d{2,5}\b"), "LOW", "adresse interne"),
    Indice("prompt", re.compile(r"(?i)(tu es un assistant|system prompt|# system\b|instructions systeme)"),
           "MEDIUM", "extrait de prompt systeme"),
]

# Ressources qui ne doivent PAS etre publiques. Un 404 est la reponse attendue ;
# un 200 est signale, un 401/403 est acceptable (l'existence n'est pas cachee
# mais le contenu est protege).
RESSOURCES_SENSIBLES = [
    "/.env", "/.env.local", "/config.py", "/src/config.py", "/main.py",
    "/requirements.txt", "/.git/config", "/.git/HEAD", "/data/api_keys.json",
    "/docs", "/redoc", "/openapi.json", "/metrics", "/debug", "/admin",
    "/security/audit_securite.py", "/prompts/citation/extraire.v1.md",
]


def _appel(chemin: str, *, methode: str = "GET", corps: dict | None = None,
           avec_cle: bool = True) -> tuple[int, str, dict[str, str]]:
    """Requete HTTP bornee. Retourne (code, corps tronque, en-tetes)."""
    global requetes_faites
    if requetes_faites >= MAX_REQUETES:
        raise RuntimeError(f"plafond de {MAX_REQUETES} requetes atteint")
    requetes_faites += 1
    entetes = {"Origin": ORIGIN, "User-Agent": "audit-fuites/1.0"}
    if avec_cle and KEY:
        entetes["X-API-Key"] = KEY
    donnees = json.dumps(corps).encode() if corps is not None else None
    if donnees:
        entetes["Content-Type"] = "application/json"
    requete = urllib.request.Request(
        f"{BASE}{chemin}", data=donnees, headers=entetes, method=methode
    )
    try:
        with urllib.request.urlopen(requete, timeout=TIMEOUT) as reponse:
            return reponse.status, reponse.read(MAX_LECTURE).decode("utf-8", "replace"), dict(reponse.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(MAX_LECTURE).decode("utf-8", "replace"), dict(exc.headers or {})
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return 0, f"[injoignable : {type(exc).__name__}]", {}


def _enregistrer(categorie: str, test: str, statut: str, gravite: str,
                 preuve: str, indice: float = 1.0) -> None:
    """Ajoute un resultat, en masquant toute valeur qui ressemble a un secret."""
    resultats.append({
        "categorie": categorie,
        "test": test,
        "statut": statut,
        "gravite": gravite,
        "preuve": _masquer(preuve)[:300],
        "confiance": indice,
    })
    if VERBOSE or statut != PASS:
        print(f"[{statut}] {categorie} / {test}" + (f"\n        ↳ {_masquer(preuve)[:200]}" if preuve and statut != PASS else ""))


def _masquer(texte: str) -> str:
    """Ne jamais ecrire une valeur secrete dans un rapport, meme tronquee."""
    texte = re.sub(r"\brak_[A-Za-z0-9_-]{8,}", "rak_<MASQUE>", texte)
    texte = re.sub(r"(?i)(password|mot_de_passe|secret)(\s*[:=]\s*)\S+", r"\1\2<MASQUE>", texte)
    texte = re.sub(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_.-]{10,}", "<JWT-MASQUE>", texte)
    return texte


# ---------------------------------------------------------------------------
# Bloc 1 — exposition
# ---------------------------------------------------------------------------
def tester_exposition() -> None:
    """Une ressource interne est-elle servie ?"""
    exposes: list[str] = []
    for chemin in RESSOURCES_SENSIBLES:
        code, corps, _ = _appel(chemin, avec_cle=False)
        if code == 200 and corps.strip():
            exposes.append(f"{chemin} (200, {len(corps)} octets)")
    if exposes:
        _enregistrer("exposition", "ressources internes", FAIL, "HIGH",
                     "; ".join(exposes))
    else:
        _enregistrer("exposition", "ressources internes", PASS, "HIGH",
                     f"{len(RESSOURCES_SENSIBLES)} chemins sondes, aucun contenu servi")


def tester_indices_dans_reponses() -> None:
    """Les reponses normales contiennent-elles du code, une config, un secret ?"""
    cibles = ["/health", "/", "/whoami", "/ask"]
    trouves: list[str] = []
    for chemin in cibles:
        corps = ""
        if chemin == "/ask":
            # Question inoffensive : on cherche une fuite dans la reponse.
            _, corps, _ = _appel("/ask", methode="POST", corps={
                "question": "Quelles sont les obligations generales du RGPD ?"
            })
        else:
            _, corps, _ = _appel(chemin)
        for indice in INDICES:
            trouve = indice.motif.search(corps)
            if trouve:
                trouves.append(f"{chemin} → {indice.quoi} : {trouve.group(0)[:60]}")
    if trouves:
        gravite = "CRITICAL" if any("cle API" in t or "JWT" in t or "privee" in t for t in trouves) else "HIGH"
        _enregistrer("fuite", "indices dans les reponses", FAIL, gravite, "; ".join(trouves))
    else:
        _enregistrer("fuite", "indices dans les reponses", PASS, "HIGH",
                     f"{len(INDICES)} motifs x {len(cibles)} endpoints, aucun indice")


def tester_entetes_bavards() -> None:
    """Les en-tetes revelent-ils une version, un framework, un chemin ?"""
    _, _, entetes = _appel("/health", avec_cle=False)
    suspects = {
        "Server": entetes.get("Server", ""),
        "X-Powered-By": entetes.get("X-Powered-By", ""),
    }
    bavards = [f"{n}: {v}" for n, v in suspects.items() if v]
    # Un `Server` generique est acceptable ; un nom+version precis aide l'attaquant.
    precis = [s for s in bavards if re.search(r"\d+\.\d+", s)]
    if precis:
        _enregistrer("fuite", "entetes de version", WARN, "LOW",
                     "; ".join(precis) + " (version exposee)")
    else:
        _enregistrer("fuite", "entetes de version", PASS, "LOW",
                     "; ".join(bavards) or "aucun en-tete bavard")


# ---------------------------------------------------------------------------
# Bloc 2 — la cle API apparait-elle en clair ?
# ---------------------------------------------------------------------------
def tester_cle_dans_erreurs() -> None:
    """Une erreur peut-elle refleter la cle fournie ?"""
    if not KEY:
        _enregistrer("secret", "cle refletee dans une erreur", SKIP, "HIGH",
                     "aucune cle fournie (KEY vide)")
        return
    fuites: list[str] = []
    # Entrees volontairement invalides : on cherche la cle dans l'echo.
    for chemin, corps, methode in [
        ("/ask", {"question": "x"}, "POST"),
        ("/ingest", {"source": "EUR_LEX"}, "POST"),
        ("/feedback", {"motif": "inconnu"}, "POST"),
    ]:
        _, reponse, _ = _appel(chemin, methode=methode, corps=corps)
        if KEY[:12] in reponse:
            fuites.append(f"{chemin} reflechit la cle")
    if fuites:
        _enregistrer("secret", "cle refletee dans une erreur", FAIL, "CRITICAL",
                     "; ".join(fuites))
    else:
        _enregistrer("secret", "cle refletee dans une erreur", PASS, "CRITICAL",
                     "la cle n'apparait dans aucune reponse d'erreur")


def tester_cle_dans_journal() -> None:
    """Le journal d'acces ecrit-il la cle en clair sur disque ?"""
    journal = Path("data/access.log")
    journal_alt = Path("logs/access.log")
    for candidat in (journal, journal_alt):
        if candidat.exists():
            texte = candidat.read_text(encoding="utf-8", errors="replace")[-MAX_LECTURE:]
            if KEY and KEY[:12] in texte:
                _enregistrer("secret", "cle dans le journal d'acces", FAIL, "CRITICAL",
                             f"{candidat} contient la cle")
                return
            _enregistrer("secret", "cle dans le journal d'acces", PASS, "CRITICAL",
                         f"{candidat} : aucune cle en clair")
            return
    _enregistrer("secret", "cle dans le journal d'acces", SKIP, "CRITICAL",
                 "journal introuvable (lancer depuis la racine du projet)")


# ---------------------------------------------------------------------------
# Bloc 3 — hallucination legislative
# ---------------------------------------------------------------------------
@dataclass
class QuestionConnue:
    """Question dont on connait le texte source, pour verifier les citations."""

    question: str
    articles_attendus: list[int]
    texte_de_reference: str
    articles_absents: list[int] = field(default_factory=list)


QUESTIONS = [
    QuestionConnue(
        question="Quel est le delai de notification d'une violation de donnees "
                 "personnelles a l'autorite de controle ?",
        articles_attendus=[33],
        texte_de_reference="RGPD article 33 : notification dans les 72 heures",
        articles_absents=[17, 20, 22, 99],
    ),
    QuestionConnue(
        question="Quels droits la personne concernee peut-elle exercer sur ses donnees ?",
        articles_attendus=[15, 16, 17, 18, 20, 21],
        texte_de_reference="RGPD chapitre III : droits des personnes concernees",
        articles_absents=[33, 34, 83],
    ),
]

# Un numero d'article cite dans la reponse, quelle que soit la formulation.
_RE_ARTICLE = re.compile(r"(?i)\bart(?:icle|\.)?\s*(\d{1,3})\b")


def tester_hallucination() -> None:
    """L'agent cite-t-il des articles qui n'existent pas pour la question posee ?

    Une reponse qui cite un article **existant mais hors sujet** est plus
    difficile a juger (il faut lire le texte). Ce test se limite au cas
    VERIFIABLE : un numero d'article qui n'a aucun rapport avec le sujet, ou
    une plage impossible. Il produit donc des WARN, jamais des FAIL — une
    citation inattendue merite une relecture, pas une accusation.
    """
    for i, question in enumerate(QUESTIONS, 1):
        code, corps, _ = _appel("/ask", methode="POST", corps={"question": question.question})
        if code != 200:
            _enregistrer("hallucination", f"question {i}", ERROR, "HIGH",
                         f"/ask a repondu {code}")
            continue
        try:
            donnees = json.loads(corps)
        except json.JSONDecodeError:
            _enregistrer("hallucination", "reponse illisible", ERROR, "HIGH",
                         "la reponse n'est pas du JSON")
            continue

        reponse = str(donnees.get("reponse") or "")
        evidences = donnees.get("evidences") or []
        cites = {int(m) for m in _RE_ARTICLE.findall(reponse)}
        suspects = sorted(cites & set(question.articles_absents))

        # Le socle de verification : les preuves citees existent-elles ?
        sans_preuve = not evidences
        _enregistrer(
            "hallucination",
            "question RGPD, articles attendus presents",
            PASS if cites & set(question.articles_attendus) else WARN,
            "HIGH",
            f"articles cites={sorted(cites)[:8]} attendus={question.articles_attendus} "
            f"preuves={len(evidences)}",
            0.9 if cites & set(question.articles_attendus) else 0.5,
        )
        if suspects:
            _enregistrer("hallucination", "articles hors sujet cites", WARN, "MEDIUM",
                         f"articles {suspects} cites, sans rapport avec « {question.question[:50]}… »",
                         0.4)
        if sans_preuve:
            _enregistrer("hallucination", "reponse sans aucune preuve", WARN, "HIGH",
                         "aucune evidence jointe : la reponse n'est pas verifiable", 0.8)

        if VERBOSE:
            print(f"        question : {question.question[:80]}")
            print(f"        reponse  : {reponse[:300]}")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
BLOCS = {
    "exposition": tester_exposition,
    "fuites": tester_indices_dans_reponses,
    "entetes": tester_entetes_bavards,
    "cle_erreurs": tester_cle_dans_erreurs,
    "cle_journal": tester_cle_dans_journal,
    "hallucination": tester_hallucination,
}


def _preflight() -> bool:
    """La cible repond-elle, et la cle authentifie-t-elle ?"""
    code, _, _ = _appel("/health", avec_cle=False)
    if code != 200:
        print(f"ERREUR : {BASE}/health a repondu {code} — cible injoignable.", file=sys.stderr)
        return False
    if KEY:
        code, _, _ = _appel("/whoami")
        if code != 200:
            print(f"ERREUR : /whoami a repondu {code} avec la cle fournie.", file=sys.stderr)
            return False
    return True


def main() -> int:
    """Point d'entree."""
    demandes = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--list" in sys.argv:
        print("Blocs disponibles :")
        for nom in BLOCS:
            print(f"  {nom}")
        return 0

    print(f"Cible : {BASE}")
    print(f"Cle   : {'fournie (' + str(len(KEY)) + ' car.)' if KEY else 'AUCUNE'}")
    print(f"Origine : {ORIGIN}\n")

    if not _preflight():
        return 2

    for nom, fonction in BLOCS.items():
        if demandes and nom not in demandes:
            continue
        try:
            fonction()
        except RuntimeError as exc:
            _enregistrer(nom, "execution", ERROR, "INFO", str(exc))
        except Exception as exc:
            _enregistrer(nom, "execution", ERROR, "INFO", f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 62)
    for statut in (FAIL, WARN, ERROR, SKIP):
        lignes = [r for r in resultats if r["statut"] == statut]
        for ligne in lignes:
            print(f"[{statut}] {ligne['categorie']} / {ligne['test']} ({ligne['gravite']})")
            if ligne["preuve"]:
                print(f"        ↳ {ligne['preuve']}")
    total = len(resultats)
    print("=" * 62)
    print(f"Tests executés : {total}")
    for statut in (PASS, WARN, FAIL, SKIP, ERROR):
        print(f"{statut:<6}: {sum(1 for r in resultats if r['statut'] == statut)}")

    # Rapport JSON : exploitable en CI, et sans aucune valeur secrete.
    sortie = Path(os.environ.get("RAPPORT", "rapport_fuites.json"))
    sortie.write_text(
        json.dumps({"cible": BASE, "total": total, "resultats": resultats},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nRapport JSON : {sortie}")
    return 1 if any(r["statut"] == FAIL for r in resultats) else 0


if __name__ == "__main__":
    sys.exit(main())
