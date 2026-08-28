"""src/audit_schema.py — Schéma SQL de la table d'audit.

Extrait de src/audit.py (§12 étape 6). Aucune logique — juste la
constante DDL utilisée par `GestionnaireAudit.initialiser()`.
"""

from __future__ import annotations

SQL_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_trail (
    id              SERIAL PRIMARY KEY,
    request_id      UUID NOT NULL,
    horodatage      TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_query      TEXT NOT NULL,
    date_contexte   DATE,
    documents       JSONB DEFAULT '[]',
    agents          JSONB DEFAULT '[]',
    reponse         TEXT,
    niveau_confiance TEXT,
    validation_humaine BOOLEAN DEFAULT FALSE,
    hash_precedent  CHAR(64),
    hash_courant    CHAR(64) NOT NULL,
    CONSTRAINT audit_hash_unique UNIQUE (hash_courant)
);

CREATE INDEX IF NOT EXISTS idx_audit_request_id ON audit_trail (request_id);
CREATE INDEX IF NOT EXISTS idx_audit_horodatage  ON audit_trail (horodatage DESC);
"""
