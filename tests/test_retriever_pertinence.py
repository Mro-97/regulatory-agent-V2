"""tests/test_retriever_pertinence.py — seuil de pertinence et abstention.

`/ask` renvoyait 15 passages sans aucun rapport avec la question, présentés
comme « pertinents » (mesuré : « 123 789 33333 » → 15 chunks, top score
0,68). Le Retriever écarte désormais tout ce qui passe sous
`cfg.qdrant_score_min` ; s'il ne reste rien, l'API s'abstient au lieu de
meubler. La passe « articles cités » est exemptée : un article explicitement
nommé reste pertinent même si son texte fragmenté obtient un score bas
(0,39-0,41 mesurés pour l'article 33 du RGPD).
"""

from __future__ import annotations

import sys
import types
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

# Stubs MLX : les tests n'exécutent aucune inférence.
for nom in ("mlx", "mlx.core", "mlx_lm", "mlx_lm.sample_utils", "mlx_embeddings"):
    if nom not in sys.modules:
        sys.modules[nom] = types.ModuleType(nom)
sys.modules["mlx.core"].eval = lambda *a, **k: None  # noqa: ARG005 — stub

from config import cfg  # noqa: E402 — stubs MLX avant imports projet
from src.agents.retriever import Retriever  # noqa: E402
from src.agents.retriever_helpers import filtrer_par_pertinence  # noqa: E402

SEUIL = cfg.qdrant_score_min


def _point(chunk_id: str, score: float, texte: str, valid_to: str | None = None):  # noqa: ANN202
    """Faux ScoredPoint Qdrant (duck-typing : id/score/payload)."""
    return SimpleNamespace(
        id=chunk_id,
        score=score,
        payload={
            "chunk_id": chunk_id,
            "document_id": "DOC",
            "article_id": f"art_{chunk_id}",
            "texte_chunk": texte,
            "valid_from": "2020-01-01",
            "valid_to": valid_to,
        },
    )


def _retriever(reponses: list[list[SimpleNamespace]], top_k: int = 15) -> Retriever:
    """Retriever dont les passes Qdrant sont servies dans l'ordre demandé."""
    client = MagicMock()
    effets = []
    for points in reponses:
        reponse = MagicMock()
        reponse.points = points
        effets.append(reponse)
    client.query_points.side_effect = effets
    retriever = Retriever(qdrant_client=client, top_k=top_k)
    retriever.embed_question = lambda q: [0.1] * 8  # noqa: ARG005 — stub
    return retriever


class TestFiltrerParPertinence:
    def test_ecarte_sous_le_seuil_et_garde_le_seuil_atteint(self) -> None:
        points = [
            _point("haut", SEUIL + 0.05, "a"),
            _point("pile", SEUIL, "b"),
            _point("bas", SEUIL - 0.01, "c"),
        ]
        gardes = filtrer_par_pertinence(points, SEUIL)
        assert [p.id for p in gardes] == ["haut", "pile"]

    def test_liste_vide(self) -> None:
        assert filtrer_par_pertinence([], SEUIL) == []


class TestAbstention:
    def test_question_hors_corpus_ne_renvoie_rien(self) -> None:
        """Tous les scores sous le seuil : aucun passage, donc abstention."""
        passe_a = [
            _point("a0", SEUIL - 0.05, "texte a"),
            _point("a1", SEUIL - 0.10, "texte b"),
        ]
        passe_b = [_point("b0", SEUIL - 0.20, "texte c")]
        r = _retriever([passe_a, passe_b])
        abstention = r.retrieve(
            question="123 789 33333", date_contexte=date(2025, 6, 15)
        )
        assert abstention == []

    def test_seul_le_surplus_sous_le_seuil_est_ecarte(self) -> None:
        passe_a = [
            _point("a0", SEUIL + 0.08, "texte a"),
            _point("a1", SEUIL - 0.08, "texte b"),
        ]
        passe_b = [
            _point("b0", SEUIL + 0.03, "texte c"),
            _point("b1", SEUIL - 0.30, "texte d"),
        ]
        r = _retriever([passe_a, passe_b])
        evidences = r.retrieve(question="Q", date_contexte=date(2025, 6, 15))
        assert {e.chunk_id for e in evidences} == {"a0", "b0"}


class TestPasseArticlesCitesExemptee:
    def test_article_cite_a_score_bas_survit_au_seuil(self) -> None:
        """Article explicite = correspondance de métadonnées, pas de score."""
        cite = _point("art_33", SEUIL - 0.31, "Notification a l'autorite")
        passe_a = [_point("a0", SEUIL + 0.06, "passage vectoriel")]
        r = _retriever([[cite], passe_a, []])
        evidences = r.retrieve(
            question="Que dit l'article 33 du RGPD ?",
            date_contexte=date(2025, 6, 15),
        )
        assert {e.chunk_id for e in evidences} == {"art_33", "a0"}
        assert evidences[0].chunk_id == "art_33", "l'article cité passe en tête"
