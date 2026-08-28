"""Test dédié Bug #3 — les filtres API atteignent bien Qdrant."""

import sys
import types

# Stubs MLX pour environnement non-Apple (les tests n'exécutent pas d'inférence).
for nom in ("mlx", "mlx.core", "mlx_lm", "mlx_lm.sample_utils", "mlx_embeddings"):
    if nom not in sys.modules:
        sys.modules[nom] = types.ModuleType(nom)
sys.modules["mlx.core"].eval = lambda *a, **k: None  # noqa: ARG005 — stub/mock respectant la signature

from datetime import date  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)
from unittest.mock import MagicMock  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)

import pytest  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)
from src.agents.retriever import Retriever  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)
from src.models import SourceReglementaire  # noqa: E402 — stubs MLX doivent précéder les imports du projet (sinon ImportError)


@pytest.fixture
def retriever_mock():
    client = MagicMock()
    reponse = MagicMock()
    reponse.points = []
    client.query_points.return_value = reponse

    r = Retriever(qdrant_client=client, top_k=5)
    r.embed_question = lambda q: [0.1] * 1024  # noqa: ARG005 — stub/mock respectant la signature
    return r, client


class TestBug3FiltresPropages:
    @staticmethod
    def _cles(filtre) -> list[str]:
        """Récupère les 'key' des FieldCondition en ignorant les IsNullCondition."""
        return [getattr(c, "key", None) for c in filtre.must if getattr(c, "key", None)]

    def test_themes_et_sources_atteignent_qdrant(self, retriever_mock):
        r, client = retriever_mock
        r.retrieve(
            question="Obligations RGPD ?",
            date_contexte=date(2025, 6, 15),
            filtres_themes=["protection_donnees", "cybersecurite"],
            filtres_sources=[SourceReglementaire.ANSSI, SourceReglementaire.CNIL],
        )
        assert client.query_points.call_count == 2
        for appel in client.query_points.call_args_list:
            cles = self._cles(appel.kwargs["query_filter"])
            assert "themes" in cles, f"Filtre thèmes absent, cles={cles}"
            assert "source" in cles, f"Filtre source absent, cles={cles}"

    def test_aucun_filtre_si_listes_vides(self, retriever_mock):
        r, client = retriever_mock
        r.retrieve(
            question="Q",
            date_contexte=date(2025, 6, 15),
            filtres_themes=[],
            filtres_sources=[],
        )
        for appel in client.query_points.call_args_list:
            cles = self._cles(appel.kwargs["query_filter"])
            assert "themes" not in cles
            assert "source" not in cles

    def test_signature_none_par_defaut(self, retriever_mock):
        """Rétrocompat : appel sans filtres."""
        r, _ = retriever_mock
        r.retrieve(question="Test", date_contexte=date(2025, 6, 15))

    def test_seulement_themes(self, retriever_mock):
        r, client = retriever_mock
        r.retrieve(
            question="Q",
            date_contexte=date(2025, 6, 15),
            filtres_themes=["cybersecurite"],
            filtres_sources=None,
        )
        for appel in client.query_points.call_args_list:
            cles = self._cles(appel.kwargs["query_filter"])
            assert "themes" in cles
            assert "source" not in cles

    def test_valeur_du_filtre_themes(self, retriever_mock):
        """Vérifie que MatchAny reçoit bien les valeurs demandées."""
        r, client = retriever_mock
        r.retrieve(
            question="Q",
            date_contexte=date(2025, 6, 15),
            filtres_themes=["cybersecurite", "sante_securite"],
        )
        appel = client.query_points.call_args_list[0]
        for cond in appel.kwargs["query_filter"].must:
            if getattr(cond, "key", None) == "themes":
                assert cond.match.any == ["cybersecurite", "sante_securite"]
                return
        pytest.fail("Condition sur 'themes' introuvable")
