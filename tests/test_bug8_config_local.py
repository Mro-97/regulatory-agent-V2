"""
tests/test_bug8_config_local.py — B8

`config.py` référençait encore les IPs LAN de l'ancienne architecture à
trois machines (192.168.1.11 pour Qdrant/Mac B, 192.168.1.12 pour Mac C).
Toute l'infrastructure tourne désormais sur m4pro2 (§3.1 CONTEXTE_PROJET).
Les défauts des hôtes doivent être `127.0.0.1`.
"""

from __future__ import annotations

from config import Parametres


class TestB8DefautsLocaux:
    def test_qdrant_host_par_defaut_est_local(self):  # noqa: ANN201
        # Instance neuve sans lire .env — on force `_env_file=None`.
        p = Parametres(_env_file=None)
        assert p.qdrant_host == "127.0.0.1"

    def test_redis_host_par_defaut_est_local(self):  # noqa: ANN201
        p = Parametres(_env_file=None)
        assert p.redis_host == "127.0.0.1"

    def test_api_host_par_defaut_est_local(self):  # noqa: ANN201
        p = Parametres(_env_file=None)
        assert p.api_host == "127.0.0.1"

    def test_aucune_ip_192_168_x_dans_les_defauts(self):  # noqa: ANN201
        p = Parametres(_env_file=None)
        # Toute valeur str des champs Pydantic doit être exempte d'IP LAN
        # de l'ancienne architecture (ne s'applique qu'aux défauts, pas aux
        # DSN paramétrés via .env).
        for nom, valeur in p.model_dump().items():
            if isinstance(valeur, str):
                assert "192.168.1." not in valeur, (
                    f"Champ config `{nom}` contient une IP LAN de l'ancienne "
                    f"architecture : {valeur!r}"
                )
