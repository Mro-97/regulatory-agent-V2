"""src/orchestrator.py — Orchestrateur de Regulatory Agent V2
===========================================================

Responsabilités :
- Recevoir les requêtes de l'API.
- Classifier la requête (courante / temporelle / conflit).
- Router vers les agents appropriés.
- Assembler la réponse avec preuves et citations.
- Soumettre à validation humaine si nécessaire.
- Enregistrer la trace d'audit.

Mécanisme de bascule :
  mode="real" → utilise les vrais agents (Retriever Qdrant, etc.)
  mode="mock" → retourne des données simulées sans dépendance externe

  Contrôlé par la variable d'environnement ORCHESTRATEUR_MODE
  ou par le paramètre du constructeur.

Pipeline par type de requête :
  courante    → Retriever → Explainer → Citation
  temporelle  → Retriever → Temporal  → Explainer → Citation
  conflit     → Retriever → Conflict  → Explainer → Citation

Agents non encore implémentés (Temporal, Explainer, Citation, Conflict)
→ leurs étapes sont marquées TODO et contournées proprement.

Dépendances : httpx, redis, pydantic >= 2.7
"""  # noqa: D205, D415

from __future__ import annotations

import asyncio
import logging
import queue
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID, uuid4

from config import cfg

if TYPE_CHECKING:
    from scripts.ingest import Ingester
    from src.agents.citation import ResultatCitation
    from src.agents.retriever import Retriever
    from src.redis_client import ClientRedis

from src.errors import QueueBackendError
from src.mlx_utils import EXECUTEUR_MLX, executer_mlx
from src.models import (
    EnregistrementAudit,
    EvidenceRecuperee,
    NiveauConfiance,
    SortieAgent,
    SourceReglementaire,
    StatutValidation,
    TacheValidation,
    TypeFilePendante,
)

# Alias des helpers extraits : ils conservent leur nom prive historique pour
# ne casser aucun appelant.  n est pas utilise dans ce
# module — il est re-exporte pour les tests — d ou le noqa.
# fmt: off
from src.orchestrator_audit import (
    MACHINE as _MACHINE,
)
from src.orchestrator_audit import (
    MACHINE_INCONNUE as _MACHINE_INCONNUE,  # noqa: F401
)
from src.orchestrator_audit import (
    construire_audit_mock as _construire_audit_mock,
)
from src.orchestrator_audit import (
    construire_audit_reel as _construire_audit_reel,
)
from src.orchestrator_audit import (
    journaliser_audit_fallback as _journaliser_audit_fallback,
)
from src.orchestrator_audit import (
    journaliser_audit_succes as _journaliser_audit_succes,
)
from src.orchestrator_audit import (
    journaliser_debut_traitement as _journaliser_debut_traitement,
)
from src.orchestrator_audit import (
    reponse_ingestion_mock as _reponse_ingestion_mock,
)
from src.orchestrator_audit import (
    resoudre_mode as _resoudre_mode,
)
from src.orchestrator_confidence import (
    CONFIANCES_A_VALIDER as CONFIANCES_A_VALIDER,
)
from src.orchestrator_confidence import (
    ORDRE_CONFIANCE as ORDRE_CONFIANCE,
)
from src.orchestrator_confidence import (
    confiance_apres_citation as _confiance_apres_citation,
)
from src.orchestrator_confidence import (
    construire_reponse_question as _construire_reponse_question,
)
from src.orchestrator_confidence import (
    doit_soumettre_validation as _doit_soumettre_validation,
)

# fmt: on
from src.orchestrator_confidence import (
    mode_depuis_agents,
)
from src.orchestrator_confidence import (
    reponse_retrieval_indisponible as _reponse_retrieval_indisponible,
)

# Ré-export de compatibilité descendante (§12 étape 6) : api.py et les
# tests importent encore `DocumentDejaIndexeError` depuis ce module.
from src.orchestrator_ingest import (
    DocumentDejaIndexeError as DocumentDejaIndexeError,
)
from src.schemas import (
    ReponseDecisionValidation,
    ReponseIngestion,
    ReponseQuestion,
    ReponseTachesPendantes,
    RequeteIngestion,
    RequeteQuestion,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Classification extraite dans src/classification.py (§12 étape 6).
from src.classification import classifier_requete as _classifier_requete  # noqa: E402

# ---------------------------------------------------------------------------
# Orchestrateur
# ---------------------------------------------------------------------------
_MSG_ERREUR_STREAM = "Erreur interne lors du traitement de la question."
_MSG_ECHEC_SYNTHESE = "Erreur lors de la génération de la réponse."

class Orchestrateur:
    """Orchestrateur central de Regulatory Agent V2.

    Paramètres :
        mode : "real" (défaut) ou "mock".
               Peut aussi être contrôlé via ORCHESTRATEUR_MODE=mock dans .env.

    En mode "real" :
      - Le Retriever appelle Qdrant via src/agents/retriever.py.
      - Les agents non encore implémentés (Temporal, Explainer, Citation)
        sont contournés proprement avec un log explicite.

    En mode "mock" :
      - Tous les agents retournent des données simulées.
      - Aucune dépendance externe requise (Qdrant, Redis, modèles MLX).
      - Utile pour les tests de l'API sans infrastructure.
    """

    def __init__(self, mode: str | None = None) -> None:
        """Initialise l'orchestrateur sans charger aucun agent en mémoire.

        Args:
            mode: "real" ou "mock". Si None, lit `cfg.orchestrateur_mode`
                  (renseigné via ORCHESTRATEUR_MODE dans .env), défaut "real".
        """
        self.mode = _resoudre_mode(mode)
        self._retriever: Retriever | None = None
        self._ingester: Ingester | None = None
        # Sérialise l'usage du registre MLX : le verrou empêche deux appels
        # concurrents de charger/évincer un modèle pendant qu'une autre requête
        # génère encore avec lui (cf. `_executer_bloquant`).
        self._verrou_agents = asyncio.Lock()
        logger.info("Orchestrateur initialisé — mode=%s", self.mode)

    # ------------------------------------------------------------------
    # Accès lazy aux agents
    # ------------------------------------------------------------------

    def _obtenir_retriever(self) -> Retriever:
        """Retourne le Retriever réel, créé au premier appel."""
        if self._retriever is None:
            from src.agents.retriever import Retriever

            self._retriever = Retriever()
            logger.info("Retriever réel initialisé.")
        return self._retriever

    def _obtenir_ingester(self) -> Ingester:
        """Retourne l'Ingester réel (scripts/ingest.py), créé au premier appel."""
        if self._ingester is None:
            from scripts.ingest import Ingester

            self._ingester = Ingester(collection_name=cfg.qdrant_collection)
            logger.info("Ingester réel initialisé.")
        return self._ingester

    async def _executer_bloquant(
        self,
        fonction: Callable[..., T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Exécute un appel synchrone (MLX) sur l'unique thread MLX du processus.

        Le thread est dédié et permanent (`mlx_utils.EXECUTEUR_MLX`) et non un
        worker quelconque du pool par défaut : MLX lie le stream GPU et l'état
        du cache de prompt au thread qui crée les tableaux, donc charger le
        modèle sur un thread et générer sur un autre échoue aléatoirement
        (« There is no Stream(gpu, 2) in current thread »).

        Le verrou sérialise l'accès au registre MLX : deux appels concurrents
        ne peuvent pas évincer le modèle pendant qu'une autre requête génère.
        """
        async with self._verrou_agents:
            return await executer_mlx(fonction, *args, **kwargs)

    async def _nouveau_client_redis(self) -> ClientRedis:
        """Client Redis asynchrone (client maison, cf. src/redis_client.py)."""
        from src.redis_client import nouveau_client

        return nouveau_client(
            host=cfg.redis_host,
            port=cfg.redis_port,
            password=cfg.redis_password,
            db=cfg.redis_db,
            timeout_secondes=cfg.redis_timeout_secondes,
        )

    @staticmethod
    def _machine_pour_agent(nom_agent: str) -> str:
        """Retourne la machine d'exécution attendue pour un agent donné.

        Utilisée pour renseigner SortieAgent.machine dans l'audit trail.
        Un nom d'agent inconnu déclenche un warning et retourne "inconnue"
        plutôt que de renvoyer une machine erronée par défaut.

        Args:
            nom_agent: Nom de l'agent (conservé pour compatibilité de signature ;
                       ignoré sous l'architecture unique m4pro2).

        Returns:
            Le nom réel de la machine d'exécution (via `platform.node()`),
            ou `"inconnue"` si le hostname n'a pas pu être résolu.
        """
        del nom_agent  # Signature préservée pour ne pas casser les appelants.
        return _MACHINE

    # ------------------------------------------------------------------
    # Étapes du pipeline — mode real
    # ------------------------------------------------------------------

    # `_etape_retrieval`, `_etape_temporal`, `_etape_explainer` ont été
    # déplacées vers src/orchestrator_pipeline.py (§12 étape 6). Les
    # méthodes ci-dessous sont conservées comme wrappers minces pour ne
    # rien changer aux callers.

    async def _etape_retrieval(
        self,
        question: str,
        date_contexte: date | None,
        filtres_themes: list[str],
        filtres_sources: list[SourceReglementaire],
    ) -> tuple[list[EvidenceRecuperee], SortieAgent]:
        """Étape 1 déléguée à src.orchestrator_pipeline."""
        from src.orchestrator_pipeline import etape_retrieval

        return await etape_retrieval(
            self, question, date_contexte, filtres_themes, filtres_sources
        )

    async def _etape_temporal(
        self,
        question: str,
        date_contexte: date | None,
        evidences: list[EvidenceRecuperee],
    ) -> tuple[list[EvidenceRecuperee], SortieAgent]:
        """Étape 2 déléguée à src.orchestrator_pipeline."""
        from src.orchestrator_pipeline import etape_temporal

        return await etape_temporal(self, question, date_contexte, evidences)

    async def _etape_explainer(
        self,
        question: str,
        evidences: list[EvidenceRecuperee],
        type_pipeline: str,
        date_ref: date | None = None,
    ) -> tuple[str, NiveauConfiance, SortieAgent]:
        """Étape 3 déléguée à src.orchestrator_pipeline."""
        from src.orchestrator_pipeline import etape_explainer

        return await etape_explainer(self, question, evidences, type_pipeline, date_ref)

    # ------------------------------------------------------------------
    # Pipeline principal
    # ------------------------------------------------------------------

    async def traiter(self, requete: RequeteQuestion) -> ReponseQuestion:
        """Point d'entrée du pipeline multi-agent (route selon `self.mode`).

        Args:
            requete: Question et paramètres de l'utilisateur.

        Returns:
            ReponseQuestion avec réponse, preuves et niveau de confiance.
        """
        request_id = uuid4()
        type_pipeline = _classifier_requete(requete.question, requete.date_contexte)
        _journaliser_debut_traitement(request_id, self.mode, type_pipeline, requete)
        if self.mode == "mock":
            return await self._traiter_mock(requete, request_id, type_pipeline)
        return await self._traiter_pipeline_reel(requete, request_id, type_pipeline)

    async def traiter_stream(
        self, requete: RequeteQuestion
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Pipeline en flux : émet ('etape'|'token'|'fin'|'erreur', charge).

        Même pipeline que `traiter` mais la synthèse Explainer est diffusée
        fragment par fragment ; citation + finalisation (HITL, audit) ont
        lieu après le dernier token, avant l'événement 'fin'.
        """
        request_id = uuid4()
        type_pipeline = _classifier_requete(requete.question, requete.date_contexte)
        _journaliser_debut_traitement(request_id, self.mode, type_pipeline, requete)
        try:
            if self.mode == "mock":
                reponse = await self._traiter_mock(requete, request_id, type_pipeline)
                yield "token", {"t": reponse.reponse}
                yield "fin", reponse.model_dump(mode="json")
                return
            async for evenement in self._stream_pipeline_reel(
                requete, request_id, type_pipeline
            ):
                yield evenement
        except Exception:
            logger.exception("traiter_stream échoué")
            yield "erreur", {"detail": _MSG_ERREUR_STREAM}

    async def _stream_pipeline_reel(
        self, requete: RequeteQuestion, request_id: UUID, type_pipeline: str
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Retrieval, synthèse diffusée token par token, puis citations.

        L'émission des tokens reste ici : c'est ce générateur qui décide ce qui
        part vers le client SSE (un helper ne peut pas rendre de valeur).
        """
        agents: list[SortieAgent] = []
        morceaux: list[str] = []
        yield "etape", {"phase": "recherche"}
        evidences = await self._executer_retrieval_et_etapes(
            requete, request_id, type_pipeline, agents
        )
        if evidences is None:
            async for evenement in self._flux_retrieval_indisponible(request_id):
                yield evenement
            return
        async for evenement in self._flux_phases_synthese():
            yield evenement
        async for fragment in self._diffuser_synthese(
            requete, type_pipeline, evidences, morceaux
        ):
            yield "token", {"t": fragment}
        yield "etape", {"phase": "citations"}
        reponse = await self._finaliser_flux(
            requete, request_id, evidences, agents, morceaux
        )
        yield "fin", reponse.model_dump(mode="json")

    async def _diffuser_synthese(
        self,
        requete: RequeteQuestion,
        type_pipeline: str,
        evidences: list[EvidenceRecuperee],
        morceaux: list[str],
    ) -> AsyncIterator[str]:
        """Produit la synthèse en flux et accumule les fragments dans `morceaux`.

        Les fragments sont collectés par l'appelant (le générateur ne peut pas
        rendre de valeur) : la reconstruction du texte final et l'évaluation de
        confiance restent ainsi dans `_stream_pipeline_reel`.
        """
        agent = self._agent_explainer()
        async for fragment in self._stream_sous_verrou(
            lambda: agent.expliquer_stream(
                question=requete.question,
                evidences=evidences,
                date_ref=requete.date_contexte,
                type_pipeline=type_pipeline,
            )
        ):
            morceaux.append(fragment)
            yield fragment

    @staticmethod
    def _agent_explainer() -> Any:
        """Instancie l'Explainer LLM (import local : évite le cycle au boot)."""
        from src.agents.explainer import AgentExplainer

        return AgentExplainer(use_llm=True)

    async def _finaliser_flux(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        evidences: list[EvidenceRecuperee],
        agents: list[SortieAgent],
        morceaux: list[str],
    ) -> ReponseQuestion:
        """Clôt le flux : confiance, trace d'audit, citations, HITL et audit."""
        from src.agents.explainer import _evaluer_confiance

        texte = "".join(morceaux).strip() or _MSG_ECHEC_SYNTHESE
        confiance = _evaluer_confiance(texte, evidences)
        agents.append(
            SortieAgent(
                nom_agent="Explainer",
                machine=_MACHINE,
                contenu={
                    "mode": "stream",
                    "evidences_utilisees": len(evidences),
                    "fragments": len(morceaux),
                    "niveau_confiance": confiance.value,
                },
            )
        )
        return await self._finaliser_avec_citations(
            requete, request_id, evidences, agents, texte, confiance
        )

    @staticmethod
    async def _flux_phases_synthese() -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Événements de progression avant la synthèse (temporel, synthèse)."""
        yield "etape", {"phase": "temporel"}
        yield "etape", {"phase": "synthese"}

    @staticmethod
    async def _flux_retrieval_indisponible(
        request_id: UUID,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Événements SSE d'une recherche indisponible (token + fin)."""
        reponse = _reponse_retrieval_indisponible(request_id)
        yield "token", {"t": reponse.reponse}
        yield "fin", reponse.model_dump(mode="json")

    async def _executer_retrieval_et_etapes(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        type_pipeline: str,
        agents: list[SortieAgent],
    ) -> list[EvidenceRecuperee] | None:
        """Retrieval puis étapes intermédiaires ; None si le retrieval a échoué."""
        evidences_ou_none = await self._executer_retrieval_avec_repli(requete, agents)
        if evidences_ou_none is None:
            return None
        return await self._executer_etapes_intermediaires(
            requete, type_pipeline, evidences_ou_none, agents, request_id
        )

    async def _finaliser_avec_citations(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        evidences: list[EvidenceRecuperee],
        agents: list[SortieAgent],
        texte: str,
        confiance: NiveauConfiance,
    ) -> ReponseQuestion:
        """Vérifie les citations puis persiste (HITL + audit) et rend la réponse."""
        confiance = await self._appliquer_citation(texte, evidences, agents, confiance)
        return await self._finaliser_reponse(
            requete, request_id, evidences, agents, texte, confiance
        )

    async def _appliquer_citation(
        self,
        texte: str,
        evidences: list[EvidenceRecuperee],
        agents: list[SortieAgent],
        confiance: NiveauConfiance,
    ) -> NiveauConfiance:
        """Vérifie les citations et ajuste la confiance en conséquence."""
        from src.agents.citation import sources_referencees

        evidences_citees = sources_referencees(texte, evidences)
        resultat = await self._executer_citation(evidences_citees, texte, agents)
        return _confiance_apres_citation(confiance, resultat)

    @staticmethod
    def _pomper_generateur(
        generateur_factory: Callable[[], Iterator[str]],
        file: queue.Queue[tuple[str, Any]],
    ) -> None:
        """Exécute le générateur synchrone (MLX) et alimente la file.

        Appelé dans un thread : toute exception est transmise par la file
        plutôt que perdue dans le thread.
        """
        try:
            for fragment in generateur_factory():
                file.put(("f", fragment))
        except Exception as exc:  # noqa: BLE001 — remonté côté async
            file.put(("e", exc))
        finally:
            file.put(("fin", None))

    async def _stream_sous_verrou(
        self, generateur_factory: Callable[[], Iterator[str]]
    ) -> AsyncIterator[str]:
        """Pompe un générateur synchrone (MLX) sur le thread MLX, sous le verrou.

        Le générateur tourne sur `EXECUTEUR_MLX` (thread unique permanent) et
        non sur un worker du pool par défaut : MLX exige de charger et de
        générer sur le même thread. La lecture bloquante de la file, elle,
        reste sur le pool par défaut — la placer sur l'unique thread MLX
        bloquerait la génération qu'elle alimente.
        """
        file: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=128)
        loop = asyncio.get_running_loop()
        async with self._verrou_agents:
            futur = loop.run_in_executor(
                EXECUTEUR_MLX, self._pomper_generateur, generateur_factory, file
            )
            while True:
                genre, charge = await loop.run_in_executor(None, file.get)
                if genre == "f":
                    yield charge
                elif genre == "e":
                    await futur
                    raise charge
                else:
                    break
            await futur

    async def _traiter_mock(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        type_pipeline: str,
    ) -> ReponseQuestion:
        """Produit une ReponseQuestion simulée + audit, sans dépendance externe."""
        reponse = (
            f"[MODE MOCK] Question reçue : '{requete.question}' "
            f"| type : {type_pipeline} | date : {requete.date_contexte}"
        )
        await self._persister_audit(
            _construire_audit_mock(requete, request_id, reponse)
        )
        return ReponseQuestion(
            request_id=request_id,
            reponse=reponse,
            niveau_confiance=NiveauConfiance.INCERTAIN,
        )

    async def _traiter_pipeline_reel(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        type_pipeline: str,
    ) -> ReponseQuestion:
        """Exécute les 4 étapes du pipeline réel puis assemble la ReponseQuestion."""
        agents_executes: list[SortieAgent] = []
        etape = await self._executer_etapes_pipeline(
            requete,
            request_id,
            type_pipeline,
            agents_executes,
        )
        if etape is None:
            return _reponse_retrieval_indisponible(request_id)
        evidences, reponse_texte, niveau_confiance = etape
        return await self._finaliser_reponse(
            requete,
            request_id,
            evidences,
            agents_executes,
            reponse_texte,
            niveau_confiance,
        )

    async def _executer_etapes_pipeline(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        type_pipeline: str,
        agents_executes: list[SortieAgent],
    ) -> tuple[list[EvidenceRecuperee], str, NiveauConfiance] | None:
        """Exécute retrieval → étapes intermédiaires → explainer → citation."""
        evidences_ou_none = await self._executer_retrieval_avec_repli(
            requete, agents_executes
        )
        if evidences_ou_none is None:
            return None
        evidences = await self._executer_etapes_intermediaires(
            requete, type_pipeline, evidences_ou_none, agents_executes, request_id
        )
        reponse_texte, confiance = await self._executer_explainer_avec_repli(
            requete, type_pipeline, evidences, agents_executes
        )
        confiance = await self._appliquer_citation(
            reponse_texte, evidences, agents_executes, confiance
        )
        return evidences, reponse_texte, confiance

    async def _executer_retrieval_avec_repli(
        self,
        requete: RequeteQuestion,
        agents_executes: list[SortieAgent],
    ) -> list[EvidenceRecuperee] | None:
        """Retourne les evidences ou None si le Retriever a échoué (loggé)."""
        try:
            return await self._executer_retrieval(requete, agents_executes)
        except Exception:
            logger.exception("Retrieval échoué")
            return None

    async def _executer_etapes_intermediaires(
        self,
        requete: RequeteQuestion,
        type_pipeline: str,
        evidences: list[EvidenceRecuperee],
        agents_executes: list[SortieAgent],
        request_id: UUID,
    ) -> list[EvidenceRecuperee]:
        """Étapes 2 (temporal) et 2b (conflit) — évidences après filtrage."""
        evidences = await self._executer_temporal_si_applicable(
            requete,
            type_pipeline,
            evidences,
            agents_executes,
        )
        await self._executer_conflit_si_applicable(
            requete,
            type_pipeline,
            evidences,
            agents_executes,
            request_id,
        )
        return evidences

    async def _executer_retrieval(
        self,
        requete: RequeteQuestion,
        agents_executes: list[SortieAgent],
    ) -> list[EvidenceRecuperee]:
        """Étape 1 : appelle le Retriever et ajoute sa SortieAgent à la trace."""
        evidences, sortie = await self._etape_retrieval(
            question=requete.question,
            date_contexte=requete.date_contexte,
            filtres_themes=requete.filtres_themes,
            filtres_sources=requete.filtres_sources,
        )
        agents_executes.append(sortie)
        return evidences

    async def _executer_temporal_si_applicable(
        self,
        requete: RequeteQuestion,
        type_pipeline: str,
        evidences: list[EvidenceRecuperee],
        agents_executes: list[SortieAgent],
    ) -> list[EvidenceRecuperee]:
        """Étape 2 : filtre temporel (activé pour `type_pipeline == 'temporelle'`)."""
        if type_pipeline != "temporelle" or not evidences:
            return evidences
        try:
            evidences, sortie = await self._etape_temporal(
                question=requete.question,
                date_contexte=requete.date_contexte,
                evidences=evidences,
            )
            agents_executes.append(sortie)
        except Exception as exc:  # noqa: BLE001 — frontière externe : dégradation gracieuse, cf. skill §8
            logger.warning("Agent Temporal échoué, ignoré : %s", exc)
        return evidences

    async def _executer_conflit_si_applicable(
        self,
        requete: RequeteQuestion,
        type_pipeline: str,
        evidences: list[EvidenceRecuperee],
        agents_executes: list[SortieAgent],
        request_id: UUID,
    ) -> None:
        """Étape 2b : détection de conflit (activée si `type_pipeline == 'conflit'`).

        `etape_conflit` renvoie toujours une SortieAgent (M9) : en cas
        d'échec de l'agent, elle porte l'erreur et l'audit en garde la trace.
        """
        if type_pipeline != "conflit" or len(evidences) < 2:
            return
        from src.orchestrator_pipeline import etape_conflit

        sortie = await etape_conflit(
            self,
            question=requete.question,
            date_contexte=requete.date_contexte,
            evidences=evidences,
            request_id=request_id,
        )
        agents_executes.append(sortie)

    async def _executer_explainer_avec_repli(
        self,
        requete: RequeteQuestion,
        type_pipeline: str,
        evidences: list[EvidenceRecuperee],
        agents_executes: list[SortieAgent],
    ) -> tuple[str, NiveauConfiance]:
        """Étape 3 : synthèse Explainer ; sur échec, renvoie un message + INCERTAIN."""
        try:
            reponse, confiance, sortie = await self._etape_explainer(
                question=requete.question,
                evidences=evidences,
                type_pipeline=type_pipeline,
                date_ref=requete.date_contexte,
            )
            agents_executes.append(sortie)
        except Exception:
            logger.exception("Explainer échoué")
            return (
                "Erreur lors de la génération de la réponse.",
                NiveauConfiance.INCERTAIN,
            )
        return reponse, confiance

    async def _executer_citation(
        self,
        evidences: list[EvidenceRecuperee],
        reponse_texte: str,
        agents_executes: list[SortieAgent],
    ) -> ResultatCitation | None:
        """Étape 4 : citations + verdict d'ancrage (ignorée si échec)."""
        from src.orchestrator_pipeline import etape_citation

        reponse = reponse_texte if cfg.citation_verifie_ancrage else None
        paire = await etape_citation(
            self, evidences=evidences, reponse_explainer=reponse
        )
        if paire is None:
            return None
        sortie, resultat = paire
        agents_executes.append(sortie)
        return resultat

    async def _finaliser_reponse(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        evidences: list[EvidenceRecuperee],
        agents_executes: list[SortieAgent],
        reponse_texte: str,
        niveau_confiance: NiveauConfiance,
    ) -> ReponseQuestion:
        """Soumet à validation si besoin, persiste l'audit, renvoie la réponse."""
        soumettre, tache_id = await self._soumettre_et_tracer(
            requete,
            request_id,
            evidences,
            agents_executes,
            reponse_texte,
            niveau_confiance,
        )
        return _construire_reponse_question(
            request_id,
            reponse_texte,
            evidences,
            niveau_confiance,
            soumettre,
            tache_id,
            mode_reponse=mode_depuis_agents(agents_executes),
        )

    async def _soumettre_et_tracer(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        evidences: list[EvidenceRecuperee],
        agents_executes: list[SortieAgent],
        reponse_texte: str,
        niveau_confiance: NiveauConfiance,
    ) -> tuple[bool, UUID | None]:
        """Soumet à validation humaine puis persiste l'audit ; rend la soumission.

        H1 : l'audit et la réponse reçoivent la soumission EFFECTIVE — une
        panne Redis ne doit ni faire échouer /ask, ni faire croire qu'une
        validation humaine est en attente alors qu'aucune tâche n'existe.
        """
        soumettre, tache_id = await self._soumettre_validation_si_besoin(
            requete,
            request_id,
            reponse_texte,
            niveau_confiance,
            _doit_soumettre_validation(requete, niveau_confiance),
        )
        await self._construire_et_persister_audit(
            requete,
            request_id,
            evidences,
            agents_executes,
            reponse_texte,
            niveau_confiance,
            soumettre,
        )
        return soumettre, tache_id

    async def _construire_et_persister_audit(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        evidences: list[EvidenceRecuperee],
        agents_executes: list[SortieAgent],
        reponse_texte: str,
        niveau_confiance: NiveauConfiance,
        soumettre: bool,
    ) -> None:
        """Assemble puis persiste l'EnregistrementAudit du pipeline réel."""
        audit = _construire_audit_reel(
            requete,
            request_id,
            evidences,
            agents_executes,
            reponse_texte,
            niveau_confiance,
            soumettre,
        )
        await self._persister_audit(audit)

    async def _soumettre_validation_si_besoin(
        self,
        requete: RequeteQuestion,
        request_id: UUID,
        reponse_texte: str,
        niveau_confiance: NiveauConfiance,
        soumettre: bool,
    ) -> tuple[bool, UUID | None]:
        """Enregistre une TacheValidation Redis quand `soumettre` est True.

        H1 : Redis indisponible ne fait PAS échouer toute la réponse /ask —
        l'échec est journalisé en ERROR et la soumission est rapportée comme
        non effectuée.

        Returns:
            `(soumission_effective, tache_id)` : `(False, None)` si
            `soumettre` est False ou si l'enregistrement a échoué ;
            `(True, tache_id)` sinon.
        """
        if not soumettre:
            return False, None
        tache = TacheValidation(
            type_file=TypeFilePendante.REPONSES,
            request_id=request_id,
            contenu={
                "question": requete.question,
                "reponse": reponse_texte,
                "niveau_confiance": niveau_confiance.value,
            },
        )
        try:
            await self._enregistrer_tache_redis(tache)
        except QueueBackendError:
            logger.exception(
                "Tâche de validation non enregistrée (Redis indisponible) — "
                "réponse renvoyée sans validation en attente"
            )
            return False, None
        return True, tache.tache_id

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def ingerer(self, requete: RequeteIngestion) -> ReponseIngestion:
        """Ingère un document réglementaire (chunking + embedding + upsert Qdrant).

        Mode mock : renvoie une ReponseIngestion factice. Mode real :
        délègue à `orchestrator_ingest.ingerer_sync` sur le thread MLX
        (l'ingestion embedde les chunks).

        Raises:
            MissingMetadataError / InvalidDocumentError : contenu_json absent
                ou invalide vis-à-vis du schéma DocumentReglementaire.
            DocumentAlreadyIndexedError : document déjà indexé sans
                `forcer_reindexation`.
        """
        logger.info(
            "Ingestion déclenchée : source=%s forcer_reindexation=%s",
            requete.source,
            requete.forcer_reindexation,
        )
        if self.mode == "mock":
            return _reponse_ingestion_mock(requete)
        return await executer_mlx(self._ingerer_sync, requete)

    def _ingerer_sync(self, requete: RequeteIngestion) -> ReponseIngestion:
        """Ingestion synchrone déléguée à src.orchestrator_ingest."""
        from src.orchestrator_ingest import ingerer_sync

        return ingerer_sync(self._obtenir_ingester, requete)

    # ------------------------------------------------------------------
    # Human-in-the-loop
    # ------------------------------------------------------------------

    async def lister_taches_pendantes(self) -> ReponseTachesPendantes:
        """Récupère les tâches en attente depuis Redis (délégué)."""
        from src.orchestrator_validation import (
            lister_taches_pendantes as _lister,
        )

        return await _lister(self._nouveau_client_redis)

    async def valider_tache(
        self,
        tache_id: UUID,
        decision: StatutValidation,
        commentaire: str | None = None,
    ) -> ReponseDecisionValidation:
        """Applique une décision humaine à une tâche Redis (délégué)."""
        from src.orchestrator_validation import valider_tache as _valider

        return await _valider(
            self._nouveau_client_redis, tache_id, decision, commentaire
        )

    async def obtenir_tache(self, tache_id: UUID) -> TacheValidation | None:
        """Récupère une tâche par id (pendante ou traitée) depuis Redis (délégué)."""
        from src.orchestrator_validation import obtenir_tache as _obtenir

        return await _obtenir(self._nouveau_client_redis, tache_id)

    # ------------------------------------------------------------------
    # Méthodes internes
    # ------------------------------------------------------------------

    async def _enregistrer_tache_redis(self, tache: TacheValidation) -> None:
        """Enregistre une tâche dans la file Redis (délégué)."""
        from src.orchestrator_validation import (
            enregistrer_tache_redis as _enregistrer,
        )

        await _enregistrer(self._nouveau_client_redis, tache)

    async def _persister_audit(self, audit: EnregistrementAudit) -> None:
        """Persiste l'audit (JSONL local + PostgreSQL) ; jamais bloquant."""
        try:
            from src.audit import obtenir_gestionnaire

            gestionnaire = await obtenir_gestionnaire()
            hash_courant = await gestionnaire.persister(audit)
            _journaliser_audit_succes(audit, hash_courant)
        except Exception:
            logger.exception("Audit échoué (non bloquant)")
            _journaliser_audit_fallback(audit)
