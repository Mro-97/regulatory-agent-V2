"""tests/test_rbac.py — clés API hachées + contrôle d'accès par rôle.

- aucune clé en clair persistée : le magasin ne connaît que des SHA-256 ;
- rôles hiérarchiques user < validateur < admin ;
- chaque endpoint exige un rôle minimum (403 sinon) ;
- /whoami renvoie le rôle ; la voie « clé en clair » vaut admin en dev.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from src import api as api_module
from src.auth import Role, hacher_cle, identifier, recharger_magasin


def _magasin(monkeypatch, tmp_path, entrees: list[dict]) -> None:  # noqa: ANN001
    """Pointe cfg.api_keys_file vers un fichier temporaire + vide le cache."""
    from config import cfg

    fichier = tmp_path / "api_keys.json"
    fichier.write_text(json.dumps({"keys": entrees}), encoding="utf-8")
    monkeypatch.setattr(cfg, "api_keys_file", fichier)
    monkeypatch.setattr(cfg, "api_key", "")
    monkeypatch.setattr(cfg, "api_keys_str", "")
    monkeypatch.setattr(cfg, "api_keys_hachees_str", "")
    recharger_magasin()


CLES = {
    "user": "rak_test_user_000000000000000000000000",
    "validateur": "rak_test_valid_00000000000000000000000",
    "admin": "rak_test_admin_00000000000000000000000",
}


@pytest.fixture
def client_rbac(monkeypatch, tmp_path) -> TestClient:  # noqa: ANN001
    _magasin(
        monkeypatch,
        tmp_path,
        [
            {"hash": hacher_cle(CLES["user"]), "role": "user", "label": "u"},
            {
                "hash": hacher_cle(CLES["validateur"]),
                "role": "validateur",
                "label": "v",
            },
            {"hash": hacher_cle(CLES["admin"]), "role": "admin", "label": "a"},
        ],
    )
    return TestClient(api_module.app)


# --- unités ---------------------------------------------------------------


def test_role_hierarchie() -> None:
    assert Role.USER < Role.VALIDATEUR < Role.ADMIN
    assert Role.depuis_texte("Admin") is Role.ADMIN


def test_identifier_temps_constant(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    _magasin(
        monkeypatch,
        tmp_path,
        [{"hash": hacher_cle("rak_abc"), "role": "user", "label": "x"}],
    )
    assert identifier("rak_abc") == (Role.USER, "x")
    assert identifier("rak_mauvais") is None
    assert identifier("") is None
    assert identifier(None) is None


def test_aucune_cle_en_clair_dans_le_fichier(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    from config import cfg

    _magasin(
        monkeypatch,
        tmp_path,
        [{"hash": hacher_cle("rak_secret"), "role": "user", "label": "x"}],
    )
    contenu = cfg.api_keys_file.read_text(encoding="utf-8")
    assert "rak_secret" not in contenu
    assert hacher_cle("rak_secret") in contenu


# --- endpoints : rôle minimum -----------------------------------------------


def test_user_peut_ask_pas_pending_ni_ingest(client_rbac) -> None:  # noqa: ANN001
    h = {"X-API-Key": CLES["user"]}
    assert client_rbac.get("/whoami", headers=h).json()["role"] == "user"
    assert (
        client_rbac.post(
            "/ask", json={"question": "obligations RGPD ?"}, headers=h
        ).status_code
        == 200
    )
    assert client_rbac.get("/pending", headers=h).status_code == 403
    assert (
        client_rbac.post("/ingest", json={"source": "EUR-Lex"}, headers=h).status_code
        == 403
    )


def test_validateur_peut_pending_pas_ingest(client_rbac) -> None:  # noqa: ANN001
    h = {"X-API-Key": CLES["validateur"]}
    r = client_rbac.get("/pending", headers=h)
    assert r.status_code in (200, 503)  # 503 si Redis absent — pas 403
    assert (
        client_rbac.post("/ingest", json={"source": "EUR-Lex"}, headers=h).status_code
        == 403
    )


def test_admin_peut_tout(client_rbac) -> None:  # noqa: ANN001
    h = {"X-API-Key": CLES["admin"]}
    assert client_rbac.get("/whoami", headers=h).json()["role"] == "admin"
    assert client_rbac.get("/pending", headers=h).status_code in (200, 503)
    # /ingest sans contenu_json -> 400 (pas 403) : le rôle passe.
    assert client_rbac.post(
        "/ingest", json={"source": "EUR-Lex"}, headers=h
    ).status_code in (400, 202)


def test_cle_inconnue_401(client_rbac) -> None:  # noqa: ANN001
    r = client_rbac.post(
        "/ask", json={"question": "x ?"}, headers={"X-API-Key": "rak_inconnue"}
    )
    assert r.status_code == 401


def test_magasin_vide_503(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    _magasin(monkeypatch, tmp_path, [])
    c = TestClient(api_module.app)
    assert (
        c.post(
            "/ask", json={"question": "x ?"}, headers={"X-API-Key": "rak_x"}
        ).status_code
        == 503
    )


def test_cle_en_clair_vaut_admin_en_dev(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    from config import cfg

    monkeypatch.setattr(cfg, "api_keys_file", tmp_path / "absent.json")
    monkeypatch.setattr(cfg, "environnement", "dev")
    monkeypatch.setattr(cfg, "api_key", "cle-en-clair-de-dev-32-caracteres-ok")
    recharger_magasin()
    c = TestClient(api_module.app)
    r = c.get("/whoami", headers={"X-API-Key": "cle-en-clair-de-dev-32-caracteres-ok"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_cle_en_clair_ignoree_en_prod(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    from config import cfg

    monkeypatch.setattr(cfg, "api_keys_file", tmp_path / "absent.json")
    monkeypatch.setattr(cfg, "environnement", "prod")
    monkeypatch.setattr(cfg, "api_key", "cle-en-clair-de-prod-32-caracteres-ko")
    recharger_magasin()
    assert identifier("cle-en-clair-de-prod-32-caracteres-ko") is None


# --- CLI gerer_cles.py ----------------------------------------------------


def test_gerer_cles_roundtrip(monkeypatch, tmp_path, capsys) -> None:  # noqa: ANN001
    from config import cfg

    monkeypatch.setattr(cfg, "api_keys_file", tmp_path / "keys.json")
    from scripts import gerer_cles

    assert gerer_cles.main(["generer", "--role", "user", "--label", "alice"]) == 0
    sortie = capsys.readouterr().out
    cle = next(m for m in sortie.split() if m.startswith("rak_"))
    assert "rak_" in cle and hacher_cle(cle) in cfg.api_keys_file.read_text()
    assert cle not in cfg.api_keys_file.read_text()  # jamais la clé en clair

    recharger_magasin()
    assert identifier(cle) == (Role.USER, "alice")

    assert gerer_cles.main(["lister"]) == 0
    assert "alice" in capsys.readouterr().out

    assert gerer_cles.main(["revoquer", "--label", "alice"]) == 0
    recharger_magasin()
    assert identifier(cle) is None
