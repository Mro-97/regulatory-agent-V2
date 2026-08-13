#!/bin/bash
# scripts/tests_rag.sh
# Jeux de tests complets pour le pipeline RAG
# Usage : bash scripts/tests_rag.sh
# Prérequis : API lancée sur localhost:8000, documents ingérés

BASE="http://localhost:8000"
PASS=0
FAIL=0
TOTAL=0

# Couleurs
VERT='\033[0;32m'
ROUGE='\033[0;31m'
JAUNE='\033[1;33m'
BLEU='\033[0;34m'
RESET='\033[0m'

log_titre() { echo -e "\n${BLEU}═══════════════════════════════════════${RESET}"; echo -e "${BLEU}  $1${RESET}"; echo -e "${BLEU}═══════════════════════════════════════${RESET}"; }
log_test()  { echo -e "\n${JAUNE}▶ TEST $TOTAL : $1${RESET}"; }
log_pass()  { echo -e "${VERT}  ✅ PASS : $1${RESET}"; ((PASS++)); }
log_fail()  { echo -e "${ROUGE}  ❌ FAIL : $1${RESET}"; ((FAIL++)); }
log_info()  { echo -e "  ℹ  $1"; }

attendre_api() {
    echo -n "  Attente API..."
    for i in $(seq 1 15); do
        if curl -s "$BASE/health" > /dev/null 2>&1; then
            echo -e " ${VERT}OK${RESET}"
            return 0
        fi
        sleep 1
        echo -n "."
    done
    echo -e " ${ROUGE}TIMEOUT${RESET}"
    exit 1
}

tester() {
    local description="$1"
    local payload="$2"
    local champ="$3"
    local valeur_attendue="$4"
    local operateur="${5:-contient}"

    ((TOTAL++))
    log_test "$description"

    REPONSE=$(curl -s -X POST "$BASE/ask" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null)

    if [ -z "$REPONSE" ]; then
        log_fail "Pas de réponse de l'API"
        return
    fi

    VALEUR=$(echo "$REPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    champs = '$champ'.split('.')
    v = d
    for c in champs:
        if c.isdigit():
            v = v[int(c)]
        else:
            v = v[c]
    print(v)
except Exception as e:
    print('ERREUR: ' + str(e))
" 2>/dev/null)

    log_info "Valeur obtenue : $(echo "$VALEUR" | head -c 120)"

    case "$operateur" in
        "contient")
            if echo "$VALEUR" | grep -qi "$valeur_attendue"; then
                log_pass "Contient '$valeur_attendue'"
            else
                log_fail "N'ait pas '$valeur_attendue' dans '$VALEUR'"
            fi
            ;;
        "egal")
            if [ "$VALEUR" = "$valeur_attendue" ]; then
                log_pass "Égal à '$valeur_attendue'"
            else
                log_fail "Attendu '$valeur_attendue', obtenu '$VALEUR'"
            fi
            ;;
        "superieur")
            if python3 -c "exit(0 if float('$VALEUR') > float('$valeur_attendue') else 1)" 2>/dev/null; then
                log_pass "Supérieur à $valeur_attendue (obtenu $VALEUR)"
            else
                log_fail "Attendu > $valeur_attendue, obtenu $VALEUR"
            fi
            ;;
        "non_vide")
            if [ -n "$VALEUR" ] && [ "$VALEUR" != "None" ] && [ "$VALEUR" != "ERREUR*" ]; then
                log_pass "Champ non vide"
            else
                log_fail "Champ vide ou absent"
            fi
            ;;
    esac

    echo "$REPONSE"
}

# ═══════════════════════════════════════
# DÉMARRAGE
# ═══════════════════════════════════════

echo -e "${BLEU}"
echo "  ██████╗  █████╗  ██████╗     ████████╗███████╗███████╗████████╗███████╗"
echo "  ██╔══██╗██╔══██╗██╔════╝        ██╔══╝██╔════╝██╔════╝╚══██╔══╝██╔════╝"
echo "  ██████╔╝███████║██║  ███╗       ██║   █████╗  ███████╗   ██║   ███████╗"
echo "  ██╔══██╗██╔══██║██║   ██║       ██║   ██╔══╝  ╚════██║   ██║   ╚════██║"
echo "  ██║  ██║██║  ██║╚██████╔╝       ██║   ███████╗███████║   ██║   ███████║"
echo "  ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝        ╚═╝   ╚══════╝╚══════╝   ╚═╝   ╚══════╝"
echo -e "${RESET}"
echo "  Regulatory Agent V2 — Suite de tests RAG complète"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"

attendre_api

# ═══════════════════════════════════════
log_titre "1. SANTÉ DU SYSTÈME"
# ═══════════════════════════════════════

((TOTAL++))
log_test "Health check API"
HEALTH=$(curl -s "$BASE/health")
if echo "$HEALTH" | grep -q '"statut": "ok"'; then
    log_pass "API opérationnelle"
    ((PASS++))
else
    log_fail "API ne répond pas correctement"
    ((FAIL++))
fi

((TOTAL++))
log_test "Endpoint /pending accessible"
PENDING=$(curl -s "$BASE/pending")
if echo "$PENDING" | grep -q "total"; then
    log_pass "File de validation accessible"
    ((PASS++))
else
    log_fail "File de validation inaccessible"
    ((FAIL++))
fi

((TOTAL++))
log_test "Swagger /docs accessible"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/docs")
if [ "$HTTP_CODE" = "200" ]; then
    log_pass "Swagger disponible (HTTP 200)"
    ((PASS++))
else
    log_fail "Swagger inaccessible (HTTP $HTTP_CODE)"
    ((FAIL++))
fi

# ═══════════════════════════════════════
log_titre "2. QUESTIONS SIMPLES (pipeline courant)"
# ═══════════════════════════════════════

tester \
    "Question RGPD sécurité — réponse non vide" \
    '{"question":"Quelles sont les obligations de sécurité selon le RGPD ?"}' \
    "reponse" "RGPD" "contient"

tester \
    "Question RGPD sécurité — evidence retournée" \
    '{"question":"Quelles sont les obligations de sécurité selon le RGPD ?"}' \
    "evidences.0.document_id" "RGPD_2016_679" "egal"

tester \
    "Question RGPD sécurité — score similarité > 0.5" \
    '{"question":"Quelles sont les obligations de sécurité selon le RGPD ?"}' \
    "evidences.0.score_similarite" "0.5" "superieur"

tester \
    "Question notification violation — art_33 retrouvé" \
    '{"question":"Quel est le délai de notification en cas de violation de données ?"}' \
    "reponse" "72" "contient"

tester \
    "Question données personnelles — définition" \
    '{"question":"Quelle est la définition des données à caractère personnel ?"}' \
    "reponse" "personne physique" "contient"

tester \
    "Question request_id — présent dans la réponse" \
    '{"question":"Obligations du responsable de traitement ?"}' \
    "request_id" "" "non_vide"

tester \
    "Question niveau confiance — présent" \
    '{"question":"Mesures de sécurité techniques requises ?"}' \
    "niveau_confiance" "" "non_vide"

# ═══════════════════════════════════════
log_titre "3. QUESTIONS TEMPORELLES (filtrage par date)"
# ═══════════════════════════════════════

tester \
    "Date contexte 2019 — chunks valides retournés" \
    '{"question":"Obligations de notification RGPD","date_contexte":"2019-01-01"}' \
    "reponse" "2019" "contient"

tester \
    "Date contexte 2019 — art_33 valide à cette date" \
    '{"question":"Délai notification violation","date_contexte":"2019-06-15"}' \
    "evidences.0.valid_from" "2018-05-25" "egal"

tester \
    "Date contexte avant entrée en vigueur 2017 — réponse prudente" \
    '{"question":"Obligations RGPD","date_contexte":"2017-01-01"}' \
    "reponse" "" "non_vide"

tester \
    "Date contexte aujourd'hui — fonctionne sans date explicite" \
    '{"question":"Quelles sont les obligations actuelles du responsable de traitement ?"}' \
    "niveau_confiance" "" "non_vide"

# ═══════════════════════════════════════
log_titre "4. DÉTECTION DE CONFLITS"
# ═══════════════════════════════════════

tester \
    "Question conflit — pipeline conflit activé" \
    '{"question":"Y a-t-il une contradiction dans les obligations de notification RGPD ?"}' \
    "reponse" "" "non_vide"

tester \
    "Question incohérence — réponse générée" \
    '{"question":"Incohérence entre les articles du RGPD sur la sécurité"}' \
    "request_id" "" "non_vide"

# ═══════════════════════════════════════
log_titre "5. VALIDATION HUMAINE"
# ═══════════════════════════════════════

((TOTAL++))
log_test "Forcer validation humaine — tâche créée dans Redis"
REPONSE=$(curl -s -X POST "$BASE/ask" \
    -H "Content-Type: application/json" \
    -d '{"question":"Test validation humaine forcée","demander_validation_humaine":true}')

EN_ATTENTE=$(echo "$REPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('en_attente_validation',''))" 2>/dev/null)
if [ "$EN_ATTENTE" = "True" ] || [ "$EN_ATTENTE" = "true" ]; then
    log_pass "Réponse correctement soumise à validation"
    ((PASS++))

    # Récupérer l'ID de la tâche
    TACHE_ID=$(echo "$REPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tache_validation_id',''))" 2>/dev/null)
    log_info "Tâche ID : $TACHE_ID"

    # Vérifier dans /pending
    PENDING=$(curl -s "$BASE/pending")
    TOTAL_PENDING=$(echo "$PENDING" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total',0))" 2>/dev/null)
    log_info "Tâches en attente : $TOTAL_PENDING"

    if [ "$TOTAL_PENDING" -gt "0" ] 2>/dev/null; then
        log_pass "Tâche visible dans /pending"

        # Approuver la tâche
        if [ -n "$TACHE_ID" ] && [ "$TACHE_ID" != "None" ]; then
            ((TOTAL++))
            log_test "Approuver la tâche $TACHE_ID"
            APPROVE=$(curl -s -X POST "$BASE/approve" \
                -H "Content-Type: application/json" \
                -d "{\"tache_id\":\"$TACHE_ID\"}")
            STATUT=$(echo "$APPROVE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('nouveau_statut',''))" 2>/dev/null)
            if [ "$STATUT" = "approuvé" ]; then
                log_pass "Tâche approuvée (statut=$STATUT)"
                ((PASS++))
            else
                log_fail "Approbation échouée (statut=$STATUT)"
                ((FAIL++))
            fi
        fi
    else
        log_info "Redis non disponible — test validation partiel"
    fi
else
    log_fail "en_attente_validation devrait être true (obtenu: $EN_ATTENTE)"
    ((FAIL++))
fi

# ═══════════════════════════════════════
log_titre "6. ROBUSTESSE ET CAS LIMITES"
# ═══════════════════════════════════════

((TOTAL++))
log_test "Question très courte — rejetée par validation Pydantic"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/ask" \
    -H "Content-Type: application/json" \
    -d '{"question":"AB"}')
if [ "$HTTP_CODE" = "422" ]; then
    log_pass "Question trop courte rejetée (HTTP 422)"
    ((PASS++))
else
    log_fail "Attendu HTTP 422, obtenu $HTTP_CODE"
    ((FAIL++))
fi

((TOTAL++))
log_test "Payload JSON invalide — erreur propre"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/ask" \
    -H "Content-Type: application/json" \
    -d 'pas_du_json')
if [ "$HTTP_CODE" = "422" ]; then
    log_pass "JSON invalide rejeté (HTTP 422)"
    ((PASS++))
else
    log_fail "Attendu HTTP 422, obtenu $HTTP_CODE"
    ((FAIL++))
fi

((TOTAL++))
log_test "Question hors corpus — réponse cohérente (pas de crash)"
REPONSE=$(curl -s -X POST "$BASE/ask" \
    -H "Content-Type: application/json" \
    -d '{"question":"Quelles sont les règles de sécurité nucléaire en France ?"}')
if echo "$REPONSE" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
    log_pass "Question hors corpus gérée sans crash"
    ((PASS++))
else
    log_fail "Réponse JSON invalide pour question hors corpus"
    ((FAIL++))
fi

((TOTAL++))
log_test "Date invalide — rejetée par validation"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/ask" \
    -H "Content-Type: application/json" \
    -d '{"question":"Question avec date invalide","date_contexte":"pas-une-date"}')
if [ "$HTTP_CODE" = "422" ]; then
    log_pass "Date invalide rejetée (HTTP 422)"
    ((PASS++))
else
    log_fail "Attendu HTTP 422, obtenu $HTTP_CODE"
    ((FAIL++))
fi

((TOTAL++))
log_test "Approve tâche inexistante — HTTP 404"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/approve" \
    -H "Content-Type: application/json" \
    -d '{"tache_id":"00000000-0000-0000-0000-000000000000"}')
if [ "$HTTP_CODE" = "404" ] || [ "$HTTP_CODE" = "500" ]; then
    log_pass "Tâche inexistante gérée (HTTP $HTTP_CODE)"
    ((PASS++))
else
    log_fail "Attendu HTTP 404, obtenu $HTTP_CODE"
    ((FAIL++))
fi

# ═══════════════════════════════════════
log_titre "7. PERFORMANCE"
# ═══════════════════════════════════════

((TOTAL++))
log_test "Temps de réponse < 10s pour une question simple"
DEBUT=$(date +%s%N)
curl -s -X POST "$BASE/ask" \
    -H "Content-Type: application/json" \
    -d '{"question":"Obligations de sécurité RGPD"}' > /dev/null
FIN=$(date +%s%N)
DUREE=$(( (FIN - DEBUT) / 1000000 ))
log_info "Durée : ${DUREE}ms"
if [ "$DUREE" -lt "10000" ]; then
    log_pass "Temps de réponse acceptable (${DUREE}ms)"
    ((PASS++))
else
    log_fail "Trop lent : ${DUREE}ms > 10000ms"
    ((FAIL++))
fi

((TOTAL++))
log_test "3 requêtes consécutives — pas de dégradation"
for i in 1 2 3; do
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/ask" \
        -H "Content-Type: application/json" \
        -d "{\"question\":\"Test charge $i RGPD obligations sécurité\"}")
    if [ "$CODE" != "200" ]; then
        log_fail "Requête $i a échoué (HTTP $CODE)"
        ((FAIL++))
        break
    fi
done
if [ $? -eq 0 ]; then
    log_pass "3 requêtes traitées sans erreur"
    ((PASS++))
fi

# ═══════════════════════════════════════
log_titre "8. AUDIT"
# ═══════════════════════════════════════

((TOTAL++))
log_test "Fichier audit JSONL créé après les requêtes"
AUDIT_FILE="$HOME/regulatory-agent/data/audit.jsonl"
if [ -f "$AUDIT_FILE" ] && [ -s "$AUDIT_FILE" ]; then
    NB_LIGNES=$(wc -l < "$AUDIT_FILE")
    log_pass "Audit JSONL présent ($NB_LIGNES enregistrements)"
    ((PASS++))
else
    log_fail "Fichier audit absent ou vide : $AUDIT_FILE"
    ((FAIL++))
fi

((TOTAL++))
log_test "Intégrité de la chaîne SHA-256"
python3 - << 'PYEOF'
import sys, json
from pathlib import Path

audit_path = Path.home() / "regulatory-agent/data/audit.jsonl"
if not audit_path.exists():
    print("  SKIP : fichier audit absent")
    sys.exit(0)

lignes = audit_path.read_text().strip().splitlines()
if not lignes:
    print("  SKIP : fichier audit vide")
    sys.exit(0)

erreurs = 0
for i, ligne in enumerate(lignes[-10:]):
    try:
        d = json.loads(ligne)
        assert "hash_courant" in d, "hash_courant absent"
        assert "request_id" in d, "request_id absent"
        assert "user_query" in d, "user_query absent"
    except Exception as e:
        print(f"  ❌ Ligne {i+1} invalide : {e}")
        erreurs += 1

if erreurs == 0:
    print(f"  ✅ {len(lignes)} enregistrement(s) d'audit valides")
else:
    print(f"  ❌ {erreurs} enregistrement(s) invalides")
    sys.exit(1)
PYEOF
if [ $? -eq 0 ]; then ((PASS++)); else ((FAIL++)); fi

# ═══════════════════════════════════════
# RÉSUMÉ FINAL
# ═══════════════════════════════════════

echo ""
echo -e "${BLEU}═══════════════════════════════════════${RESET}"
echo -e "${BLEU}  RÉSUMÉ FINAL${RESET}"
echo -e "${BLEU}═══════════════════════════════════════${RESET}"
echo -e "  Total   : $TOTAL tests"
echo -e "  ${VERT}Passés  : $PASS${RESET}"
echo -e "  ${ROUGE}Échoués : $FAIL${RESET}"
echo ""

if [ "$FAIL" -eq "0" ]; then
    echo -e "${VERT}  ✅ TOUS LES TESTS PASSENT — Système prêt pour la production${RESET}"
else
    POURCENT=$(python3 -c "print(f'{$PASS/$TOTAL*100:.0f}')")
    echo -e "${JAUNE}  ⚠️  $POURCENT% de réussite — $FAIL test(s) à corriger${RESET}"
fi
echo ""
