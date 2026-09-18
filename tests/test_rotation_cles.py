"""tests/test_rotation_cles.py — S-2 : le cycle de rotation doit être sûr.

Une rotation se fait en deux temps : on ajoute la nouvelle clé, on vérifie
qu'elle fonctionne, PUIS on retire l'ancienne. Ce test verrouille le fait que
les deux clés coexistent pendant la fenêtre de bascule — c'est ce qui permet de
déployer un nouveau client sans coupure.

Attention, propriété importante documentée ici : la révocation est désormais
**immédiate** (cf. `_invalider_si_fichier_change`). Il n'existe donc AUCUN délai
de grâce : un client qui utilise encore l'ancienne clé est refusé dès la
révocation. C'est le but recherché en cas d'incident, mais cela impose de
vérifier la nouvelle clé AVANT de révoquer l'ancienne.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import cfg
from src.auth import hacher_cle, identifier, magasin_configure


def _ecrire(chemin: Path, cles: list[tuple[str, str, str]]) -> None:
    """Écrit un `api_keys.json` : liste de (clé, rôle, label)."""
    contenu = [
        {"hash": hacher_cle(cle), "role": role, "label": label}
        for cle, role, label in cles
    ]
    chemin.write_text(json.dumps(contenu), encoding="utf-8")


@pytest.fixture
def magasin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Magasin sur fichier temporaire, voies d'environnement neutralisées."""
    chemin = tmp_path / "api_keys.json"
    monkeypatch.setattr(cfg, "api_keys_file", chemin)
    # Le champ s'écrit « hachees » (deux e) : une faute de frappe ferait échouer
    # le monkeypatch SILENCIEUSEMENT.
    monkeypatch.setattr(cfg, "api_keys_hachees_str", "")
    monkeypatch.setattr(cfg, "api_key", "")
    monkeypatch.setattr(cfg, "api_keys_str", "")
    return chemin


class TestCoexistencePendantLaRotation:
    def test_les_deux_cles_fonctionnent_durant_la_bascule(self, magasin: Path) -> None:
        """Étape 1 : on ajoute la nouvelle SANS retirer l'ancienne."""
        _ecrire(magasin, [("rak_ancienne", "admin", "prod-2026-09")])
        assert identifier("rak_ancienne") is not None

        _ecrire(
            magasin,
            [
                ("rak_ancienne", "admin", "prod-2026-09"),
                ("rak_nouvelle", "admin", "prod-2026-10"),
            ],
        )
        # Les deux répondent : la fenêtre de bascule est utilisable.
        assert identifier("rak_ancienne") is not None
        assert identifier("rak_nouvelle") is not None

    def test_le_role_est_porte_par_chaque_cle(self, magasin: Path) -> None:
        """Une nouvelle clé peut avoir un rôle différent de l'ancienne."""
        _ecrire(
            magasin,
            [
                ("rak_ancienne", "admin", "a"),
                ("rak_nouvelle", "validateur", "n"),
            ],
        )
        ancienne = identifier("rak_ancienne")
        nouvelle = identifier("rak_nouvelle")
        assert ancienne is not None and ancienne[0].name == "ADMIN"
        assert nouvelle is not None and nouvelle[0].name == "VALIDATEUR"

    def test_le_retrait_de_l_ancienne_coupe_immediatement(self, magasin: Path) -> None:
        """Étape 2 : après révocation, l'ancienne clé est refusée sans délai.

        C'est la propriété qui rend la rotation efficace en incident — et qui
        impose de vérifier la nouvelle clé AVANT de révoquer.
        """
        _ecrire(
            magasin,
            [("rak_ancienne", "admin", "a"), ("rak_nouvelle", "admin", "n")],
        )
        _ecrire(magasin, [("rak_nouvelle", "admin", "n")])
        assert identifier("rak_ancienne") is None
        assert identifier("rak_nouvelle") is not None

    def test_rotation_complete_ne_laisse_pas_le_service_sans_cle(
        self, magasin: Path
    ) -> None:
        """À aucun moment le magasin ne doit être vide (sinon 503 pour tous)."""
        _ecrire(magasin, [("rak_1", "admin", "v1")])
        # Bascule : nouvelle clé ajoutée avant le retrait de l'ancienne.
        _ecrire(magasin, [("rak_1", "admin", "v1"), ("rak_2", "admin", "v2")])
        assert magasin_configure() is True
        _ecrire(magasin, [("rak_2", "admin", "v2")])
        assert magasin_configure() is True
        # L'ordre inverse aurait produit un instant sans clé valide :
        _ecrire(magasin, [])
        assert magasin_configure() is False

    def test_cle_inconnue_reste_refusee(self, magasin: Path) -> None:
        """Contrôle négatif : le magasin ne valide pas n'importe quoi."""
        _ecrire(magasin, [("rak_reelle", "user", "r")])
        assert identifier("rak_inexistante") is None
        assert identifier("") is None
        assert identifier(None) is None
