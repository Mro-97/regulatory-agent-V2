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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from config import cfg
from src.http_client import ClientSortant
from src.models import (
    AlerteWatcher,
    SourceReglementaire,
    TacheValidation,
    TypeFilePendante,
)
from src.redis_client import ClientRedis, nouveau_client

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
    """Pousse une AlerteWatcher dans la file Redis `pending_alerts`."""
    try:
        client = _nouveau_client_redis()
        tache = _construire_tache_validation_alerte(alerte)
        alerte.tache_validation_id = tache.tache_id
        await cast(
            "Awaitable[Any]",
            client.lpush(TypeFilePendante.ALERTES.value, tache.model_dump_json()),
        )
        await client.aclose()
        logger.info(
            "Alerte Watcher enregistrée dans Redis : source=%s url=%s",
            alerte.source.value,
            alerte.url_detectee,
        )
    except Exception:
        logger.exception("Redis indisponible pour l'alerte Watcher")


def _nouveau_client_redis() -> ClientRedis:
    """Fabrique un client Redis asynchrone (client maison, source unique)."""
    return nouveau_client(
        host=cfg.redis_host,
        port=cfg.redis_port,
        password=cfg.redis_password,
        db=cfg.redis_db,
        timeout_secondes=cfg.redis_timeout_secondes,
    )


@dataclass(frozen=True)
class _ResultatTentativeFetch:
    """Résultat d'une tentative HTTP dans `Watcher._tenter_fetch`."""

    contenu: str | None  # contenu HTTP si succès, sinon None
    arreter: bool  # True si 4xx (pas de retry)
    erreur: Exception | None  # exception rencontrée (si non-succès)


def _construire_alerte(
    source: SourceReglementaire,
    url: str,
    hash_precedent: str,
    hash_nouveau: str,
) -> AlerteWatcher:
    """Construit une AlerteWatcher horodatée avec description SHA-256 tronquée."""
    return AlerteWatcher(
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


async def _attendre_backoff(
    essai: int, max_essais: int, url: str, erreur: Exception | None, base: float
) -> None:
    """Attend `base * 2^(essai-1)` secondes et loggue la tentative."""
    attente = base * (2 ** (essai - 1))
    logger.warning(
        "Watcher — essai %d/%d échoué pour %s (%s), nouvelle tentative dans %.1fs",
        essai,
        max_essais,
        url,
        erreur,
        attente,
    )
    await asyncio.sleep(attente)


def _construire_tache_validation_alerte(alerte: AlerteWatcher) -> TacheValidation:
    """Construit la TacheValidation qui matérialise une alerte dans Redis."""
    return TacheValidation(
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


# ---------------------------------------------------------------------------
# Watcher principal
# ---------------------------------------------------------------------------


class Watcher:
    """Surveille les sources réglementaires et publie des alertes
    dans Redis (pending_alerts) lors de la détection de modifications.

    Chaque modification détectée produit une AlerteWatcher soumise à
    validation humaine — jamais appliquée automatiquement au corpus.
    """  # noqa: D205

    def __init__(self) -> None:  # noqa: D107
        self._hashes = charger_hashes_connus()
        self._client_http: ClientSortant | None = None
        self._en_cours = False
        logger.info("Watcher initialisé — %d hashes connus.", len(self._hashes))

    async def _http(self) -> ClientSortant:
        """Client sortant durci (IP validée épinglée, redirections revalidées)."""
        if self._client_http is None:
            self._client_http = ClientSortant(
                timeout=30.0,
                max_redirections=max(1, int(cfg.watcher_max_redirections)),
            )
        return self._client_http

    async def _fetch_avec_retry(
        self, url: str, source: SourceReglementaire
    ) -> str | None:
        """Récupère `url` avec backoff exponentiel ; None sur 4xx/épuisement."""
        max_essais = max(1, int(cfg.watcher_max_essais))
        base = max(0.0, float(cfg.watcher_backoff_secondes))
        derniere_erreur: Exception | None = None
        for essai in range(1, max_essais + 1):
            resultat = await self._tenter_fetch(url, source)
            if resultat.contenu is not None:
                return resultat.contenu
            if resultat.arreter:
                return None
            derniere_erreur = resultat.erreur
            if essai < max_essais:
                await _attendre_backoff(essai, max_essais, url, derniere_erreur, base)
        logger.error(
            "Watcher — %d tentatives épuisées pour %s : %s",
            max_essais,
            url,
            derniere_erreur,
        )
        return None

    async def _tenter_fetch(
        self, url: str, source: SourceReglementaire
    ) -> _ResultatTentativeFetch:
        """Une tentative HTTP : succès, arrêt (4xx ou refus SSRF), ou à réessayer."""
        try:
            client = await self._http()
            reponse = await client.recuperer(url)
        except Exception as exc:  # noqa: BLE001 — frontière externe, cf. skill §8
            return self._echec_tentative(url, source, exc)
        return _ResultatTentativeFetch(
            contenu=reponse.contenu, arreter=False, erreur=None
        )

    def _echec_tentative(
        self, url: str, source: SourceReglementaire, exc: Exception
    ) -> _ResultatTentativeFetch:
        """Qualifie un échec : refus définitif, 4xx définitif, ou à réessayer.

        Un refus SSRF (URL ou cible de redirection) et un 4xx sont permanents :
        réessayer ne changerait rien et ne ferait qu'ajouter du bruit. Le reste
        (réseau, 5xx) est transitoire et mérite une reprise.
        """
        from src.http_client import RedirectionsExcessivesError
        from src.http_safety import UrlInterneRefuseeError

        if isinstance(exc, (UrlInterneRefuseeError, RedirectionsExcessivesError)):
            logger.warning(
                "Watcher — URL refusée (SSRF prévention) : %s — %s", url, exc
            )
            return _ResultatTentativeFetch(contenu=None, arreter=True, erreur=exc)
        if (
            isinstance(exc, httpx.HTTPStatusError)
            and 400 <= exc.response.status_code < 500
        ):
            logger.warning(
                "Source indisponible (%s) : %s — %s (pas de retry)",
                source.value,
                url,
                exc,
            )
            return _ResultatTentativeFetch(contenu=None, arreter=True, erreur=exc)
        return _ResultatTentativeFetch(contenu=None, arreter=False, erreur=exc)

    async def verifier_url(
        self,
        url: str,
        source: SourceReglementaire,
    ) -> AlerteWatcher | None:
        """Retourne une AlerteWatcher si le contenu de `url` a changé, None sinon."""
        contenu_brut = await self._fetch_avec_retry(url, source)
        if contenu_brut is None:
            return None
        hash_nouveau = calculer_hash_contenu(normaliser_contenu(contenu_brut))
        hash_precedent = self._hashes.get(url)
        if hash_precedent is None:
            await self._memoriser_hash_initial(url, hash_nouveau)
            return None
        if hash_nouveau == hash_precedent:
            logger.debug("Watcher — inchangé : %s", url)
            return None
        logger.warning(
            "Watcher — modification détectée : source=%s url=%s", source.value, url
        )
        alerte = _construire_alerte(source, url, hash_precedent, hash_nouveau)
        await self._memoriser_hash(url, hash_nouveau)
        return alerte

    async def _memoriser_hash_initial(self, url: str, hash_nouveau: str) -> None:
        """Enregistre le hash de référence (première vérification d'une URL)."""
        await self._memoriser_hash(url, hash_nouveau)
        logger.info("Watcher — première indexation : %s", url)

    async def _memoriser_hash(self, url: str, hash_nouveau: str) -> None:
        """Met à jour la table des hashes EN MÉMOIRE (persistance en fin de cycle).

        La persistance était faite ici, donc une réécriture complète du fichier
        par URL — et une écriture tronquée pouvait perdre la totalité des
        empreintes connues. `cycle_verification` enregistre désormais une seule
        fois, atomiquement.
        """
        self._hashes[url] = hash_nouveau

    async def _persister_hashes(self) -> None:
        """Persiste la table des hashes (écriture atomique, hors event loop)."""
        await asyncio.to_thread(sauvegarder_hashes, self._hashes)

    async def cycle_verification(self) -> list[AlerteWatcher]:
        """Vérifie séquentiellement toutes les URLs configurées ; publie les alertes."""
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
                await self._verifier_source(config, alertes)
        finally:
            # Persistance unique en fin de cycle (même en cas d'erreur) : les
            # hashes mis à jour en mémoire ne doivent pas être perdus.
            await self._persister_hashes()
            self._en_cours = False
        logger.info("Watcher — cycle terminé. %d alerte(s) générée(s).", len(alertes))
        return alertes

    async def _verifier_source(
        self,
        config: _SourceConfig,
        alertes: list[AlerteWatcher],
    ) -> None:
        """Vérifie toutes les URLs d'une source ; publie chaque alerte détectée."""
        for url in config.urls:
            alerte = await self.verifier_url(url, config.source)
            if alerte:
                alertes.append(alerte)
                await enregistrer_alerte_redis(alerte)
            await asyncio.sleep(2.0)  # Délai poli entre requêtes

    async def demarrer_boucle(self) -> None:
        """Lance la boucle de surveillance en arrière-plan.
        Tourne indéfiniment avec un intervalle de cfg.watcher_intervalle_heures.
        """  # noqa: D205
        intervalle_s = cfg.watcher_intervalle_heures * 3600
        logger.info(
            "Watcher — boucle démarrée (intervalle : %d h).",
            cfg.watcher_intervalle_heures,
        )

        while True:
            try:
                await self.cycle_verification()
            except Exception:
                logger.exception("Watcher — erreur cycle")

            await self._rotation_cles_user()

            logger.info(
                "Watcher — prochain cycle dans %d h.",
                cfg.watcher_intervalle_heures,
            )
            await asyncio.sleep(intervalle_s)

    @staticmethod
    async def _rotation_cles_user() -> None:
        """Révoque les clés API de rôle `user` trop anciennes.

        Accrochée à la boucle du watcher (6 h par défaut) plutôt qu'au
        démarrage : l'API tourne des jours, une rotation décidée uniquement au
        boot ne s'appliquerait jamais. JAMAIS les rôles `validateur`/`admin` —
        cf. src/rotation_cles.py. Une erreur ici ne doit pas interrompre la
        veille : elle est journalisée.
        """
        from src.rotation_cles import rotation

        try:
            revoquees = await asyncio.to_thread(rotation)
        except Exception:
            logger.exception("Rotation des clés échouée (non bloquant)")
            return
        for entree in revoquees:
            logger.warning(
                "Rotation : clé user %r révoquée (durée de vie %d jours).",
                entree.get("label"),
                cfg.cle_user_duree_vie_jours,
            )

    async def fermer(self) -> None:
        """Ferme le client HTTP proprement."""
        if self._client_http is not None:
            await self._client_http.fermer()
            self._client_http = None
