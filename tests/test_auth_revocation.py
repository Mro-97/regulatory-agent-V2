"""tests/test_auth_revocation.py — révocation effective sans redémarrage.

Avant ce correctif, `src/auth.py` mémoïsait le magasin de clés sans jamais
l'invalider : `gerer_cles.py revoquer` affichait « Redémarrer l'API », et une clé
compromise restait acceptée jusqu'au redémarrage — fenêtre d'exposition non
bornée sur un service qui tourne des jours.

L'invalidation est décidée **à l'extérieur** de la fonction mémoïsée (voir
`_invalider_si_fichier_change`) : décidée à l'intérieur, elle n'est jamais
atteinte puisque `lru_cache` rend son résultat sans exécuter le corps.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import cfg
from src.auth import identifier, magasin_configure


def _ecrire(chemin: Path, cles: list[tuple[str, str]]) -> None:
    """Écrit un `api_keys.json` : liste de (clé en clair, rôle)."""
    from src.auth import hacher_cle

    contenu = [
        {"hash": hacher_cle(cle), "role": role, "label": "test"} for cle, role in cles
    ]
    chemin.write_text(json.dumps(contenu), encoding="utf-8")


@pytest.fixture
def magasin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Magasin sur un fichier temporaire, voies d'environnement neutralisées.

    Le nom des champs est repris tel quel de `config.py` : une faute de frappe
    (`api_keys_haches_str` au lieu de `api_keys_hachees_str`) fait échouer le
    monkeypatch SILENCIEUSEMENT, et les tests échouent alors pour une raison qui
    n'est pas celle qu'on croit.
    """
    chemin = tmp_path / "api_keys.json"
    monkeypatch.setattr(cfg, "api_keys_file", chemin)
    monkeypatch.setattr(cfg, "api_keys_hachees_str", "")
    monkeypatch.setattr(cfg, "api_key", "")
    monkeypatch.setattr(cfg, "api_keys_str", "")
    return chemin


class TestRevocationSansRedemarrage:
    def test_cle_revoquee_cesse_d_etre_acceptee(self, magasin: Path) -> None:
        """Le cœur du correctif : retirer une clé la révoque sans redémarrer."""
        _ecrire(magasin, [("rak_ancienne", "admin")])
        assert identifier("rak_ancienne") is not None

        _ecrire(magasin, [])
        assert identifier("rak_ancienne") is None

    def test_cle_ajoutee_est_acceptee_immediatement(self, magasin: Path) -> None:
        """Symétrique : une clé ajoutée est utilisable aussitôt."""
        _ecrire(magasin, [])
        assert identifier("rak_nouvelle") is None

        _ecrire(magasin, [("rak_nouvelle", "user")])
        resultat = identifier("rak_nouvelle")
        assert resultat is not None
        assert resultat[0].name == "USER"

    def test_role_modifie_suit(self, magasin: Path) -> None:
        """Une élévation ou un retrait de rôle doit aussi être pris en compte."""
        _ecrire(magasin, [("rak_x", "user")])
        resultat = identifier("rak_x")
        assert resultat is not None and resultat[0].name == "USER"

        _ecrire(magasin, [("rak_x", "admin")])
        resultat = identifier("rak_x")
        assert resultat is not None and resultat[0].name == "ADMIN"

    def test_fichier_absent_puis_cree(self, magasin: Path) -> None:
        """Un fichier qui n'existait pas est pris en compte à sa création."""
        assert identifier("rak_z") is None
        _ecrire(magasin, [("rak_z", "validateur")])
        assert identifier("rak_z") is not None

    def test_toutes_cles_revoquees_desactive_le_magasin(
        self, magasin: Path
    ) -> None:
        """Révoquer la dernière clé rend `magasin_configure` faux (→ 503)."""
        _ecrire(magasin, [("rak_seule", "admin")])
        assert magasin_configure() is True

        _ecrire(magasin, [])
        assert magasin_configure() is False


class TestCacheToujoursEfficace:
    def test_sans_changement_le_resultat_reste_memoise(self, magasin: Path) -> None:
        """L'invalidation ne doit pas casser la mémoïsation : sinon on relit
        le fichier à chaque requête sans raison."""
        from src import auth

        _ecrire(magasin, [("rak_y", "user")])
        premier = auth._magasin()
        second = auth._magasin()
        assert premier is second

    def test_signature_absente_puis_presente(self, magasin: Path) -> None:
        """La sentinelle distingue « jamais chargé » de « fichier absent ».

        Un fichier absent produit une signature `None` : si `None` servait de
        marqueur « jamais chargé », le premier chargement serait sauté et le
        magasin resterait vide à jamais.
        """
        from src import auth

        assert auth._signature_fichier() is None
        assert isinstance(auth._signature_vue, object)
        _ecrire(magasin, [("rak_w", "user")])
        assert auth._signature_fichier() is not None
        assert identifier("rak_w") is not None
