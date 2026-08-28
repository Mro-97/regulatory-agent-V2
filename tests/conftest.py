"""
tests/conftest.py — Configuration partagée des tests.
Définit l'environnement AVANT l'import de config.py (singleton `cfg`).
"""

import os

os.environ.setdefault("API_KEY", "cle-de-test-0123456789abcdef")
os.environ.setdefault("ORCHESTRATEUR_MODE", "mock")
os.environ.setdefault("CORS_ORIGINS", "http://testserver")
os.environ.setdefault("POSTGRES_DSN", "")
os.environ.setdefault("REDIS_PASSWORD", "")
os.environ.setdefault("AUDIT_LOCAL_PATH", "/tmp/regulatory_agent_test_audit.jsonl")  # noqa: S108 - TODO 12 etape 4/6 : revue ciblee au moment du typage / de l extraction
os.environ.setdefault("TAILLE_MAX_REQUETE_OCTETS", "2097152")
