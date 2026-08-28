"""src/watcher_helpers.py — Sources, normalisation et persistance du Watcher.

Extrait de src/watcher.py (§12 étape 6 — réduire le module principal
sous 400 lignes). Aucun changement de comportement — les fonctions et
constantes sont ré-exportées telles quelles depuis src/watcher.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import cast

from src.models import SourceReglementaire

logger = logging.getLogger(__name__)

# Fichier de persistance des hashes connus (fallback si Redis indisponible)
CHEMIN_HASHES = Path(__file__).parent.parent / "data" / "watcher_hashes.json"


# ---------------------------------------------------------------------------
# Sources à surveiller
# ---------------------------------------------------------------------------


class _SourceConfig:
    """Source surveillée : source institutionnelle + URLs associées."""

    def __init__(self, source: SourceReglementaire, urls: list[str]) -> None:
        """Groupe une source et l'ensemble d'URLs à surveiller pour cette source."""
        self.source = source
        self.urls = urls


SOURCES_CONFIG: list[_SourceConfig] = [
    # EUR-Lex retiré : les pages HTML `legal-content/FR/TXT/?uri=CELEX:...`
    # embarquent du contenu dynamique (tokens de session, timestamps, IDs
    # d'analytics) que `normaliser_contenu` ne capture pas. Deux GET
    # consécutifs à 2 s d'écart produisent deux hash différents → alerte
    # à chaque cycle Watcher. Le suivi EUR-Lex devra passer par l'API
    # officielle (SPARQL / OData) plutôt que par scraping HTML — refonte
    # à traiter séparément.
    _SourceConfig(
        source=SourceReglementaire.CNIL,
        urls=[
            "https://www.cnil.fr/fr/reglement-europeen-protection-donnees",
        ],
    ),
    _SourceConfig(
        source=SourceReglementaire.ANSSI,
        urls=[
            # Portail ANSSI/DINUM dédié au suivi NIS2 — répond 200 direct
            # (compatible watcher_follow_redirects=false).
            "https://messervices.cyber.gouv.fr/nis2",
        ],
    ),
]


# ---------------------------------------------------------------------------
# Normalisation du contenu
# ---------------------------------------------------------------------------


def normaliser_contenu(texte_brut: str) -> str:
    """Normalise le contenu HTML/texte avant de calculer le hash.

    Supprime les éléments variables (dates d'accès, compteurs, tokens CSRF,
    publicités) qui changeraient le hash sans modifier le contenu réglementaire.

    Args:
        texte_brut: Contenu brut récupéré depuis la source.

    Returns:
        Contenu normalisé pour le calcul du hash.
    """
    # Suppression balises HTML
    texte = re.sub(r"<[^>]+>", " ", texte_brut)
    # Normalisation des espaces
    texte = re.sub(r"\s+", " ", texte).strip()
    # Suppression des tokens variables courants
    texte = re.sub(r'csrf[_-]?token["\s:=]+\S+', "", texte, flags=re.IGNORECASE)
    texte = re.sub(r'nonce["\s:=]+\S+', "", texte, flags=re.IGNORECASE)
    # Suppression des horodatages en clair
    texte = re.sub(r"\d{1,2}/\d{1,2}/\d{4}\s+\d{2}:\d{2}(:\d{2})?", "", texte)
    return texte


def calculer_hash_contenu(contenu: str) -> str:
    """Calcule le SHA-256 du contenu normalisé."""
    return hashlib.sha256(contenu.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Persistance des hashes
# ---------------------------------------------------------------------------


def charger_hashes_connus() -> dict[str, str]:
    """Charge les hashes connus depuis le fichier local."""
    if not CHEMIN_HASHES.exists():
        return {}
    try:
        return cast(
            "dict[str, str]",
            json.loads(CHEMIN_HASHES.read_text(encoding="utf-8")),
        )
    except Exception as exc:
        logger.exception("Lecture hashes Watcher échouée : %s", exc)  # noqa: TRY401
        return {}


def sauvegarder_hashes(hashes: dict[str, str]) -> None:
    """Sauvegarde les hashes dans le fichier local."""
    CHEMIN_HASHES.parent.mkdir(parents=True, exist_ok=True)
    try:
        CHEMIN_HASHES.write_text(
            json.dumps(hashes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.exception("Sauvegarde hashes Watcher échouée : %s", exc)  # noqa: TRY401
