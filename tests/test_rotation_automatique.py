"""tests/test_rotation_automatique.py — révocation périodique des clés `user`.

Distinct de `tests/test_rotation_cles.py` (procédure MANUELLE de bascule S-2) :
ici c'est la politique AUTOMATIQUE demandée le 2026-09-24 — une clé `user` vit
7 jours, une clé `admin` n'est jamais révoquée automatiquement. La nuance est
structurante : une rotation qui emporterait la dernière clé d'administration
rendrait l'API inadministrable, et refuserait même son démarrage (garde-fou de
`valider_configuration_demarrage`).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from config import cfg
from src.rotation_cles import entrees_a_revoquer, rotation

AUJOURDHUI = date(2026, 9, 25)


def _entree(
    label: str, role: str, cree: str, hash_: str | None = None
) -> dict[str, str]:
    return {
        "hash": hash_ or f"{label:x<64}"[:64],
        "role": role,
        "label": label,
        "cree": cree,
    }


class TestEntreesARevoquer:
    def test_user_ancienne_revoquee(self) -> None:
        entrees = [_entree("vieille", "user", "2026-09-01")]
        assert entrees_a_revoquer(entrees, 7, AUJOURDHUI) == entrees

    def test_user_recente_conservee(self) -> None:
        entrees = [_entree("neuve", "user", "2026-09-24")]
        assert entrees_a_revoquer(entrees, 7, AUJOURDHUI) == []

    def test_limite_exacte_conservee(self) -> None:
        """À 7 jours pile la clé vit encore ; à 8 jours elle part."""
        assert (
            entrees_a_revoquer([_entree("pile", "user", "2026-09-18")], 7, AUJOURDHUI)
            == []
        )
        assert entrees_a_revoquer(
            [_entree("juste", "user", "2026-09-17")], 7, AUJOURDHUI
        )

    @pytest.mark.parametrize("role", ["admin", "validateur"])
    def test_jamais_les_roles_superieurs(self, role: str) -> None:
        """Même très ancienne, une clé admin ou validateur n'est pas touchée."""
        entrees = [_entree("haute", role, "2020-01-01")]
        assert entrees_a_revoquer(entrees, 7, AUJOURDHUI) == []

    def test_date_illisible_conservee(self) -> None:
        """On ne révoque pas sur une donnée qu'on ne comprend pas."""
        entrees = [_entree("sans-date", "user", ""), _entree("cassee", "user", "hier")]
        assert entrees_a_revoquer(entrees, 7, AUJOURDHUI) == []

    def test_role_inconnu_conserve(self) -> None:
        entrees = [_entree("etrange", "superuser", "2020-01-01")]
        assert entrees_a_revoquer(entrees, 7, AUJOURDHUI) == []


class TestRotationSurFichier:
    @pytest.fixture
    def magasin(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        chemin = tmp_path / "api_keys.json"
        monkeypatch.setattr(cfg, "api_keys_file", chemin)
        return chemin

    def test_ecrit_le_magasin_sans_les_cles_revoquees(self, magasin: Path) -> None:
        entrees = [
            _entree("vieille", "user", "2026-09-01", "a" * 64),
            _entree("admin-prod", "admin", "2026-01-01", "b" * 64),
            _entree("neuve", "user", "2026-09-24", "c" * 64),
        ]
        magasin.write_text(json.dumps({"keys": entrees}), encoding="utf-8")

        revoquees = rotation(7, aujourdhui=AUJOURDHUI)

        assert [entree["label"] for entree in revoquees] == ["vieille"]
        restantes = json.loads(magasin.read_text(encoding="utf-8"))["keys"]
        assert {entree["label"] for entree in restantes} == {"admin-prod", "neuve"}

    def test_aucune_revocation_n_ecrit_pas(self, magasin: Path) -> None:
        """Un magasin inchangé ne doit pas voir son `mtime` bouger.

        L'API invalide son cache de clés par signature (mtime, taille) :
        réécrire pour rien ferait relire le fichier à chaque cycle.
        """
        magasin.write_text(
            json.dumps({"keys": [_entree("admin-prod", "admin", "2026-01-01")]}),
            encoding="utf-8",
        )
        avant = magasin.stat().st_mtime_ns

        assert rotation(7, aujourdhui=AUJOURDHUI) == []
        assert magasin.stat().st_mtime_ns == avant

    def test_duree_nulle_desactive(self, magasin: Path) -> None:
        magasin.write_text(
            json.dumps({"keys": [_entree("vieille", "user", "2020-01-01")]}),
            encoding="utf-8",
        )
        assert rotation(0, aujourdhui=AUJOURDHUI) == []
        assert len(json.loads(magasin.read_text(encoding="utf-8"))["keys"]) == 1

    def test_fichier_absent_ne_leve_pas(self, magasin: Path) -> None:
        assert rotation(7, aujourdhui=AUJOURDHUI) == []
        assert not magasin.exists(), "rien à écrire sans magasin"

    def test_magasin_corrompu_ne_leve_pas(self, magasin: Path) -> None:
        magasin.write_text("{pas du json", encoding="utf-8")
        assert rotation(7, aujourdhui=AUJOURDHUI) == []
