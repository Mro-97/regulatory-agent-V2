"""
Test dédié Bug #7 — top_k appliqué deux fois dans Retriever.retrieve().

Avant le fix, chaque passe Qdrant (A : valid_to présent / dispositions
transitoires — B : valid_to null / dispositions permanentes) demandait
top_k résultats, puis la fusion re-triait par score et coupait à top_k au
global. Si la passe B avait systématiquement un score légèrement supérieur
(ex. dispositions permanentes plus génériques donc plus proches en cosinus
de la question), la passe A pouvait être totalement évincée du résultat
final — alors même qu'elle contient parfois une disposition réglementaire
cruciale (ex. un délai de mise en conformité).

Le fix répartit le budget top_k équitablement entre les deux passes, avec
repêchage si l'une d'elles manque de candidats.
"""

import sys
import types
from types import SimpleNamespace

# Stubs MLX pour environnement non-Apple (les tests n'exécutent pas d'inférence).
for nom in ("mlx", "mlx.core", "mlx_lm", "mlx_lm.sample_utils", "mlx_embeddings"):
    if nom not in sys.modules:
        sys.modules[nom] = types.ModuleType(nom)
sys.modules["mlx.core"].eval = lambda *a, **k: None  # noqa: ARG005 — stub/mock respectant la signature

from datetime import (
    date,
)
from unittest.mock import (
    MagicMock,
)

from src.agents.retriever import (
    Retriever,
)


def _point(point_id: str, score: float, valid_to: str | None = None) -> SimpleNamespace:
    """Fabrique un faux ScoredPoint Qdrant (duck-typing : id/score/payload)."""
    return SimpleNamespace(
        id=point_id,
        score=score,
        payload={
            "chunk_id": point_id,
            "document_id": "DOC",
            "article_id": f"art_{point_id}",
            "texte_chunk": f"Texte du chunk {point_id}",
            "valid_from": "2020-01-01",
            "valid_to": valid_to,
        },
    )


def _retriever(
    passe_a: list[SimpleNamespace], passe_b: list[SimpleNamespace], top_k: int
):
    """Retriever avec client Qdrant mocké : 1er appel = passe A, 2e = passe B."""
    client = MagicMock()
    reponse_a = MagicMock()
    reponse_a.points = passe_a
    reponse_b = MagicMock()
    reponse_b.points = passe_b
    client.query_points.side_effect = [reponse_a, reponse_b]

    r = Retriever(qdrant_client=client, top_k=top_k)
    r.embed_question = lambda q: [0.1] * 1024  # noqa: ARG005 — stub/mock respectant la signature
    return r, client


class TestBug7RepartitionEquilibree:
    def test_passe_a_non_evincee_par_scores_plus_bas(self):
        """
        Passe A (transitoire) a des scores systématiquement plus bas que
        passe B (permanent). Avant le fix, un top-k global pur aurait
        entièrement évincé passe A. Après le fix, elle doit rester
        représentée dans le résultat final.
        """
        passe_a = [
            _point(f"a{i}", score=0.50 - i * 0.01, valid_to="2030-01-01")
            for i in range(4)
        ]
        passe_b = [
            _point(f"b{i}", score=0.90 - i * 0.01, valid_to=None) for i in range(4)
        ]

        r, _ = _retriever(passe_a, passe_b, top_k=4)
        evidences = r.retrieve(question="Q", date_contexte=date(2025, 6, 15))

        chunk_ids = {e.chunk_id for e in evidences}
        assert len(evidences) == 4
        assert any(cid.startswith("a") for cid in chunk_ids), (
            "Passe A totalement évincée malgré des scores plus bas — "
            f"résultat={chunk_ids}"
        )

    def test_total_ne_depasse_jamais_top_k(self):
        passe_a = [
            _point(f"a{i}", score=0.9 - i * 0.01, valid_to="2030-01-01")
            for i in range(10)
        ]
        passe_b = [
            _point(f"b{i}", score=0.8 - i * 0.01, valid_to=None) for i in range(10)
        ]

        r, _ = _retriever(passe_a, passe_b, top_k=5)
        evidences = r.retrieve(question="Q", date_contexte=date(2025, 6, 15))
        assert len(evidences) == 5

    def test_repechage_si_passe_sous_alimentee(self):
        """
        Une seule disposition transitoire disponible (passe A) : le budget
        qui lui était réservé (mais non consommé) doit être repêché depuis
        passe B plutôt que de réduire le total retourné.
        """
        passe_a = [_point("a0", score=0.95, valid_to="2030-01-01")]
        passe_b = [
            _point(f"b{i}", score=0.80 - i * 0.01, valid_to=None) for i in range(10)
        ]

        r, _ = _retriever(passe_a, passe_b, top_k=6)
        evidences = r.retrieve(question="Q", date_contexte=date(2025, 6, 15))

        assert len(evidences) == 6  # aucune perte malgré la sous-alimentation de A
        chunk_ids = {e.chunk_id for e in evidences}
        assert "a0" in chunk_ids

    def test_resultat_final_trie_par_score_decroissant(self):
        passe_a = [_point("a0", score=0.60, valid_to="2030-01-01")]
        passe_b = [
            _point("b0", score=0.95, valid_to=None),
            _point("b1", score=0.70, valid_to=None),
        ]

        r, _ = _retriever(passe_a, passe_b, top_k=3)
        evidences = r.retrieve(question="Q", date_contexte=date(2025, 6, 15))

        scores = [e.score_similarite for e in evidences]
        assert scores == sorted(scores, reverse=True)

    def test_deux_passes_vides_retourne_liste_vide(self):
        r, _ = _retriever([], [], top_k=5)
        evidences = r.retrieve(question="Q", date_contexte=date(2025, 6, 15))
        assert evidences == []

    def test_chaque_passe_interrogee_avec_budget_top_k_complet(self):
        """
        Le sur-échantillonnage par passe (limite=top_k) doit être préservé :
        c'est ce qui permet le repêchage sans jamais perdre de candidat.
        """
        r, client = _retriever([], [], top_k=7)
        r.retrieve(question="Q", date_contexte=date(2025, 6, 15))

        for appel in client.query_points.call_args_list:
            assert appel.kwargs["limit"] == 7
