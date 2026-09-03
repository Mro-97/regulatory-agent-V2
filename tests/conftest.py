"""
tests/conftest.py — Configuration partagée des tests.
Définit l'environnement AVANT l'import de config.py (singleton `cfg`).
"""

from __future__ import annotations

import os

os.environ.setdefault("API_KEY", "cle-de-test-0123456789abcdef")
os.environ.setdefault("ORCHESTRATEUR_MODE", "mock")
os.environ.setdefault("CORS_ORIGINS", "http://testserver")
os.environ.setdefault("POSTGRES_DSN", "")
os.environ.setdefault("REDIS_PASSWORD", "")
os.environ.setdefault("AUDIT_LOCAL_PATH", "/tmp/regulatory_agent_test_audit.jsonl")  # noqa: S108 - TODO 12 etape 4/6 : revue ciblee au moment du typage / de l extraction
os.environ.setdefault("TAILLE_MAX_REQUETE_OCTETS", "2097152")
# Filtre de longueur minimale des chunks désactivé par défaut en test :
# les fixtures d'ingestion utilisent des textes volontairement courts.
# `test_ingest_min_chunk.py` réactive le seuil explicitement.
os.environ.setdefault("INGEST_TAILLE_MIN_CHUNK", "0")


# ---------------------------------------------------------------------------
# Fixtures partagées — extraites de tests/test_integration.py (§12 étape 6)
# ---------------------------------------------------------------------------


import pytest


@pytest.fixture
def doc_rgpd_json() -> dict:  # type: ignore[type-arg]
    """JSON canonique du document RGPD de test — utilisé par plusieurs
    modules d'intégration."""
    return {
        "id": "RGPD_2016_679",
        "titre": "Règlement (UE) 2016/679",
        "source": "EUR-Lex",
        "publication_date": "2016-05-04",
        "entry_into_force": "2018-05-25",
        "version": "2026-08-03",
        "themes": ["protection_donnees", "numerique"],
        "chapitres": [
            {
                "id": "chap4",
                "titre": "Sécurité",
                "articles": [
                    {
                        "id": "art_32",
                        "titre": "Sécurité du traitement",
                        "texte": (
                            "Compte tenu de l'état des connaissances, des coûts "
                            "de mise en œuvre et de la nature du traitement, le responsable "  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
                            "met en œuvre les mesures techniques et organisationnelles appropriées."  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
                        ),
                        "validite": {"valid_from": "2018-05-25", "valid_to": None},
                        "citations": ["art_33"],
                    },
                    {
                        "id": "art_33",
                        "titre": "Notification d'une violation",
                        "texte": (
                            "En cas de violation de données à caractère personnel, "
                            "le responsable notifie l'autorité de contrôle dans les 72 heures."  # noqa: E501 — message ou docstring irréductible, cf. §12 (extraction plutôt que scission)
                        ),
                        "validite": {"valid_from": "2018-05-25", "valid_to": None},
                        "citations": [],
                    },
                ],
            }
        ],
        "textes_lies": [],
    }


@pytest.fixture
def client_qdrant_memoire():  # noqa: ANN201
    """Client Qdrant en mémoire pour les tests."""
    from qdrant_client import QdrantClient

    return QdrantClient(location=":memory:")
