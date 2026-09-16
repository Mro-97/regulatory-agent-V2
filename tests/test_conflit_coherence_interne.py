"""tests/test_conflit_coherence_interne.py — M4/M5 de l'agent Conflit.

M4 : la passe intra-document doit normaliser les `article_id`
(`art_32_2026` ≡ `art_32`) et vérifier l'activité des deux preuves à
`date_ref`, exactement comme la passe inter-documents.

M5 : `_calculer_niveau_global([])` doit renvoyer AUCUN, pas POTENTIEL.
"""

from __future__ import annotations

from datetime import date

from src.agents.conflit import (
    AgentConflit,
    ConflitDetecte,
    NiveauConflit,
    _calculer_niveau_global,
)
from src.models import EvidenceRecuperee


def _ev(
    article_id: str,
    texte: str,
    valid_from: date,
    valid_to: date | None = None,
    document_id: str = "DOC",
) -> EvidenceRecuperee:
    return EvidenceRecuperee(
        chunk_id=f"{document_id}_{article_id}",
        document_id=document_id,
        article_id=article_id,
        texte_extrait=texte,
        score_similarite=0.9,
        valid_from=valid_from,
        valid_to=valid_to,
    )


class TestNiveauGlobal:
    def test_liste_vide_est_aucun(self) -> None:
        assert _calculer_niveau_global([]) is NiveauConflit.AUCUN

    def test_potentiel_conserve(self) -> None:
        detecte = ConflitDetecte(
            evidence_a=_ev("art_1", "doit", date(2020, 1, 1)),
            evidence_b=_ev("art_2", "ne doit pas", date(2020, 1, 1)),
            niveau=NiveauConflit.POTENTIEL,
            description="tension",
        )
        assert _calculer_niveau_global([detecte]) is NiveauConflit.POTENTIEL


class TestIncoherencesInternes:
    def test_versions_du_meme_article_non_comparees(self) -> None:
        """art_32 et art_32_2026 sont deux versions, pas deux articles."""
        agent = AgentConflit(use_llm=False)
        evidences = [
            _ev("art_32", "Le responsable doit agir.", date(2020, 1, 1)),
            _ev("art_32_2026", "Le responsable ne doit pas agir.", date(2020, 1, 1)),
        ]
        resultat = agent.analyser("Q ?", evidences, date(2025, 1, 1))
        assert resultat.niveau_global is NiveauConflit.AUCUN
        assert resultat.conflits == []

    def test_article_abroge_ne_cree_pas_d_incoherence(self) -> None:
        agent = AgentConflit(use_llm=False)
        evidences = [
            _ev(
                "art_1",
                "Le responsable doit agir.",
                date(2020, 1, 1),
                date(2021, 12, 31),
            ),
            _ev("art_2", "Le responsable ne doit pas agir.", date(2024, 1, 1)),
        ]
        resultat = agent.analyser("Q ?", evidences, date(2025, 1, 1))
        assert resultat.niveau_global is NiveauConflit.AUCUN

    def test_articles_actifs_en_tension_detectes(self) -> None:
        agent = AgentConflit(use_llm=False)
        evidences = [
            _ev("art_1", "Le responsable doit agir.", date(2020, 1, 1)),
            _ev("art_2", "Le responsable ne doit pas agir.", date(2020, 1, 1)),
        ]
        resultat = agent.analyser("Q ?", evidences, date(2025, 1, 1))
        assert resultat.niveau_global is NiveauConflit.POTENTIEL
        assert len(resultat.conflits) == 1
