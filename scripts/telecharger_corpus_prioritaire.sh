#!/bin/bash
# scripts/telecharger_corpus_prioritaire.sh
# Télécharge automatiquement les PDFs accessibles
# et liste les URLs à télécharger manuellement via Safari
# Usage : cd ~/regulatory-agent && bash scripts/telecharger_corpus_prioritaire.sh

cd ~/regulatory-agent
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

echo "════════════════════════════════════════════════"
echo "  Téléchargement corpus réglementaire prioritaire"
echo "════════════════════════════════════════════════"
echo ""

# Fonction téléchargement avec vérification PDF
dl() {
  local url="$1" fichier="$2" label="$3"
  echo "  ↓ $label"
  curl -L "$url" -o "data/raw/$fichier" --user-agent "$UA" -s --show-error
  if file "data/raw/$fichier" 2>/dev/null | grep -q "PDF"; then
    echo "    ✅ OK : data/raw/$fichier"
    return 0
  else
    echo "    ❌ Echec (HTML) — à télécharger manuellement"
    rm -f "data/raw/$fichier"
    return 1
  fi
}

# ════════════════════════════════════════
# TÉLÉCHARGEMENTS AUTOMATIQUES
# ════════════════════════════════════════

echo "── ANSSI ──"
dl "https://www.ssi.gouv.fr/uploads/2018/10/guide-methode-ebios-risk-manager.pdf" \
   "anssi_ebios_rm.pdf" "ANSSI — EBIOS Risk Manager"

dl "https://www.ssi.gouv.fr/uploads/2020/09/anssi-guide-attaques_par_rancongiciels_tous_concernes-v1.0.pdf" \
   "anssi_ransomware.pdf" "ANSSI — Ransomware"

dl "https://www.ssi.gouv.fr/uploads/2021/03/anssi-guide-risques-cyber-tpe-pme.pdf" \
   "anssi_guide_tpe_pme.pdf" "ANSSI — Risques cyber TPE/PME"

dl "https://www.ssi.gouv.fr/uploads/2015/10/NP_Crypto_Mecanismes.pdf" \
   "anssi_crypto_mecanismes.pdf" "ANSSI — Mécanismes cryptographiques"

echo ""
echo "── CNIL ──"
dl "https://www.cnil.fr/sites/default/files/atoms/files/cnil_guide_securite_des_donnees_personnelles-2023.pdf" \
   "cnil_guide_secu_2023_v2.pdf" "CNIL — Guide sécurité 2023"

dl "https://www.cnil.fr/sites/default/files/atoms/files/guide_open_source.pdf" \
   "cnil_guide_open_source.pdf" "CNIL — Guide open source"

dl "https://www.cnil.fr/sites/default/files/atoms/files/cnil-guide-rgpd-pour-les-developpeurs.pdf" \
   "cnil_guide_developpeurs.pdf" "CNIL — Guide RGPD développeurs"

echo ""
echo "── INRS ──"
dl "https://www.inrs.fr/dms/inrs/CataloguePapier/ED/TI-ED-6122/ed6122.pdf" \
   "inrs_ed6122_securite_machines.pdf" "INRS — ED6122 Sécurité machines"

dl "https://www.inrs.fr/dms/inrs/CataloguePapier/ED/TI-ED-6009/ed6009.pdf" \
   "inrs_ed6009_equipements_travail.pdf" "INRS — ED6009 Équipements de travail"

dl "https://www.inrs.fr/dms/inrs/CataloguePapier/ED/TI-ED-835/ed835.pdf" \
   "inrs_ed835_duerp.pdf" "INRS — ED835 Document unique"

dl "https://www.inrs.fr/dms/inrs/CataloguePapier/ED/TI-ED-902/ed902.pdf" \
   "inrs_ed902_agents_chimiques.pdf" "INRS — ED902 Agents chimiques"

dl "https://www.inrs.fr/dms/inrs/CataloguePapier/ED/TI-ED-963/ed963.pdf" \
   "inrs_ed963_cmr.pdf" "INRS — ED963 CMR"

dl "https://www.inrs.fr/dms/inrs/CataloguePapier/ED/TI-ED-975/ed975.pdf" \
   "inrs_ed975_bruit.pdf" "INRS — ED975 Bruit au travail"

dl "https://www.inrs.fr/dms/inrs/CataloguePapier/ED/TI-ED-984/ed984.pdf" \
   "inrs_ed984_vibrations.pdf" "INRS — ED984 Vibrations"

dl "https://www.inrs.fr/dms/inrs/CataloguePapier/ED/TI-ED-628/ed628.pdf" \
   "inrs_ed628_manutention.pdf" "INRS — ED628 Manutention manuelle"

echo ""
echo "── INERIS ──"
dl "https://www.ineris.fr/sites/ineris.fr/files/contribution/Documents/Ineris-DRA-13-136871-07218A-guide_ATEX_v3.pdf" \
   "ineris_guide_atex.pdf" "INERIS — Guide ATEX"

echo ""
echo "── ADEME ──"
dl "https://librairie.ademe.fr/ged/6571/guide-audit-energetique-tertiaire-2019.pdf" \
   "ademe_audit_energetique.pdf" "ADEME — Guide audit énergétique"

# ════════════════════════════════════════
# URLS À TÉLÉCHARGER MANUELLEMENT DANS SAFARI
# ════════════════════════════════════════

echo ""
echo "════════════════════════════════════════════════"
echo "  TÉLÉCHARGEMENTS MANUELS REQUIS (EUR-Lex / Légifrance)"
echo "  → Ouvrir dans Safari et sauvegarder dans data/raw/"
echo "════════════════════════════════════════════════"
echo ""

echo "── CRITIQUE ──"
echo "  RGPD            → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32016R0679"
echo "                    Sauvegarder sous : data/raw/rgpd_2016_679.pdf"
echo ""
echo "  NIS2            → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32022L2555"
echo "                    Sauvegarder sous : data/raw/nis2_2022_2555.pdf"
echo ""
echo "  Dir. Machines   → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32006L0042"
echo "                    Sauvegarder sous : data/raw/directive_machines_2006_42.pdf"
echo ""
echo "  Rgt Machines    → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32023R1230"
echo "                    Sauvegarder sous : data/raw/reglement_machines_2023_1230.pdf"
echo ""
echo "  ATEX équip.     → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32014L0034"
echo "                    Sauvegarder sous : data/raw/atex_2014_34.pdf"
echo ""
echo "  ATEX travail    → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:31999L0092"
echo "                    Sauvegarder sous : data/raw/atex_1999_92.pdf"
echo ""
echo "  Dir. cadre SST  → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:31989L0391"
echo "                    Sauvegarder sous : data/raw/directive_sst_89_391.pdf"
echo ""
echo "  REACH           → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32006R1907"
echo "                    Sauvegarder sous : data/raw/reach_2006_1907.pdf"
echo ""
echo "  Dir. IED        → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32010L0075"
echo "                    Sauvegarder sous : data/raw/directive_ied_2010_75.pdf"
echo ""
echo "── IMPORTANT ──"
echo "  DORA            → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32022R2554"
echo "                    Sauvegarder sous : data/raw/dora_2022_2554.pdf"
echo ""
echo "  AI Act          → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32024R1689"
echo "                    Sauvegarder sous : data/raw/ai_act_2024_1689.pdf"
echo ""
echo "  CRA             → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32024R2847"
echo "                    Sauvegarder sous : data/raw/cra_2024_2847.pdf"
echo ""
echo "  Dir. EPI        → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32016R0425"
echo "                    Sauvegarder sous : data/raw/directive_epi_2016_425.pdf"
echo ""
echo "  Dir. Pression   → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32014L0068"
echo "                    Sauvegarder sous : data/raw/directive_desp_2014_68.pdf"
echo ""
echo "  Dir. Déchets    → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32008L0098"
echo "                    Sauvegarder sous : data/raw/directive_dechets_2008_98.pdf"
echo ""
echo "  SEVESO III      → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32012L0018"
echo "                    Sauvegarder sous : data/raw/seveso3_2012_18.pdf"
echo ""
echo "  Dir. Agents Ch. → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:31998L0024"
echo "                    Sauvegarder sous : data/raw/directive_agents_chimiques_98_24.pdf"
echo ""
echo "  Dir. CMR        → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32004L0037"
echo "                    Sauvegarder sous : data/raw/directive_cmr_2004_37.pdf"
echo ""
echo "  EED 2023        → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32023L1791"
echo "                    Sauvegarder sous : data/raw/directive_eed_2023.pdf"
echo ""
echo "── UTILE ──"
echo "  eIDAS           → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32014R0910"
echo "                    Sauvegarder sous : data/raw/eidas_2014_910.pdf"
echo ""
echo "  RoHS            → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32011L0065"
echo "                    Sauvegarder sous : data/raw/rohs_2011_65.pdf"
echo ""
echo "  DEEE            → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32012L0019"
echo "                    Sauvegarder sous : data/raw/deee_2012_19.pdf"
echo ""
echo "  Dir. Eau        → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32000L0060"
echo "                    Sauvegarder sous : data/raw/directive_eau_2000_60.pdf"
echo ""
echo "  EPBD 2024       → https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32024L1275"
echo "                    Sauvegarder sous : data/raw/directive_epbd_2024.pdf"
echo ""
echo "════════════════════════════════════════════════"
echo "  Une fois les PDFs téléchargés dans data/raw/"
echo "  lancer : bash scripts/convertir_et_ingerer.sh"
echo "════════════════════════════════════════════════"
