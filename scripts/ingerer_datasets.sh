#!/bin/bash
# scripts/ingerer_datasets.sh
# Ingère tous les documents du dataset dans Qdrant
# Usage : bash scripts/ingerer_datasets.sh

set -e
cd ~/regulatory-agent
source venv/bin/activate

echo "=== Ingestion des datasets réglementaires ==="
echo ""

DOCS=(
    "data/raw/rgpd_complet.json"
    "data/raw/nis2_2022_2555.json"
    "data/raw/cnil_guide_securite.json"
    "data/raw/anssi_recommandations.json"
)

TOTAL=0
SUCCES=0

for doc in "${DOCS[@]}"; do
    if [ -f "$doc" ]; then
        echo "📄 Ingestion : $doc"
        if python3 scripts/ingest.py --json "$doc"; then
            ((SUCCES++))
            echo "   ✅ OK"
        else
            echo "   ❌ ECHEC"
        fi
        ((TOTAL++))
        echo ""
        sleep 1
    else
        echo "   ⚠️  Fichier absent : $doc"
    fi
done

echo "=== Résumé : $SUCCES/$TOTAL documents ingérés ==="
echo ""
echo "Vérification Qdrant :"
python3 -c "
import sys
sys.path.insert(0, '.')
from config import cfg
from qdrant_client import QdrantClient
client = QdrantClient(host=cfg.qdrant_host, port=cfg.qdrant_port)
try:
    info = client.get_collection(cfg.qdrant_collection)
    print(f'  Collection : {cfg.qdrant_collection}')
    print(f'  Chunks indexés : {info.points_count}')
except Exception as e:
    print(f'  Erreur : {e}')
"
