"""tests/test_citation_llm_ancrage.py — M7/L6 de l'extraction LLM des citations.

M7 : l'extrait d'une citation LLM était recopié du chunk vérifié
(`ev.texte_extrait[:200]`), donc la vérification `extrait in chunk` était
tautologique. L'extrait doit venir d'une phrase de la RÉPONSE réellement
retrouvée dans le chunk ; sinon la citation part en DOUTEUSE.

L6 : une sortie LLM dont aucun token n'est un `chunk_id` connu (et qui n'est
pas « AUCUN ») doit déclencher le repli déterministe (None).
"""

from __future__ import annotations

from datetime import date

from src.agents.citation import AgentCitation
from src.agents.citation_llm import (
    _citation_depuis_evidence_llm,
    _parser_citations_llm,
)
from src.models import EvidenceRecuperee


def _ev(chunk_id: str, texte: str) -> EvidenceRecuperee:
    return EvidenceRecuperee(
        chunk_id=chunk_id,
        document_id="RGPD_2016_679",
        article_id="art_33",
        texte_extrait=texte,
        valid_from=date(2018, 5, 25),
    )


class TestParsingChunkIds:
    def test_chunk_id_connu_retenu(self) -> None:
        ev = _ev("chunk_1", "texte")
        assert _parser_citations_llm("chunk_1", [ev]) == [ev]

    def test_aucun_explicite_renvoie_liste_vide(self) -> None:
        ev = _ev("chunk_1", "texte")
        assert _parser_citations_llm("AUCUN", [ev]) == []
        assert _parser_citations_llm("Aucun chunk cité.", [ev]) == []

    def test_format_inattendu_renvoie_none(self) -> None:
        ev = _ev("chunk_1", "texte")
        assert _parser_citations_llm("je ne sais pas", [ev]) is None

    def test_chunk_id_inconnu_seul_renvoie_none(self) -> None:
        ev = _ev("chunk_1", "texte")
        assert _parser_citations_llm("chunk_42", [ev]) is None

    def test_melange_garde_les_connus(self) -> None:
        ev = _ev("chunk_1", "texte")
        assert _parser_citations_llm("chunk_1, chunk_42", [ev]) == [ev]

    def test_separateurs_multiples(self) -> None:
        ev_a = _ev("chunk_1", "a")
        ev_b = _ev("chunk_2", "b")
        assert _parser_citations_llm("chunk_1\nchunk_2", [ev_a, ev_b]) == [ev_a, ev_b]


class TestAncrageCitationLlm:
    def test_extrait_vient_de_la_reponse_et_reste_verifie(self) -> None:
        ev = _ev(
            "chunk_1",
            "Article 33. Le responsable doit notifier la violation dans les "
            "72 heures. Toute notification est documentée.",
        )
        reponse = (
            "1) Réponse directe : Le responsable doit notifier la violation dans "
            "les 72 heures. "
            "2) Détails : délai impératif."
        )
        citation = _citation_depuis_evidence_llm(ev, reponse)
        assert citation.extrait in reponse
        assert citation.extrait != ev.texte_extrait
        verifiees, douteuses = AgentCitation(use_llm=False).verify([citation], [ev])
        assert len(verifiees) == 1
        assert douteuses == []

    def test_chunk_id_au_hasard_part_en_douteuse(self) -> None:
        ev = _ev(
            "chunk_1",
            "Le responsable doit mettre en œuvre des mesures appropriées.",
        )
        reponse = "Le sous-traitant peut refuser toute mesure. Voir la source."
        citation = _citation_depuis_evidence_llm(ev, reponse)
        assert citation.extrait not in ev.texte_extrait
        verifiees, douteuses = AgentCitation(use_llm=False).verify([citation], [ev])
        assert verifiees == []
        assert len(douteuses) == 1
