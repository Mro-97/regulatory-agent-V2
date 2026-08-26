"""
src/watcher.py — Watcher de Regulatory Agent V2
================================================

Surveille les sources réglementaires et détecte les modifications.

Pipeline :
  scheduler → fetch source → normaliser → comparer hash
  → si changement : créer AlerteWatcher → Redis pending_alerts
  → validation humaine

Sources surveillées : EUR-Lex, Légifrance, ANSSI, CNIL, INERIS.
Fréquence : cfg.watcher_intervalle_heures (défaut : 6 h).

Idempotence : une URL déjà alertée n'est pas re-alertée si le hash
n'a pas changé depuis la dernière alerte.

Dépendances : httpx, redis, pydantic >= 2.7
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx

from config import cfg
from src.models import AlerteWatcher, SourceReglementaire, TacheValidation, TypeFilePendante

logger = logging.getLogger(__name__)

# Fichier de persistance des hashes connus (fallback si Redis indisponible)
CHEMIN_HASHES = Path(__file__).parent.parent / "data" / "watcher_hashes.json"


# ---------------------------------------------------------------------------
# Sources à surveiller
# ---------------------------------------------------------------------------


class _SourceConfig:
    """Source surveillée : source institutionnelle + URLs associées."""

    def __init__(self, source: SourceReglementaire, urls: list[str]) -> None:
        self.source = source
        self.urls = urls


SOURCES_CONFIG: list[_SourceConfig] = [
    _SourceConfig(
        source=SourceReglementaire.EUR_LEX,
        urls=[
            "https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=CELEX:32016R0679",
            "https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=CELEX:32022L2555",
        ],
    ),
    _SourceConfig(
        source=SourceReglementaire.CNIL,
        urls=[
            "https://www.cnil.fr/fr/reglement-europeen-protection-donnees",
        ],
    ),
    _SourceConfig(
        source=SourceReglementaire.ANSSI,
        urls=[
            "https://cyber.gouv.fr/uploads/2023/01/anssi-guide-nis2.pdf",
        ],
    ),
]


# ---------------------------------------------------------------------------
# Normalisation du contenu
# ---------------------------------------------------------------------------

def normaliser_contenu(texte_brut: str) -> str:
    """
    Normalise le contenu HTML/texte avant de calculer le hash.

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
    texte = re.sub(
        r"\d{1,2}/\d{1,2}/\d{4}\s+\d{2}:\d{2}(:\d{2})?", "", texte
    )
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
        return json.loads(CHEMIN_HASHES.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Lecture hashes Watcher échouée : %s", exc)
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
        logger.error("Sauvegarde hashes Watcher échouée : %s", exc)


# ---------------------------------------------------------------------------
# Redis — file pending_alerts
# ---------------------------------------------------------------------------


async def enregistrer_alerte_redis(alerte: AlerteWatcher) -> None:
    """Enregistre une alerte dans la file Redis pending_alerts."""
    try:
        import redis.asyncio as aioredis

        client = aioredis.Redis(
            host=cfg.redis_host,
            port=cfg.redis_port,
            password=cfg.redis_password or None,
            db=cfg.redis_db,
            decode_responses=True,
        )

        tache = TacheValidation(
            type_file=TypeFilePendante.ALERTES,
            contenu={
                "alerte_id": str(alerte.alerte_id),
                "source": alerte.source.value,
                "url": alerte.url_detectee,
                "document_concerne": alerte.document_id_concerne,
                "hash_avant": alerte.hash_precedent[:16] + "…",
                "hash_apres": alerte.hash_nouveau[:16] + "…",
                "description": alerte.description_modification,
                "horodatage": alerte.horodatage_detection.isoformat(),
            },
        )
        alerte.tache_validation_id = tache.tache_id

        await client.lpush(TypeFilePendante.ALERTES.value, tache.model_dump_json())
        await client.aclose()
        logger.info(
            "Alerte Watcher enregistrée dans Redis : source=%s url=%s",
            alerte.source.value, alerte.url_detectee,
        )
    except Exception as exc:
        logger.error("Redis indisponible pour l'alerte Watcher : %s", exc)


# ---------------------------------------------------------------------------
# Watcher principal
# ---------------------------------------------------------------------------


class Watcher:
    """
    Surveille les sources réglementaires et publie des alertes
    dans Redis (pending_alerts) lors de la détection de modifications.

    Chaque modification détectée produit une AlerteWatcher soumise à
    validation humaine — jamais appliquée automatiquement au corpus.
    """

    def __init__(self) -> None:
        self._hashes = charger_hashes_connus()
        self._client_http: Optional[httpx.AsyncClient] = None
        self._en_cours = False
        logger.info("Watcher initialisé — %d hashes connus.", len(self._hashes))

    async def _http(self) -> httpx.AsyncClient:
        """Client HTTP partagé avec retry et timeout adaptés aux sources réglementaires."""
        if self._client_http is None or self._client_http.is_closed:
            self._client_http = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=cfg.watcher_follow_redirects,
                headers={
                    "User-Agent": (
                        "RegulatoryAgentV2/0.1 (veille reglementaire locale; "
                        "contact: admin@regulatory-agent.local)"
                    )
                },
            )
        return self._client_http

    async def _fetch_avec_retry(self, url: str, source: SourceReglementaire) -> Optional[str]:
        """
        Récupère une URL avec reprise sur échec réseau ou erreur 5xx.

        Politique : `cfg.watcher_max_essais` tentatives, backoff exponentiel
        de base `cfg.watcher_backoff_secondes`. Les erreurs 4xx (permanentes)
        ne sont PAS réessayées.

        Args:
            url: URL cible.
            source: Source pour le journal.

        Returns:
            Corps de la réponse (str) si succès, None si toutes les
            tentatives ont échoué.
        """
        max_essais = max(1, int(cfg.watcher_max_essais))
        base = max(0.0, float(cfg.watcher_backoff_secondes))
        derniere_erreur: Optional[Exception] = None

        for essai in range(1, max_essais + 1):
            try:
                client = await self._http()
                rep = await client.get(url)
                rep.raise_for_status()
                return rep.text
            except httpx.HTTPStatusError as exc:
                # 4xx : ressource déplacée/supprimée/interdite — pas de retry.
                statut = exc.response.status_code
                if 400 <= statut < 500:
                    logger.warning(
                        "Source indisponible (%s) : %s — %s (pas de retry)",
                        source.value, url, exc,
                    )
                    return None
                derniere_erreur = exc
            except Exception as exc:
                derniere_erreur = exc

            if essai < max_essais:
                attente = base * (2 ** (essai - 1))
                logger.warning(
                    "Watcher — essai %d/%d échoué pour %s (%s), nouvelle tentative dans %.1fs",
                    essai, max_essais, url, derniere_erreur, attente,
                )
                await asyncio.sleep(attente)

        logger.error(
            "Watcher — %d tentatives épuisées pour %s : %s",
            max_essais, url, derniere_erreur,
        )
        return None

    async def verifier_url(
        self,
        url: str,
        source: SourceReglementaire,
    ) -> Optional[AlerteWatcher]:
        """
        Vérifie une URL et retourne une AlerteWatcher si le contenu a changé.

        Args:
            url:    URL à vérifier.
            source: Source réglementaire associée.

        Returns:
            AlerteWatcher si modification détectée, None sinon.
        """
        contenu_brut = await self._fetch_avec_retry(url, source)
        if contenu_brut is None:
            return None

        contenu_normalise = normaliser_contenu(contenu_brut)
        hash_nouveau = calculer_hash_contenu(contenu_normalise)
        hash_precedent = self._hashes.get(url)

        if hash_precedent is None:
            # Première vérification — enregistrer le hash de référence
            self._hashes[url] = hash_nouveau
            await asyncio.to_thread(sauvegarder_hashes, self._hashes)
            logger.info("Watcher — première indexation : %s", url)
            return None

        if hash_nouveau == hash_precedent:
            logger.debug("Watcher — inchangé : %s", url)
            return None

        # Modification détectée
        logger.warning(
            "Watcher — modification détectée : source=%s url=%s",
            source.value, url,
        )

        alerte = AlerteWatcher(
            source=source,
            url_detectee=url,
            hash_precedent=hash_precedent,
            hash_nouveau=hash_nouveau,
            horodatage_detection=datetime.now(timezone.utc),
            description_modification=(
                f"Modification détectée sur {source.value}. "
                f"Hash avant : {hash_precedent[:16]}… "
                f"Hash après : {hash_nouveau[:16]}…"
            ),
        )

        # Mettre à jour le hash connu
        self._hashes[url] = hash_nouveau
        await asyncio.to_thread(sauvegarder_hashes, self._hashes)

        return alerte

    async def cycle_verification(self) -> list[AlerteWatcher]:
        """
        Vérifie toutes les URLs configurées en un seul cycle.

        Exécute les vérifications en parallèle (une par source)
        avec un délai entre les sources pour éviter les surcharges.

        Returns:
            Liste des AlerteWatcher détectées ce cycle.
        """
        if self._en_cours:
            logger.warning("Cycle Watcher déjà en cours — ignoré.")
            return []

        self._en_cours = True
        alertes: list[AlerteWatcher] = []

        logger.info(
            "Watcher — début du cycle (%d sources configurées).",
            len(SOURCES_CONFIG),
        )

        try:
            for config in SOURCES_CONFIG:
                source = config.source
                for url in config.urls:
                    alerte = await self.verifier_url(url, source)
                    if alerte:
                        alertes.append(alerte)
                        await enregistrer_alerte_redis(alerte)
                    # Délai poli entre requêtes
                    await asyncio.sleep(2.0)

        finally:
            self._en_cours = False

        logger.info(
            "Watcher — cycle terminé. %d alerte(s) générée(s).",
            len(alertes),
        )
        return alertes

    async def demarrer_boucle(self) -> None:
        """
        Lance la boucle de surveillance en arrière-plan.
        Tourne indéfiniment avec un intervalle de cfg.watcher_intervalle_heures.
        """
        intervalle_s = cfg.watcher_intervalle_heures * 3600
        logger.info(
            "Watcher — boucle démarrée (intervalle : %d h).",
            cfg.watcher_intervalle_heures,
        )

        while True:
            try:
                await self.cycle_verification()
            except Exception as exc:
                logger.error("Watcher — erreur cycle : %s", exc)

            logger.info(
                "Watcher — prochain cycle dans %d h.",
                cfg.watcher_intervalle_heures,
            )
            await asyncio.sleep(intervalle_s)

    async def fermer(self) -> None:
        """Ferme le client HTTP proprement."""
        if self._client_http and not self._client_http.is_closed:
            await self._client_http.aclose()
