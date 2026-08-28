"""tests/test_integration_audit.py — Tests d'intégration de l'audit trail.

Extraits de tests/test_integration.py (§12 étape 6). Regroupe les tests
autour de la chaîne d'audit JSONL/PostgreSQL, séparés du pipeline
retrieval/agent pour respecter le seuil §10 (≤ 400 lignes).
"""

from __future__ import annotations

import asyncio

from src.models import NiveauConfiance


class TestAuditTrail:
    def test_persistance_locale(self, tmp_path):  # noqa: ANN001, ANN201
        """Vérifie que l'audit JSONL est bien écrit localement."""
        import src.audit as audit_module
        from src.models import EnregistrementAudit

        # Patch du chemin de fichier
        chemin_test = tmp_path / "audit_test.jsonl"
        original = audit_module.CHEMIN_AUDIT_LOCAL
        audit_module.CHEMIN_AUDIT_LOCAL = chemin_test

        async def _run():  # noqa: ANN202
            gestionnaire = audit_module.GestionnaireAudit(postgres_dsn=None)
            await gestionnaire.initialiser()

            audit = EnregistrementAudit(
                user_query="Question de test",
                reponse_finale="Réponse de test",
                niveau_confiance=NiveauConfiance.ELEVE,
            )
            hash_retourne = await gestionnaire.persister(audit)
            assert len(hash_retourne) == 64

            # Vérifier le fichier
            assert chemin_test.exists()
            contenu = chemin_test.read_text()
            assert "Question de test" in contenu

        try:
            asyncio.run(_run())
        finally:
            audit_module.CHEMIN_AUDIT_LOCAL = original

    def test_chainaage_hashes(self, tmp_path):  # noqa: ANN001, ANN201
        """Deux audits successifs doivent avoir des hashes chaînés."""
        import src.audit as audit_module
        from src.models import EnregistrementAudit

        chemin_test = tmp_path / "audit_chain.jsonl"
        original = audit_module.CHEMIN_AUDIT_LOCAL
        audit_module.CHEMIN_AUDIT_LOCAL = chemin_test
        audit_module._hash_precedent = None  # reset

        async def _run():  # noqa: ANN202
            gestionnaire = audit_module.GestionnaireAudit(postgres_dsn=None)
            await gestionnaire.initialiser()

            a1 = EnregistrementAudit(user_query="Q1", reponse_finale="R1")
            h1 = await gestionnaire.persister(a1)

            a2 = EnregistrementAudit(user_query="Q2", reponse_finale="R2")
            h2 = await gestionnaire.persister(a2)

            assert h1 != h2
            # Le hash précédent de a2 doit être h1
            assert a2.hash_precedent == h1

        try:
            asyncio.run(_run())
        finally:
            audit_module.CHEMIN_AUDIT_LOCAL = original
            audit_module._hash_precedent = None

    def test_verifier_integrite_chaine_intacte(self, tmp_path):  # noqa: ANN001, ANN201
        """Une chaîne d'audit intacte doit être entièrement valide."""
        import src.audit as audit_module
        from src.models import EnregistrementAudit

        chemin_test = tmp_path / "audit_ok.jsonl"
        original = audit_module.CHEMIN_AUDIT_LOCAL
        audit_module.CHEMIN_AUDIT_LOCAL = chemin_test
        audit_module._hash_precedent = None

        async def _run():  # noqa: ANN202
            gestionnaire = audit_module.GestionnaireAudit(postgres_dsn=None)
            await gestionnaire.initialiser()

            for i in range(3):
                await gestionnaire.persister(
                    EnregistrementAudit(user_query=f"Q{i}", reponse_finale=f"R{i}")
                )

            resultat = await gestionnaire.verifier_integrite()
            assert resultat["total"] == 3
            assert resultat["valides"] == 3
            assert resultat["invalides"] == 0
            assert resultat["erreurs"] == []

        try:
            asyncio.run(_run())
        finally:
            audit_module.CHEMIN_AUDIT_LOCAL = original
            audit_module._hash_precedent = None

    def test_verifier_integrite_detecte_enregistrement_supprime(self, tmp_path):  # noqa: ANN001, ANN201
        """
        Un enregistrement retiré du milieu du fichier JSONL doit être détecté :
        les deux enregistrements restants sont chacun auto-cohérents (leur
        propre hash_courant reste correct), mais le hash_precedent du
        troisième ne correspond plus au hash_courant du premier une fois
        le deuxième supprimé — la liaison de chaîne est rompue.
        """
        import src.audit as audit_module
        from src.models import EnregistrementAudit

        chemin_test = tmp_path / "audit_trafique.jsonl"
        original = audit_module.CHEMIN_AUDIT_LOCAL
        audit_module.CHEMIN_AUDIT_LOCAL = chemin_test
        audit_module._hash_precedent = None

        async def _run():  # noqa: ANN202
            gestionnaire = audit_module.GestionnaireAudit(postgres_dsn=None)
            await gestionnaire.initialiser()

            for i in range(3):
                await gestionnaire.persister(
                    EnregistrementAudit(user_query=f"Q{i}", reponse_finale=f"R{i}")
                )

            # Suppression du deuxième enregistrement (falsification simulée).
            lignes = chemin_test.read_text(encoding="utf-8").strip().splitlines()
            assert len(lignes) == 3
            lignes_trafiquees = [lignes[0], lignes[2]]
            chemin_test.write_text(
                "\n".join(lignes_trafiquees) + "\n", encoding="utf-8"
            )

            resultat = await gestionnaire.verifier_integrite()
            assert resultat["total"] == 2
            assert resultat["invalides"] == 1
            assert resultat["erreurs"][0]["type"] == "chaine_rompue"

        try:
            asyncio.run(_run())
        finally:
            audit_module.CHEMIN_AUDIT_LOCAL = original

    def test_desynchronisation_postgres_comptabilisee(self, tmp_path):  # noqa: ANN001, ANN201
        """
        Si PostgreSQL est censé être actif mais que l'INSERT échoue, la
        persistance locale (source de vérité) doit rester intacte, et
        l'échec doit être comptabilisé et exposé via statut() — plus
        jamais silencieusement ignoré comme auparavant.
        """
        import src.audit as audit_module
        from src.models import EnregistrementAudit

        chemin_test = tmp_path / "audit_desync.jsonl"
        original = audit_module.CHEMIN_AUDIT_LOCAL
        audit_module.CHEMIN_AUDIT_LOCAL = chemin_test
        audit_module._hash_precedent = None

        class FausseAcquisition:
            async def __aenter__(self):  # noqa: ANN204
                raise RuntimeError("connexion PostgreSQL indisponible")  # noqa: TRY003

            async def __aexit__(self, *args):  # noqa: ANN002, ANN204
                return False

        class FauxPool:
            def acquire(self):  # noqa: ANN202
                return FausseAcquisition()

        async def _run():  # noqa: ANN202
            gestionnaire = audit_module.GestionnaireAudit(postgres_dsn=None)
            await gestionnaire.initialiser()
            # Simule un PostgreSQL déclaré actif dont l'INSERT échoue.
            gestionnaire._postgres_ok = True
            gestionnaire._pool = FauxPool()

            audit = EnregistrementAudit(user_query="Q", reponse_finale="R")
            hash_retourne = await gestionnaire.persister(audit)

            # La persistance locale a réussi malgré l'échec PostgreSQL.
            assert chemin_test.exists()
            assert "Q" in chemin_test.read_text()
            assert len(hash_retourne) == 64

            statut = gestionnaire.statut()
            assert statut["postgres_actif"] is True
            assert statut["desynchronisations"] == 1

        try:
            asyncio.run(_run())
        finally:
            audit_module.CHEMIN_AUDIT_LOCAL = original
            audit_module._hash_precedent = None

    def test_health_expose_statut_audit(self):  # noqa: ANN201
        """L'endpoint /health doit exposer le statut de synchronisation de l'audit."""
        from fastapi.testclient import TestClient
        from src import api as api_module

        client = TestClient(api_module.app)
        rep = client.get("/health")
        assert rep.status_code == 200
        donnees = rep.json()
        assert "audit" in donnees
        assert "postgres_actif" in donnees["audit"]
        assert "desynchronisations" in donnees["audit"]
