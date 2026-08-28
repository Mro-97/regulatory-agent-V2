"""
tests/test_models.py — Tests unitaires des modèles Pydantic et temporalité
==========================================================================

Couvre :
- IntervalleValidite : bornes incluses, intervalle ouvert, incohérence dates
- VersionArticle : hash auto, applicabilité
- DocumentReglementaire : hash, articles_applicables_a, est_en_vigueur_a
- EnregistrementAudit : chaînage SHA-256
- Quality gates temporels du contexte projet (§ 25)
"""

import hashlib
from datetime import date

import pytest
from src.models import (
    Chapitre,
    DocumentReglementaire,
    EnregistrementAudit,
    EvidenceRecuperee,
    IntervalleValidite,
    NiveauConfiance,
    SourceReglementaire,
    VersionArticle,
)

# ---------------------------------------------------------------------------
# IntervalleValidite
# ---------------------------------------------------------------------------


class TestIntervalleValidite:
    """Tests du modèle de validité temporelle."""

    def test_applicable_date_normale(self):  # noqa: ANN201
        iv = IntervalleValidite(valid_from=date(2018, 5, 25), valid_to=date(2026, 8, 2))
        assert iv.est_applicable_a(date(2025, 6, 15)) is True

    def test_non_applicable_avant_valid_from(self):  # noqa: ANN201
        iv = IntervalleValidite(valid_from=date(2018, 5, 25), valid_to=date(2026, 8, 2))
        assert iv.est_applicable_a(date(2017, 12, 31)) is False

    def test_non_applicable_apres_valid_to(self):  # noqa: ANN201
        iv = IntervalleValidite(valid_from=date(2018, 5, 25), valid_to=date(2026, 8, 2))
        assert iv.est_applicable_a(date(2026, 8, 3)) is False

    def test_borne_inferieure_incluse(self):  # noqa: ANN201
        """valid_from est inclus."""
        iv = IntervalleValidite(valid_from=date(2018, 5, 25), valid_to=date(2026, 8, 2))
        assert iv.est_applicable_a(date(2018, 5, 25)) is True

    def test_borne_superieure_incluse(self):  # noqa: ANN201
        """valid_to est inclus."""
        iv = IntervalleValidite(valid_from=date(2018, 5, 25), valid_to=date(2026, 8, 2))
        assert iv.est_applicable_a(date(2026, 8, 2)) is True

    def test_intervalle_ouvert(self):  # noqa: ANN201
        """valid_to = None signifie en vigueur indéfiniment."""
        iv = IntervalleValidite(valid_from=date(2026, 8, 3))
        assert iv.est_applicable_a(date(2030, 1, 1)) is True
        assert iv.est_ouvert() is True

    def test_intervalle_ouvert_borne_inferieure(self):  # noqa: ANN201
        iv = IntervalleValidite(valid_from=date(2026, 8, 3))
        assert iv.est_applicable_a(date(2026, 8, 2)) is False

    def test_incoherence_dates_leve_erreur(self):  # noqa: ANN201
        """valid_to < valid_from doit lever ValueError."""
        with pytest.raises(ValueError):
            IntervalleValidite(
                valid_from=date(2025, 1, 1),
                valid_to=date(2024, 12, 31),
            )

    def test_dates_egales_valides(self):  # noqa: ANN201
        """valid_from == valid_to : valide un seul jour."""
        iv = IntervalleValidite(
            valid_from=date(2025, 6, 15), valid_to=date(2025, 6, 15)
        )
        assert iv.est_applicable_a(date(2025, 6, 15)) is True
        assert iv.est_applicable_a(date(2025, 6, 16)) is False


# ---------------------------------------------------------------------------
# VersionArticle
# ---------------------------------------------------------------------------


class TestVersionArticle:
    def test_hash_calcule_automatiquement(self):  # noqa: ANN201
        art = VersionArticle(
            id="art_32",
            titre="Sécurité du traitement",
            texte="Compte tenu de l'état des connaissances…",
            validite=IntervalleValidite(valid_from=date(2018, 5, 25)),
        )
        assert art.hash_contenu is not None
        assert len(art.hash_contenu) == 64

    def test_hash_sha256_correct(self):  # noqa: ANN201
        texte = "Texte de test"
        art = VersionArticle(
            id="art_1",
            titre="Test",
            texte=texte,
            validite=IntervalleValidite(valid_from=date(2018, 5, 25)),
        )
        attendu = hashlib.sha256(texte.encode()).hexdigest()
        assert art.hash_contenu == attendu

    def test_est_applicable_a(self):  # noqa: ANN201
        art = VersionArticle(
            id="art_32",
            titre="Test",
            texte="Texte",
            validite=IntervalleValidite(
                valid_from=date(2018, 5, 25),
                valid_to=date(2026, 8, 2),
            ),
        )
        assert art.est_applicable_a(date(2025, 6, 15)) is True
        assert art.est_applicable_a(date(2017, 1, 1)) is False


# ---------------------------------------------------------------------------
# DocumentReglementaire — cas du contexte projet (§ 25)
# ---------------------------------------------------------------------------


@pytest.fixture
def doc_rgpd():  # noqa: ANN201
    """Document RGPD avec deux versions de l'article 32."""
    return DocumentReglementaire(
        id="RGPD_2016_679",
        titre="Règlement (UE) 2016/679",
        source=SourceReglementaire.EUR_LEX,
        publication_date=date(2016, 5, 4),
        entry_into_force=date(2018, 5, 25),
        version="2026-08-03",
        themes=["protection_donnees"],
        chapitres=[
            Chapitre(
                id="chap4",
                articles=[
                    VersionArticle(
                        id="art_32",
                        titre="Sécurité du traitement (version A)",
                        texte="Texte version A",
                        validite=IntervalleValidite(
                            valid_from=date(2018, 5, 25),
                            valid_to=date(2026, 8, 2),
                        ),
                    ),
                    VersionArticle(
                        id="art_32_2026",
                        titre="Sécurité du traitement (version B)",
                        texte="Texte version B",
                        validite=IntervalleValidite(
                            valid_from=date(2026, 8, 3),
                        ),
                    ),
                ],
            )
        ],
    )


class TestDocumentReglementaire:
    def test_articles_applicables_2025(self, doc_rgpd):  # noqa: ANN001, ANN201
        """Question du contexte projet : applicable le 15 juin 2025 → version A."""
        applicables = doc_rgpd.articles_applicables_a(date(2025, 6, 15))
        assert len(applicables) == 1
        assert applicables[0].id == "art_32"

    def test_articles_applicables_2026(self, doc_rgpd):  # noqa: ANN001, ANN201
        """Après le 3 août 2026 → version B."""
        applicables = doc_rgpd.articles_applicables_a(date(2026, 8, 10))
        assert len(applicables) == 1
        assert applicables[0].id == "art_32_2026"

    def test_borne_valid_to(self, doc_rgpd):  # noqa: ANN001, ANN201
        """Le 2 août 2026 → encore version A (borne incluse)."""
        applicables = doc_rgpd.articles_applicables_a(date(2026, 8, 2))
        assert applicables[0].id == "art_32"

    def test_borne_valid_from_version_b(self, doc_rgpd):  # noqa: ANN001, ANN201
        """Le 3 août 2026 exactement → version B."""
        applicables = doc_rgpd.articles_applicables_a(date(2026, 8, 3))
        assert applicables[0].id == "art_32_2026"

    def test_avant_entree_en_vigueur(self, doc_rgpd):  # noqa: ANN001, ANN201
        """Avant 2018-05-25 → aucune version applicable."""
        applicables = doc_rgpd.articles_applicables_a(date(2017, 1, 1))
        assert len(applicables) == 0

    def test_est_en_vigueur_a(self, doc_rgpd):  # noqa: ANN001, ANN201
        assert doc_rgpd.est_en_vigueur_a(date(2025, 6, 15)) is True
        assert doc_rgpd.est_en_vigueur_a(date(2017, 1, 1)) is False

    def test_hash_document_stable(self, doc_rgpd):  # noqa: ANN001, ANN201
        """Le hash doit être stable et ne pas inclure date_indexation."""
        h1 = doc_rgpd.calculer_hash()
        h2 = doc_rgpd.calculer_hash()
        assert h1 == h2
        assert len(h1) == 64

    def test_hash_change_si_contenu_change(self, doc_rgpd):  # noqa: ANN001, ANN201
        h1 = doc_rgpd.calculer_hash()
        doc_rgpd.titre = "Titre modifié"
        h2 = doc_rgpd.calculer_hash()
        assert h1 != h2


# ---------------------------------------------------------------------------
# EnregistrementAudit — chaînage SHA-256
# ---------------------------------------------------------------------------


class TestEnregistrementAudit:
    def test_hash_calcule(self):  # noqa: ANN201
        audit = EnregistrementAudit(
            user_query="Question test",
            reponse_finale="Réponse test",
            niveau_confiance=NiveauConfiance.ELEVE,
        )
        h = audit.calculer_hash()
        assert len(h) == 64

    def test_hash_exclut_hash_courant(self):  # noqa: ANN201
        """Le calcul du hash ne doit pas inclure hash_courant."""
        audit = EnregistrementAudit(user_query="Test", reponse_finale="Rep")
        h1 = audit.calculer_hash()
        audit.hash_courant = "valeur_quelconque"
        h2 = audit.calculer_hash()
        assert h1 == h2

    def test_chainaage_hash_precedent(self):  # noqa: ANN201
        """Le hash précédent modifie le hash courant."""
        audit = EnregistrementAudit(user_query="Test", reponse_finale="Rep")
        h_sans = audit.calculer_hash()

        audit.hash_precedent = "a" * 64
        h_avec = audit.calculer_hash()
        assert h_sans != h_avec

    def test_hashes_differents_pour_requetes_differentes(self):  # noqa: ANN201
        a1 = EnregistrementAudit(user_query="Question A", reponse_finale="Rep A")
        a2 = EnregistrementAudit(user_query="Question B", reponse_finale="Rep B")
        assert a1.calculer_hash() != a2.calculer_hash()


# ---------------------------------------------------------------------------
# Schémas API
# ---------------------------------------------------------------------------


class TestSchemasAPI:
    def test_requete_question_validation(self):  # noqa: ANN201
        from src.models import RequeteQuestion

        rq = RequeteQuestion(question="Quelles sont les obligations RGPD ?")
        assert rq.question.startswith("Quelles")
        assert rq.date_contexte is None
        assert rq.demander_validation_humaine is False

    def test_requete_question_trop_courte(self):  # noqa: ANN201
        from src.models import RequeteQuestion

        with pytest.raises(Exception):  # noqa: B017 - TODO 12 etape 4/6 : revue ciblee au moment du typage / de l extraction
            RequeteQuestion(question="AB")

    def test_evidence_recuperee(self):  # noqa: ANN201
        ev = EvidenceRecuperee(
            chunk_id="c1",
            document_id="RGPD_2016_679",
            article_id="art_32",
            texte_extrait="Texte extrait",
            valid_from=date(2018, 5, 25),
        )
        assert ev.score_similarite is None
        assert ev.valid_to is None
