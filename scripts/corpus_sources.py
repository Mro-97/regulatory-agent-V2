#!/usr/bin/env python3
"""scripts/corpus_sources.py — registre déclaratif des textes du corpus.

Source de vérité unique pour `scripts/corpus_fetch.py`. Une entrée =
un document réglementaire à télécharger et convertir en JSON canonique.

Les URL EUR-Lex (CELEX) sont stables. Les URL ANSSI / CNIL / ENISA / NIST
sont données au mieux et marquées `a_verifier=True` : ces sites
réorganisent régulièrement leurs chemins — `corpus_fetch.py` signale les
404 sans planter, on corrige l'URL puis on relance.

`source` doit correspondre à `src.models.SourceReglementaire`
(EUR-Lex / Légifrance / ANSSI / CNIL / INERIS / Autre). ENISA et NIST
tombent donc en « Autre ».
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceReg:
    """Un texte réglementaire à intégrer au corpus."""

    id: str
    titre: str
    source: str
    url: str
    convertisseur: str  # "eurlex" | "pdf_prose" | "nist_oscal"
    publication_date: str
    entry_into_force: str
    version: str
    themes: list[str] = field(default_factory=list)
    a_verifier: bool = False


def _eurlex(celex: str) -> str:
    return f"https://eur-lex.europa.eu/legal-content/FR/TXT/HTML/?uri=CELEX:{celex}"


_CYBER = ["cybersecurite", "numerique"]
_DATA = ["protection_donnees", "numerique"]

# ---------------------------------------------------------------------------
# EUR-Lex — actes de base (CELEX). URLs stables.
# ---------------------------------------------------------------------------
_EURLEX: list[SourceReg] = [
    SourceReg(
        "RGPD_2016_679",
        "Règlement (UE) 2016/679 — protection des données (RGPD)",
        "EUR-Lex",
        _eurlex("32016R0679"),
        "eurlex",
        "2016-05-04",
        "2018-05-25",
        "2016-05-04",
        _DATA,
    ),
    SourceReg(
        "EPRIVACY_2002_58",
        "Directive 2002/58/CE — vie privée et communications électroniques",
        "EUR-Lex",
        _eurlex("32002L0058"),
        "eurlex",
        "2002-07-31",
        "2003-10-31",
        "2002-07-31",
        _DATA,
    ),
    SourceReg(
        "NIS2_2022_2555",
        "Directive (UE) 2022/2555 — mesures pour un niveau élevé de cybersécurité (NIS 2)",
        "EUR-Lex",
        _eurlex("32022L2555"),
        "eurlex",
        "2022-12-27",
        "2023-01-16",
        "2022-12-14",
        _CYBER,
    ),
    SourceReg(
        "DORA_2022_2554",
        "Règlement (UE) 2022/2554 — résilience opérationnelle numérique (DORA)",
        "EUR-Lex",
        _eurlex("32022R2554"),
        "eurlex",
        "2022-12-27",
        "2025-01-17",
        "2022-12-14",
        _CYBER,
    ),
    SourceReg(
        "AI_ACT_2024_1689",
        "Règlement (UE) 2024/1689 — intelligence artificielle (AI Act)",
        "EUR-Lex",
        _eurlex("32024R1689"),
        "eurlex",
        "2024-07-12",
        "2024-08-01",
        "2024-06-13",
        ["ia", "numerique"],
    ),
    SourceReg(
        "CRA_2024_2847",
        "Règlement (UE) 2024/2847 — cyber-résilience (Cyber Resilience Act)",
        "EUR-Lex",
        _eurlex("32024R2847"),
        "eurlex",
        "2024-11-20",
        "2024-12-10",
        "2024-10-23",
        _CYBER,
    ),
    SourceReg(
        "CSA_2019_881",
        "Règlement (UE) 2019/881 — ENISA et certification de cybersécurité (Cybersecurity Act)",
        "EUR-Lex",
        _eurlex("32019R0881"),
        "eurlex",
        "2019-06-07",
        "2019-06-27",
        "2019-04-17",
        _CYBER,
    ),
    SourceReg(
        "EIDAS_2014_910",
        "Règlement (UE) n° 910/2014 — identification électronique et services de confiance (eIDAS)",
        "EUR-Lex",
        _eurlex("32014R0910"),
        "eurlex",
        "2014-08-28",
        "2016-07-01",
        "2014-07-23",
        _CYBER,
    ),
    SourceReg(
        "EIDAS2_2024_1183",
        "Règlement (UE) 2024/1183 — cadre européen d'identité numérique (eIDAS 2)",
        "EUR-Lex",
        _eurlex("32024R1183"),
        "eurlex",
        "2024-04-30",
        "2024-05-20",
        "2024-04-11",
        _CYBER,
    ),
    SourceReg(
        "CER_2022_2557",
        "Directive (UE) 2022/2557 — résilience des entités critiques (CER)",
        "EUR-Lex",
        _eurlex("32022L2557"),
        "eurlex",
        "2022-12-27",
        "2023-01-16",
        "2022-12-14",
        _CYBER,
    ),
    SourceReg(
        "DATA_ACT_2023_2854",
        "Règlement (UE) 2023/2854 — règles harmonisées pour l'accès aux données (Data Act)",
        "EUR-Lex",
        _eurlex("32023R2854"),
        "eurlex",
        "2023-12-22",
        "2024-01-11",
        "2023-12-13",
        _DATA,
    ),
    SourceReg(
        "DGA_2022_868",
        "Règlement (UE) 2022/868 — gouvernance européenne des données (Data Governance Act)",
        "EUR-Lex",
        _eurlex("32022R0868"),
        "eurlex",
        "2022-06-03",
        "2022-06-23",
        "2022-05-30",
        _DATA,
    ),
]

# ---------------------------------------------------------------------------
# ANSSI — guides & recommandations (PDF). URLs à vérifier.
# ---------------------------------------------------------------------------
_ANSSI: list[SourceReg] = [
    SourceReg(
        "ANSSI_HYGIENE_INFORMATIQUE",
        "ANSSI — Guide d'hygiène informatique (42 mesures)",
        "ANSSI",
        "https://cyber.gouv.fr/sites/default/files/2017/01/guide_hygiene_informatique_anssi.pdf",
        "pdf_prose",
        "2017-01-01",
        "2017-01-01",
        "2017-01",
        _CYBER,
        a_verifier=True,
    ),
    SourceReg(
        "ANSSI_ADMIN_SECURISEE",
        "ANSSI — Recommandations pour l'administration sécurisée des SI (PA-022)",
        "ANSSI",
        "https://cyber.gouv.fr/sites/default/files/2018/04/guide_admin_securisee_si_anssi_pa_022_v3.pdf",
        "pdf_prose",
        "2021-05-01",
        "2021-05-01",
        "3.0",
        _CYBER,
        a_verifier=True,
    ),
    SourceReg(
        "ANSSI_MFA_MDP",
        "ANSSI — Recommandations relatives à l'authentification multifacteur et aux mots de passe",
        "ANSSI",
        "https://cyber.gouv.fr/sites/default/files/document/reco_authentification_multifacteur_mots_de_passe.pdf",
        "pdf_prose",
        "2021-10-08",
        "2021-10-08",
        "1.0",
        _CYBER,
        a_verifier=True,
    ),
    SourceReg(
        "ANSSI_EBIOS_RM",
        "ANSSI — EBIOS Risk Manager (méthode d'appréciation des risques)",
        "ANSSI",
        "https://cyber.gouv.fr/sites/default/files/2018/10/guide-methode-ebios-risk-manager.pdf",
        "pdf_prose",
        "2018-10-01",
        "2018-10-01",
        "1.5",
        _CYBER,
        a_verifier=True,
    ),
]

# ---------------------------------------------------------------------------
# CNIL — guides (PDF). URLs à vérifier.
# ---------------------------------------------------------------------------
_CNIL: list[SourceReg] = [
    SourceReg(
        "CNIL_GUIDE_SECURITE",
        "CNIL — Guide de la sécurité des données personnelles",
        "CNIL",
        "https://www.cnil.fr/sites/cnil/files/2024-03/cnil_guide_securite_personnelle_2024.pdf",
        "pdf_prose",
        "2024-03-01",
        "2024-03-01",
        "2024",
        _DATA,
        a_verifier=True,
    ),
    SourceReg(
        "CNIL_GUIDE_DEVELOPPEUR",
        "CNIL — Guide RGPD du développeur",
        "CNIL",
        "https://www.cnil.fr/sites/cnil/files/atoms/files/cnil_guide_rgpd_du_developpeur.pdf",
        "pdf_prose",
        "2023-01-01",
        "2023-01-01",
        "2023",
        _DATA,
        a_verifier=True,
    ),
    SourceReg(
        "CNIL_RECO_MOTS_DE_PASSE",
        "CNIL — Recommandation relative aux mots de passe et autres secrets partagés",
        "CNIL",
        "https://www.cnil.fr/sites/cnil/files/2022-10/recommandation_mots_de_passe.pdf",
        "pdf_prose",
        "2022-10-17",
        "2022-10-17",
        "2022",
        _DATA,
        a_verifier=True,
    ),
]

# ---------------------------------------------------------------------------
# ENISA / NIST — cadres & lignes directrices. « Autre » côté SourceReglementaire.
# ---------------------------------------------------------------------------
_ENISA_NIST: list[SourceReg] = [
    SourceReg(
        "ENISA_NIS2_IMPLEMENTATION",
        "ENISA — Technical implementation guidance for NIS2 security measures",
        "Autre",
        "https://www.enisa.europa.eu/publications/implementation-guidance-on-nis2-security-measures/@@download/fullReport",
        "pdf_prose",
        "2024-11-01",
        "2024-11-01",
        "2024",
        _CYBER,
        a_verifier=True,
    ),
    SourceReg(
        "NIST_CSF_2_0",
        "NIST — Cybersecurity Framework (CSF) 2.0",
        "Autre",
        "https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.29.pdf",
        "pdf_prose",
        "2024-02-26",
        "2024-02-26",
        "2.0",
        _CYBER,
        a_verifier=True,
    ),
    SourceReg(
        "NIST_SP_800_53_R5",
        "NIST SP 800-53 Rev. 5 — Security and Privacy Controls (catalogue OSCAL)",
        "Autre",
        "https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json",
        "nist_oscal",
        "2020-09-23",
        "2020-09-23",
        "5.1.1",
        _CYBER,
    ),
    SourceReg(
        "NIST_SP_800_207",
        "NIST SP 800-207 — Zero Trust Architecture",
        "Autre",
        "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-207.pdf",
        "pdf_prose",
        "2020-08-11",
        "2020-08-11",
        "final",
        _CYBER,
        a_verifier=True,
    ),
]

SOURCES: list[SourceReg] = [*_EURLEX, *_ANSSI, *_CNIL, *_ENISA_NIST]


def par_id(identifiant: str) -> SourceReg | None:
    """Retourne la source d'`id` donné, ou None."""
    return next((s for s in SOURCES if s.id == identifiant), None)
