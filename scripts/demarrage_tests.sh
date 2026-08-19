#!/bin/bash
# scripts/demarrage_tests.sh
# Lance tout ce qu'il faut pour les tests RAG en une commande.
# Usage : bash scripts/demarrage_tests.sh

set -e
cd ~/regulatory-agent
source venv/bin/activate

echo "=== Regulatory Agent V2 — Préparation tests RAG ==="

# 1. Vérifier Qdrant
echo ""
echo "1. Vérification Qdrant..."
if curl -s http://localhost:6333/healthz > /dev/null 2>&1; then
    echo "   ✅ Qdrant opérationnel"
else
    echo "   ❌ Qdrant inaccessible — lancez Qdrant d'abord"
    echo "   Commande : docker run -p 6333:6333 qdrant/qdrant"
    exit 1
fi

# 2. Vérifier Redis
echo ""
echo "2. Vérification Redis..."
if redis-cli ping > /dev/null 2>&1; then
    echo "   ✅ Redis opérationnel"
else
    echo "   ⚠️  Redis inaccessible — les files de validation seront désactivées"
fi

# 3. Initialiser la collection Qdrant
echo ""
echo "3. Initialisation collection Qdrant..."
python3 scripts/setup_qdrant.py
echo "   ✅ Collection prête"

# 4. Ingérer le document de test
echo ""
echo "4. Ingestion du document de test..."
python3 scripts/ingest.py --fichier data/raw/test_rgpd.json
echo "   ✅ Document ingéré"

# 5. Lancer les tests unitaires
echo ""
echo "5. Tests unitaires..."
python3 -m pytest tests/test_models.py tests/test_agents.py -q
echo "   ✅ Tests passés"

# 6. Lancer l'API
echo ""
echo "=== Démarrage API ==="
echo "Interface : http://localhost:8000"
echo "Docs API  : http://localhost:8000/docs"
echo "Ctrl+C pour arrêter"
echo ""
python3 main.py
