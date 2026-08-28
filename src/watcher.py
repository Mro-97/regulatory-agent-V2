"""src/watcher.py — Watcher de Regulatory Agent V2
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
"""  # noqa: D205, D415

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from config import cfg

from src.models import (
    AlerteWatcher,
    SourceReglementaire,
    TacheValidation,
    TypeFilePendante,
)

logger = logging.getLogger(__name__)

# Sources, normalisation et persistance des hashes extraites dans
# src/watcher_helpers.py (§12 étape 6). Ré-exportées sous les noms
# historiques pour ne pas casser les callers externes (tests inclus,
# qui monkeypatch `src.watcher.CHEMIN_HASHES` par exemple).
from src.watcher_helpers import (  # noqa: E402, F401
    CHEMIN_HASHES,
    SOURCES_CONFIG,
    _SourceConfig,
    calculer_hash_contenu,
    charger_hashes_connus,
    normaliser_contenu,
    sauvegarder_hashes,
)

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

        # `redis.asyncio.Redis.lpush` a une signature générique
        # `Awaitable[int] | int` selon le mode ; le client instancié plus
        # haut est bien asynchrone.
        await cast(
            "Awaitable[Any]",
            client.lpush(
                TypeFilePendante.ALERTES.value,
                tache.model_dump_json(),
            ),
        )
        await client.aclose()
        logger.info(
            "Alerte Watcher enregistrée dans Redis : source=%s url=%s",
            alerte.source.value,
            alerte.url_detectee,
        )
    except Exception as exc:
        logger.exception("Redis indisponible pour l'alerte Watcher : %s", exc)  # noqa: TRY401 — TODO §12 étape 4 : réviser le message en même temps que le typage


# ---------------------------------------------------------------------------
# Watcher principal
# ---------------------------------------------------------------------------


class Watcher:
    """Surveille les sources réglementaires et publie des alertes
    dans Redis (pending_alerts) lors de la détection de modifications.

    Chaque modification détectée produit une AlerteWatcher soumise à
    validation humaine — jamais appliquée automatiquement au corpus.
    """  # noqa: D205 — TODO §12 étape 4 : compléter docstrings

    def __init__(self) -> None:  # noqa: D107 — TODO §12 étape 4 : compléter docstrings
        self._hashes = charger_hashes_connus()
        self._client_http: httpx.AsyncClient | None = None
        self._en_cours = False
        logger.info("Watcher initialisé — %d hashes connus.", len(self._hashes))

    async def _http(self) -> httpx.AsyncClient:
        """Client HTTP partagé avec retry et timeout adaptés aux sources réglementaires."""  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
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

    async def _fetch_avec_retry(
        self, url: str, source: SourceReglementaire
    ) -> str | None:
        """Récupère une URL avec reprise sur échec réseau ou erreur 5xx.

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
        derniere_erreur: Exception | None = None

        for essai in range(1, max_essais + 1):
            try:
                client = await self._http()
                rep = await client.get(url)
                rep.raise_for_status()
                return rep.text  # noqa: TRY300 - TODO 12 etape 4/6 : revue ciblee au moment du typage / de l extraction
            except httpx.HTTPStatusError as exc:
                # 4xx : ressource déplacée/supprimée/interdite — pas de retry.
                statut = exc.response.status_code
                if 400 <= statut < 500:
                    logger.warning(
                        "Source indisponible (%s) : %s — %s (pas de retry)",
                        source.value,
                        url,
                        exc,
                    )
                    return None
                derniere_erreur = exc
            except Exception as exc:  # noqa: BLE001 — frontière externe : journalisation + dégradation gracieuse, cf. skill §8
                derniere_erreur = exc

            if essai < max_essais:
                attente = base * (2 ** (essai - 1))
                logger.warning(
                    "Watcher — essai %d/%d échoué pour %s (%s), nouvelle tentative dans %.1fs",  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
                    essai,
                    max_essais,
                    url,
                    derniere_erreur,
                    attente,
                )
                await asyncio.sleep(attente)

        logger.error(
            "Watcher — %d tentatives épuisées pour %s : %s",
            max_essais,
            url,
            derniere_erreur,
        )
        return None

    async def verifier_url(
        self,
        url: str,
        source: SourceReglementaire,
    ) -> AlerteWatcher | None:
        """Vérifie une URL et retourne une AlerteWatcher si le contenu a changé.

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
            source.value,
            url,
        )

        alerte = AlerteWatcher(
            source=source,
            url_detectee=url,
            hash_precedent=hash_precedent,
            hash_nouveau=hash_nouveau,
            horodatage_detection=datetime.now(UTC),
            description_modification=(
                f"Modification détectée — source {source.value} — "
                f"URL : {url} — "
                f"empreinte SHA-256 : {hash_precedent[:12]}… → {hash_nouveau[:12]}…"
            ),
        )

        # Mettre à jour le hash connu
        self._hashes[url] = hash_nouveau
        await asyncio.to_thread(sauvegarder_hashes, self._hashes)

        return alerte

    async def cycle_verification(self) -> list[AlerteWatcher]:
        """Vérifie toutes les URLs configurées en un seul cycle.

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
        """Lance la boucle de surveillance en arrière-plan.
        Tourne indéfiniment avec un intervalle de cfg.watcher_intervalle_heures.
        """  # noqa: D205 — TODO §12 étape 4 : compléter docstrings
        intervalle_s = cfg.watcher_intervalle_heures * 3600
        logger.info(
            "Watcher — boucle démarrée (intervalle : %d h).",
            cfg.watcher_intervalle_heures,
        )

        while True:
            try:
                await self.cycle_verification()
            except Exception as exc:
                logger.exception("Watcher — erreur cycle : %s", exc)  # noqa: TRY401 — TODO §12 étape 4 : réviser le message en même temps que le typage

            logger.info(
                "Watcher — prochain cycle dans %d h.",
                cfg.watcher_intervalle_heures,
            )
            await asyncio.sleep(intervalle_s)

    async def fermer(self) -> None:
        """Ferme le client HTTP proprement."""
        if self._client_http and not self._client_http.is_closed:
            await self._client_http.aclose()
