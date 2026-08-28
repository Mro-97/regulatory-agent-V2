"""
Test dédié Bug #8 — dédoublement de question_max_length.

Avant le fix, RequeteQuestion.question (src/models.py) codait en dur
max_length=4000, indépendamment de cfg.question_max_length (config.py).
Modifier QUESTION_MAX_LENGTH dans .env n'avait donc aucun effet sur la
validation réelle de l'API.

Le fix fait dépendre RequeteQuestion.question de cfg.question_max_length,
seule source de vérité.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestBug8QuestionMaxLength:
    def test_max_length_derive_de_cfg_par_defaut(self):
        """La contrainte Pydantic doit être identique à cfg.question_max_length,
        et non une constante indépendante qui coïncide par hasard."""
        from config import cfg
        from src.models import RequeteQuestion

        champ = RequeteQuestion.model_fields["question"]
        max_len = next(m.max_length for m in champ.metadata if hasattr(m, "max_length"))
        assert max_len == cfg.question_max_length

    def test_env_override_propage_a_requete_question(self):
        """
        Régression du Bug #8 : QUESTION_MAX_LENGTH doit réellement modifier
        la validation, pas seulement cfg.

        Exécuté dans un sous-processus isolé (plutôt qu'un importlib.reload
        en process) car recharger src.models en cours de suite casse
        l'identité des classes Pydantic déjà importées ailleurs
        (ex. orchestrator.py garde une référence à l'ancienne classe
        SortieAgent), ce qui fait échouer des tests sans rapport.
        """
        script = (
            "from src.models import RequeteQuestion\n"
            "champ = RequeteQuestion.model_fields['question']\n"
            "max_len = next(m.max_length for m in champ.metadata if hasattr(m, 'max_length'))\n"  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
            "assert max_len == 10, f'attendu 10, obtenu {max_len}'\n"
            "try:\n"
            "    RequeteQuestion(question='ceci depasse largement dix caracteres')\n"
            "    raise SystemExit('BUG : aucune ValidationError levee')\n"
            "except Exception as exc:\n"
            "    assert type(exc).__name__ == 'ValidationError', exc\n"
            "print('OK')\n"
        )
        env = os.environ.copy()
        env["QUESTION_MAX_LENGTH"] = "10"
        resultat = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert resultat.returncode == 0, (
            f"stdout={resultat.stdout!r} stderr={resultat.stderr!r}"
        )
        assert "OK" in resultat.stdout
