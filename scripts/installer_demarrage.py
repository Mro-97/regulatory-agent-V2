"""scripts/installer_demarrage.py — Démarrage automatique via launchd (O-6).

Sans ce mécanisme, l'API devait être lancée à la main après chaque
redémarrage de m4pro2 : le proxy Tailscale, lui, survit au reboot, si bien
qu'après un redémarrage on avait un accès en ligne vers… rien. C'est
exactement le scénario vécu en fin de session.

Deux agents sont installés :

- `org.regulatory-agent.api` : lance `scripts/launcher.py` au démarrage
  (`RunAtLoad`). Qdrant et Redis sont déjà gérés par le launcher, qui ne les
  redémarre que s'ils sont absents.
- `org.regulatory-agent.surveillance` : exécute `scripts/surveillance.py`
  toutes les 15 minutes. Sans planification, un script de supervision ne sert
  à rien — personne ne le lance à la main.

`KeepAlive` est volontairement **faux** : si l'API échoue au démarrage (clé
absente, dimension incohérente, port occupé), `launchd` la relancerait en
boucle et masquerait l'erreur au lieu de la rendre visible. Un démarrage raté
doit rester un échec consultable dans les journaux.

Usage :
    python scripts/installer_demarrage.py --installer
    python scripts/installer_demarrage.py --etat
    python scripts/installer_demarrage.py --desinstaller
"""

from __future__ import annotations

import argparse
import logging
import subprocess
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_AGENTS = Path.home() / "Library" / "LaunchAgents"
DOSSIER_LOGS = RACINE / "logs"

logger = logging.getLogger("installer_demarrage")

AGENT_API = "org.regulatory-agent.api"
AGENT_SURVEILLANCE = "org.regulatory-agent.surveillance"

_GABARIT_API = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{launcher}</string>
  </array>
  <key>WorkingDirectory</key><string>{racine}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>StandardOutPath</key><string>{log_sortie}</string>
  <key>StandardErrorPath</key><string>{log_erreur}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>{chemin}</string>
  </dict>
</dict>
</plist>
"""

_GABARIT_SURVEILLANCE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{script}</string>
  </array>
  <key>WorkingDirectory</key><string>{racine}</string>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>{log_sortie}</string>
  <key>StandardErrorPath</key><string>{log_erreur}</string>
</dict>
</plist>
"""


def _python_du_venv() -> Path:
    """Interpréteur du venv du projet — jamais le python système."""
    return RACINE / "venv" / "bin" / "python"


def _chemin_execution() -> str:
    """PATH minimal pour l'agent : venv d'abord, puis les binaires système."""
    return f"{_python_du_venv().parent}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def _contenu_api() -> str:
    """Contenu du plist de l'API."""
    return _GABARIT_API.format(
        label=AGENT_API,
        python=_python_du_venv(),
        launcher=RACINE / "scripts" / "launcher.py",
        racine=RACINE,
        log_sortie=DOSSIER_LOGS / "api.launchd.log",
        log_erreur=DOSSIER_LOGS / "api.launchd.err.log",
        chemin=_chemin_execution(),
    )


def _contenu_surveillance() -> str:
    """Contenu du plist de la supervision."""
    return _GABARIT_SURVEILLANCE.format(
        label=AGENT_SURVEILLANCE,
        python=_python_du_venv(),
        script=RACINE / "scripts" / "surveillance.py",
        racine=RACINE,
        log_sortie=DOSSIER_LOGS / "surveillance.launchd.log",
        log_erreur=DOSSIER_LOGS / "surveillance.launchd.err.log",
    )


def _ecrire_agent(nom: str, contenu: str) -> Path:
    """Écrit le plist et le valide avec `plutil` avant de le rendre actif."""
    DOSSIER_AGENTS.mkdir(parents=True, exist_ok=True)
    chemin = DOSSIER_AGENTS / f"{nom}.plist"
    chemin.write_text(contenu, encoding="utf-8")
    resultat = subprocess.run(  # noqa: S603 — binaire système, arguments fixes
        ["/usr/bin/plutil", "-lint", str(chemin)],
        capture_output=True,
        text=True,
        check=False,
    )
    if resultat.returncode != 0:
        chemin.unlink(missing_ok=True)
        raise SystemExit(  # noqa: TRY003 — message opérateur
            f"plist invalide pour {nom} : {resultat.stdout}{resultat.stderr}"
        )
    logger.info("  %s écrit et validé", chemin.name)
    return chemin


def _charger(nom: str) -> None:
    """Décharge puis recharge l'agent (idempotent)."""
    chemin = DOSSIER_AGENTS / f"{nom}.plist"
    etiquette = f"gui/{_uid()}/{nom}"
    subprocess.run(  # noqa: S603 — binaire système, arguments fixes
        ["/bin/launchctl", "bootout", etiquette],
        capture_output=True,
        check=False,
    )
    resultat = subprocess.run(  # noqa: S603
        ["/bin/launchctl", "bootstrap", f"gui/{_uid()}", str(chemin)],
        capture_output=True,
        text=True,
        check=False,
    )
    if resultat.returncode != 0:
        logger.warning("  %s non chargé : %s", nom, resultat.stderr.strip())
        return
    logger.info("  %s chargé", nom)


def _uid() -> int:
    """UID de l'utilisateur courant (domaine `gui/<uid>` de launchctl)."""
    import os

    return os.getuid()


def installer() -> int:
    """Écrit, valide et charge les deux agents."""
    if not _python_du_venv().exists():
        raise SystemExit(  # noqa: TRY003 — message opérateur
            f"venv introuvable : {_python_du_venv()} — lancer depuis m4pro2"
        )
    DOSSIER_LOGS.mkdir(exist_ok=True)
    logger.info("Installation des agents launchd :")
    _ecrire_agent(AGENT_API, _contenu_api())
    _ecrire_agent(AGENT_SURVEILLANCE, _contenu_surveillance())
    _charger(AGENT_API)
    _charger(AGENT_SURVEILLANCE)
    logger.info("L'API démarrera automatiquement au prochain redémarrage.")
    logger.info("Supervision toutes les 15 min, journal dans logs/.")
    return 0


def etat() -> int:
    """Liste les agents du projet connus de launchd."""
    resultat = subprocess.run(
        ["/bin/launchctl", "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    lignes = [
        ligne
        for ligne in resultat.stdout.splitlines()
        if "regulatory-agent" in ligne
    ]
    if not lignes:
        logger.info("Aucun agent regulatory-agent installé.")
        return 1
    for ligne in lignes:
        logger.info("  %s", ligne.strip())
    return 0


def desinstaller() -> int:
    """Décharge et supprime les deux agents."""
    for nom in (AGENT_API, AGENT_SURVEILLANCE):
        subprocess.run(  # noqa: S603
            ["/bin/launchctl", "bootout", f"gui/{_uid()}/{nom}"],
            capture_output=True,
            check=False,
        )
        chemin = DOSSIER_AGENTS / f"{nom}.plist"
        chemin.unlink(missing_ok=True)
        logger.info("  %s déchargé et supprimé", nom)
    return 0


def _parser() -> argparse.Namespace:
    """Analyse les arguments."""
    parser = argparse.ArgumentParser(description="Démarrage automatique launchd.")
    groupe = parser.add_mutually_exclusive_group(required=True)
    groupe.add_argument("--installer", action="store_true")
    groupe.add_argument("--etat", action="store_true")
    groupe.add_argument("--desinstaller", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Point d'entrée."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parser()
    if args.installer:
        return installer()
    if args.etat:
        return etat()
    return desinstaller()


if __name__ == "__main__":
    raise SystemExit(main())
