"""
tests/test_bug2_audit_ancrage.py — B2

`GestionnaireAudit.verifier_integrite(limite=N)` lit les N dernières lignes
du fichier JSONL et vérifie la chaîne SHA-256 entre elles. Le premier
enregistrement DE LA FENÊTRE n'est pas contrôlé côté liaison, car son
prédécesseur peut être hors fenêtre — la ligne d'ancrage réelle n'est
jamais lue. Un bloc auto-cohérent inséré au début de la fenêtre passe
donc inaperçu.
"""

from __future__ import annotations

import asyncio


class TestAncrageHorsFenetre:
    def test_bloc_auto_coherent_en_tete_de_fenetre_est_detecte(self, tmp_path):
        """
        Un attaquant remplace le milieu et la fin du fichier par un bloc
        auto-cohérent dont l'ancrage (hash_precedent du premier) ne
        correspond PAS au hash_courant du dernier enregistrement réel qui
        le précède. Avec une fenêtre bornée à 2 lignes, la vérification
        actuelle passe : elle ne compare pas la première ligne lue à son
        vrai prédécesseur. Doit être détecté comme `chaine_rompue`.
        """
        import src.audit as audit_module
        from src.models import EnregistrementAudit

        chemin_test = tmp_path / "audit_ancrage.jsonl"
        original = audit_module.CHEMIN_AUDIT_LOCAL
        audit_module.CHEMIN_AUDIT_LOCAL = chemin_test
        audit_module._hash_precedent = None

        async def _run():
            gestionnaire = audit_module.GestionnaireAudit(postgres_dsn=None)
            await gestionnaire.initialiser()

            # 3 enregistrements réels, chaînés proprement.
            for i in range(3):
                await gestionnaire.persister(
                    EnregistrementAudit(user_query=f"Q{i}", reponse_finale=f"R{i}")
                )

            # Deux enregistrements fabriqués, auto-cohérents entre eux,
            # dont l'ancrage ne correspond à AUCUN hash_courant réel.
            fake2 = EnregistrementAudit(user_query="FAKE2", reponse_finale="FAKE2")
            fake2.hash_precedent = "0" * 64
            fake2.hash_courant = fake2.calculer_hash()

            fake3 = EnregistrementAudit(user_query="FAKE3", reponse_finale="FAKE3")
            fake3.hash_precedent = fake2.hash_courant
            fake3.hash_courant = fake3.calculer_hash()

            # Remplacer les 2 derniers enregistrements réels par les fabriqués.
            lignes = chemin_test.read_text(encoding="utf-8").strip().splitlines()
            assert len(lignes) == 3
            nouvelles = [
                lignes[0],
                fake2.model_dump_json(),
                fake3.model_dump_json(),
            ]
            chemin_test.write_text("\n".join(nouvelles) + "\n", encoding="utf-8")

            # Fenêtre = 2 dernières lignes = fake2 + fake3.
            # Ancrage réel (ligne 0 réelle) DOIT être lu pour valider fake2.
            resultat = await gestionnaire.verifier_integrite(limite=2)
            assert resultat["invalides"] >= 1, (
                f"Falsification hors fenêtre non détectée: {resultat}"
            )
            assert any(e.get("type") == "chaine_rompue" for e in resultat["erreurs"]), (
                f"Type d'erreur attendu 'chaine_rompue', reçu: {resultat['erreurs']}"
            )

        try:
            asyncio.run(_run())
        finally:
            audit_module.CHEMIN_AUDIT_LOCAL = original
            audit_module._hash_precedent = None
