"""
Test dédié Bug #1/#2 — main.py ne passe plus reload/workers à uvicorn.Server
et journalise un warning explicite si DEBUG=true ou API_WORKERS>1.
"""

import ast
from pathlib import Path


MAIN_PY = Path(__file__).parent.parent / "main.py"


def _source_main() -> str:
    return MAIN_PY.read_text(encoding="utf-8")


def _kwargs_uvicorn_config() -> set[str]:
    """Retourne l'ensemble des noms de kwargs passés à uvicorn.Config()."""
    arbre = ast.parse(_source_main())
    for noeud in ast.walk(arbre):
        if (
            isinstance(noeud, ast.Call)
            and isinstance(noeud.func, ast.Attribute)
            and noeud.func.attr == "Config"
            and isinstance(noeud.func.value, ast.Name)
            and noeud.func.value.id == "uvicorn"
        ):
            return {kw.arg for kw in noeud.keywords if kw.arg}
    return set()


class TestBug1AucunReloadNiWorkers:
    """
    Régression Bug #1/#2 : uvicorn.Config() ne doit plus recevoir
    reload= ni workers= car ces options ne fonctionnent pas avec
    uvicorn.Server programmatique.
    """

    def test_pas_de_reload_dans_uvicorn_config(self):
        """AST-based : reload= ne doit pas être un kwarg d'uvicorn.Config()."""
        assert "reload" not in _kwargs_uvicorn_config(), (
            "Régression Bug #1 : uvicorn.Config() ne doit plus recevoir reload=."
        )

    def test_pas_de_workers_dans_uvicorn_config(self):
        """AST-based : workers= ne doit pas être un kwarg d'uvicorn.Config()."""
        assert "workers" not in _kwargs_uvicorn_config(), (
            "Régression Bug #2 : uvicorn.Config() ne doit plus recevoir workers=."
        )

    def test_uvicorn_config_ne_contient_que_options_supportees(self):
        """
        Vérifie que l'appel à uvicorn.Config() ne mentionne que
        les kwargs compatibles avec le mode programmatique.
        """
        arbre = ast.parse(_source_main())
        appels_config = []
        for noeud in ast.walk(arbre):
            if (
                isinstance(noeud, ast.Call)
                and isinstance(noeud.func, ast.Attribute)
                and noeud.func.attr == "Config"
                and isinstance(noeud.func.value, ast.Name)
                and noeud.func.value.id == "uvicorn"
            ):
                appels_config.append(noeud)

        assert len(appels_config) == 1, (
            f"Attendu 1 appel à uvicorn.Config(), trouvé {len(appels_config)}"
        )
        kwargs = {kw.arg for kw in appels_config[0].keywords}
        interdits = {"reload", "workers"}
        intersection = kwargs & interdits
        assert not intersection, (
            f"kwargs interdits présents dans uvicorn.Config() : {intersection}"
        )


class TestBug1WarningsDebugEtWorkers:
    """Les warnings doivent apparaître dans le source pour guider l'utilisateur."""

    def test_warning_si_debug_active(self):
        source = _source_main()
        assert "cfg.debug" in source
        assert "reload" in source.lower()
        assert "uvicorn main:app --reload" in source, (
            "Le warning doit indiquer la commande CLI de remplacement."
        )

    def test_warning_si_workers_superieur_a_1(self):
        source = _source_main()
        assert "cfg.api_workers > 1" in source
        assert "gunicorn" in source.lower(), (
            "Le warning doit pointer vers gunicorn pour le multi-worker."
        )

    def test_import_module_reste_valide(self):
        """main.py doit rester importable syntaxiquement."""
        source = _source_main()
        # ast.parse lève SyntaxError si le fichier est cassé
        ast.parse(source)
