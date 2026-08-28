"""
Test dédié Bug #6 — vérification de citation insensible aux reformatages
purement typographiques (espaces multiples, retours à la ligne, guillemets
courbes vs droits) introduits par le LLM (Mistral 7B) en mode extraction.

Avant le fix : `cit.extrait.strip() not in chunk.texte_extrait` comparait
littéralement les deux chaînes. Un extrait valide légèrement reformaté par
le LLM (double espace, retour à la ligne, guillemets « » au lieu de " ")
était marqué DOUTEUSE alors qu'il correspondait bien au texte source.
"""

from datetime import date

from src.agents.citation import (
    AgentCitation,
    CitationReglementaire,
    StatutCitation,
    _normaliser_pour_comparaison,
)
from src.models import EvidenceRecuperee


def _ev(texte: str) -> EvidenceRecuperee:
    return EvidenceRecuperee(
        chunk_id="chunk_001",
        document_id="RGPD_2016_679",
        article_id="art_32",
        texte_extrait=texte,
        valid_from=date(2018, 5, 25),
        valid_to=None,
    )


def _cit(extrait: str, chunk_id: str = "chunk_001") -> CitationReglementaire:
    return CitationReglementaire(
        document_id="RGPD_2016_679",
        article_id="art_32",
        valid_from=date(2018, 5, 25),
        valid_to=None,
        extrait=extrait,
        chunk_id=chunk_id,
    )


class TestNormaliserPourComparaison:
    def test_espaces_multiples_collabses(self):
        assert (
            _normaliser_pour_comparaison("Le  responsable   doit agir")
            == "Le responsable doit agir"
        )

    def test_retours_a_la_ligne_normalises(self):
        assert (
            _normaliser_pour_comparaison("Le responsable\ndoit\tagir")
            == "Le responsable doit agir"
        )

    def test_guillemets_typographiques_normalises(self):
        assert (
            _normaliser_pour_comparaison("«mesures appropriées»")
            == '"mesures appropriées"'
        )
        assert (
            _normaliser_pour_comparaison("‘test’ et “texte”") == "'test' et \"texte\""  # noqa: RUF001 — caractère typographique français légitime
        )

    def test_espaces_peripheriques_retires(self):
        assert _normaliser_pour_comparaison("  texte  ") == "texte"


class TestBug6CitationReformatee:
    """
    Régression du Bug #6 : un extrait sémantiquement identique au texte
    source, mais reformaté par le LLM, doit rester VERIFIEE.
    """

    def test_double_espace_reste_verifiee(self):
        agent = AgentCitation(use_llm=False)
        source = _ev("Le responsable doit mettre en œuvre des mesures appropriées.")
        cit = _cit("Le responsable  doit mettre en œuvre des mesures appropriées.")

        verifiees, douteuses = agent.verify([cit], [source])
        assert len(verifiees) == 1
        assert len(douteuses) == 0
        assert verifiees[0].statut == StatutCitation.VERIFIEE

    def test_retour_a_la_ligne_reste_verifiee(self):
        agent = AgentCitation(use_llm=False)
        source = _ev("Le responsable doit notifier la violation dans les 72 heures.")
        cit = _cit("Le responsable doit notifier\nla violation dans les 72 heures.")

        verifiees, douteuses = agent.verify([cit], [source])
        assert len(verifiees) == 1
        assert len(douteuses) == 0

    def test_guillemets_typographiques_reste_verifiee(self):
        agent = AgentCitation(use_llm=False)
        source = _ev(
            'Le texte prévoit des "mesures appropriées" au sens de l\'article.'
        )
        cit = _cit("Le texte prévoit des «mesures appropriées» au sens de l'article.")

        verifiees, douteuses = agent.verify([cit], [source])
        assert len(verifiees) == 1
        assert len(douteuses) == 0

    def test_extrait_reellement_absent_toujours_douteuse(self):
        """Le fix ne doit pas masquer une vraie divergence de contenu."""
        agent = AgentCitation(use_llm=False)
        source = _ev("Le responsable doit mettre en œuvre des mesures appropriées.")
        cit = _cit("Le sous-traitant peut refuser toute mesure.")

        verifiees, douteuses = agent.verify([cit], [source])
        assert len(verifiees) == 0
        assert len(douteuses) == 1
        assert douteuses[0].statut == StatutCitation.DOUTEUSE
