"""tests/test_sauvegarde.py — sauvegarde Qdrant et Redis (O-4).

Le script est un outil d'exploitation : il n'est pas exécuté par la suite
(pas de Qdrant ni de Redis garantis), mais sa logique de décision l'est —
notamment le ménage, qui supprime des fichiers et doit donc être vérifié
ligne à ligne.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.sauvegarde as sauvegarde


class FauxSnapshot:
    """Snapshot Qdrant minimal, tel que `list_snapshots` le renvoie."""

    def __init__(self, nom: str, creation: str, taille: int = 1_000_000) -> None:
        self.name = nom
        self.creation_time = creation
        self.size = taille


class FauxClientQdrant:
    """Client Qdrant en mémoire : enregistre les appels reçus.

    Les arguments sont conservés (et non ignorés) : c'est ce qui permet de
    vérifier que le script agit bien sur `cfg.qdrant_collection` et non sur un
    nom codé en dur.
    """

    def __init__(self, snapshots: list[FauxSnapshot]) -> None:
        self.snapshots = snapshots
        self.supprimes: list[tuple[str, str]] = []
        self.crees: list[str] = []
        self.restaures: list[tuple[str, str]] = []

    def list_snapshots(self, collection_name: str) -> list[FauxSnapshot]:
        self.collection_lue = collection_name
        return list(self.snapshots)

    def delete_snapshot(self, collection_name: str, snapshot_name: str) -> None:
        self.supprimes.append((collection_name, snapshot_name))

    def create_snapshot(self, collection_name: str, wait: bool) -> FauxSnapshot:
        assert wait is True, "un snapshot sans wait=True peut être incomplet"
        self.crees.append(collection_name)
        return FauxSnapshot("nouveau.snapshot", "2026-09-18T00:00:00")

    def recover_snapshot(self, collection_name: str, location: str, wait: bool) -> None:
        assert wait is True, "une restauration sans wait=True peut être partielle"
        self.restaures.append((collection_name, location))


class TestPurge:
    def test_garde_les_plus_recents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Avec 4 snapshots et `garder=2`, les 2 plus anciens sont supprimés."""
        client = FauxClientQdrant(
            [
                FauxSnapshot("vieux.snapshot", "2026-09-01T00:00:00"),
                FauxSnapshot("ancien.snapshot", "2026-09-05T00:00:00"),
                FauxSnapshot("recent.snapshot", "2026-09-10T00:00:00"),
                FauxSnapshot("neuf.snapshot", "2026-09-15T00:00:00"),
            ]
        )
        monkeypatch.setattr(sauvegarde, "_client_qdrant", lambda: client)
        assert sauvegarde.purger(garder=2) == 0
        noms = [nom for _, nom in client.supprimes]
        assert noms == ["vieux.snapshot", "ancien.snapshot"]

    def test_ne_supprime_rien_si_sous_le_seuil(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Moins de snapshots que le seuil : aucune suppression (le cas sûr)."""
        seul = FauxSnapshot("seul.snapshot", "2026-09-15T00:00:00")
        client = FauxClientQdrant([seul])
        monkeypatch.setattr(sauvegarde, "_client_qdrant", lambda: client)
        assert sauvegarde.purger(garder=2) == 0
        assert client.supprimes == []

    def test_garder_zero_supprime_tout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`--garder 0` est une purge complète, et doit le rester explicitement."""
        client = FauxClientQdrant(
            [
                FauxSnapshot("a.snapshot", "2026-09-01T00:00:00"),
                FauxSnapshot("b.snapshot", "2026-09-02T00:00:00"),
            ]
        )
        monkeypatch.setattr(sauvegarde, "_client_qdrant", lambda: client)
        sauvegarde.purger(garder=0)
        noms = [nom for _, nom in client.supprimes]
        assert noms == ["a.snapshot", "b.snapshot"]


class TestSauvegardeQdrant:
    def test_cree_un_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un snapshot est demandé à Qdrant, et son nom est rendu."""
        client = FauxClientQdrant([])
        monkeypatch.setattr(sauvegarde, "_client_qdrant", lambda: client)
        chemin = sauvegarde._sauvegarder_qdrant()
        assert client.crees == [sauvegarde.cfg.qdrant_collection]
        assert chemin.name == "nouveau.snapshot"


class TestRestauration:
    def test_snapshot_absent_refuse(self) -> None:
        """Un chemin inexistant échoue AVANT tout appel à Qdrant."""
        with pytest.raises(SystemExit):
            sauvegarde.restaurer_chemin("/inexistant/absolument.snapshot")

    def test_restauration_appelle_recover_snapshot(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """`recover_snapshot` est bien la méthode employée (nom réel de l'API)."""
        fichier = tmp_path / "archive.snapshot"
        fichier.write_bytes(b"contenu factice")
        client = FauxClientQdrant([])
        monkeypatch.setattr(sauvegarde, "_client_qdrant", lambda: client)
        assert sauvegarde.restaurer_chemin(str(fichier)) == 0
        assert client.restaures == [(sauvegarde.cfg.qdrant_collection, str(fichier))]


class TestDimension:
    def test_vector_params_simple(self) -> None:
        """Collection mono-vecteur : `.size` est lu directement."""
        assert sauvegarde._dimension(SimpleNamespace(size=1024)) == 1024

    def test_vecteurs_nommes(self) -> None:
        """Collection à vecteurs nommés : un dictionnaire nom -> taille."""
        vecteurs = {
            "texte": SimpleNamespace(size=1024),
            "image": SimpleNamespace(size=512),
        }
        attendu = {"texte": 1024, "image": 512}
        assert sauvegarde._dimension(vecteurs) == attendu

    def test_forme_inconnue(self) -> None:
        """Une config inhabituelle ne doit pas lever, seulement signaler '?'."""
        assert sauvegarde._dimension(None) == "?"


class TestExportHorsMachine:
    """`--exporter` : sans copie hors machine, une panne disque emporte tout."""

    @staticmethod
    def _preparer(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, taille: int
    ) -> FauxClientQdrant:
        """Client factice + dossier de snapshots local, sans réseau."""
        client = FauxClientQdrant(
            [FauxSnapshot("recent.snapshot", "2026-09-21T00:00:00", taille)]
        )
        monkeypatch.setattr(sauvegarde, "_client_qdrant", lambda: client)
        monkeypatch.setattr(sauvegarde, "DOSSIER_SNAPSHOTS", tmp_path / "snapshots")
        (tmp_path / "snapshots").mkdir()
        (tmp_path / "snapshots" / "redis-2026-09-21-160000.rdb").write_bytes(b"rdb")
        return client

    def test_exporte_snapshot_et_dump(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Le snapshot Qdrant et le dump Redis arrivent dans la destination."""
        self._preparer(monkeypatch, tmp_path, taille=12)
        destination = tmp_path / "hors-machine"
        destination.mkdir()

        def _faux_telechargement(_nom: str, cible: Path) -> Path:
            cible.write_bytes(b"x" * 12)
            return cible

        monkeypatch.setattr(sauvegarde, "_telecharger_snapshot", _faux_telechargement)
        assert sauvegarde.exporter(str(destination)) == 0
        assert (destination / "recent.snapshot").stat().st_size == 12
        assert (destination / "redis-2026-09-21-160000.rdb").read_bytes() == b"rdb"

    def test_destination_absente_refuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un montage absent échoue AVANT tout appel à Qdrant."""
        monkeypatch.setattr(
            sauvegarde,
            "_client_qdrant",
            lambda: pytest.fail("Qdrant ne doit pas être contacté"),
        )
        with pytest.raises(SystemExit):
            sauvegarde.exporter("/inexistant/absolument")

    def test_aucun_snapshot_refuse(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Sans snapshot Qdrant, l'export ne peut pas être complet."""
        monkeypatch.setattr(sauvegarde, "_client_qdrant", lambda: FauxClientQdrant([]))
        with pytest.raises(SystemExit):
            sauvegarde.exporter(str(tmp_path))

    def test_export_tronque_refuse(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Un snapshot de taille inattendue est signalé, pas accepté en silence."""
        self._preparer(monkeypatch, tmp_path, taille=12)
        destination = tmp_path / "hors-machine"
        destination.mkdir()

        def _telechargement_tronque(_nom: str, cible: Path) -> Path:
            cible.write_bytes(b"x" * 5)
            return cible

        monkeypatch.setattr(
            sauvegarde, "_telecharger_snapshot", _telechargement_tronque
        )
        with pytest.raises(SystemExit):
            sauvegarde.exporter(str(destination))
