#!/bin/bash
# scripts/ingerer_corpus_complet.sh
# Téléchargement et ingestion du corpus réglementaire complet
# Usage : cd ~/regulatory-agent && bash scripts/ingerer_corpus_complet.sh

set -e
cd ~/regulatory-agent
source venv/bin/activate

UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
INGEST="python3 scripts/ingest.py --json"
CONVERT="python3 scripts/pdf_to_json.py"

dl() {
  local url="$1" fichier="$2"
  echo "  Téléchargement : $fichier"
  curl -L "$url" -o "data/raw/$fichier" --user-agent "$UA" -s --show-error
  if file "data/raw/$fichier" | grep -q HTML; then
    echo "  ⚠️  Résultat HTML — téléchargement manuel requis : $url"
    rm "data/raw/$fichier"
    return 1
  fi
  echo "  ✅ PDF OK"
}

convert_ingest() {
  local fichier="$1" id="$2" titre="$3" source="$4" pub="$5" vig="$6" themes="$7"
  if [ ! -f "data/raw/$fichier" ]; then
    echo "  ⚠️  Fichier absent, ignoré : $fichier"
    return
  fi
  $CONVERT --fichier "data/raw/$fichier" --id "$id" --titre "$titre" \
    --source "$source" --publication "$pub" --vigueur "$vig" --themes "$themes" \
    && $INGEST "data/raw/$id.json" \
    && echo "  ✅ $id ingéré" \
    || echo "  ❌ Erreur sur $id"
}

echo ""
echo "════════════════════════════════════════════════"
echo "  Regulatory Agent V2 — Ingestion corpus complet"
echo "════════════════════════════════════════════════"
echo ""

# ════════════════════════════════════════════════
# THÈME 1 — PROTECTION DES DONNÉES / CYBERSÉCURITÉ
# ════════════════════════════════════════════════
echo "── Thème 1 : Protection des données / Cybersécurité ──"

# RGPD — TÉLÉCHARGEMENT MANUEL
# https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/\?uri\=CELEX:32016R0679
echo "# RGPD : télécharger manuellement depuis EUR-Lex et placer dans data/raw/rgpd_2016_679.pdf"

# NIS2
# TÉLÉCHARGEMENT MANUEL : https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/\?uri\=CELEX:32022L2555
echo "# NIS2 : télécharger manuellement depuis EUR-Lex et placer dans data/raw/nis2_2022_2555.pdf"

# DORA
# TÉLÉCHARGEMENT MANUEL : https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/\?uri\=CELEX:32022R2554
echo "# DORA : télécharger manuellement depuis EUR-Lex et placer dans data/raw/dora_2022_2554.pdf"

# Guide CNIL sécurité 2023
dl "https://www.cnil.fr/sites/default/files/atoms/files/cnil_guide_securite_des_donnees_personnelles-2023.pdf" \
   "cnil_guide_secu_2023.pdf" && \
convert_ingest "cnil_guide_secu_2023.pdf" "CNIL_GUIDE_SECU_2023" \
  "Guide CNIL sécurité des données personnelles 2023" "CNIL" \
  "2023-04-01" "2023-04-01" "securite,protection_donnees,rgpd"

# Guide ANSSI EBIOS Risk Manager
dl "https://www.ssi.gouv.fr/uploads/2018/10/guide-methode-ebios-risk-manager.pdf" \
   "anssi_ebios_rm.pdf" && \
convert_ingest "anssi_ebios_rm.pdf" "ANSSI_EBIOS_RM" \
  "ANSSI — Méthode EBIOS Risk Manager" "ANSSI" \
  "2018-10-01" "2018-10-01" "cybersecurite,gestion_risques,methode"

# Guide ANSSI Ransomware
dl "https://www.ssi.gouv.fr/uploads/2020/09/anssi-guide-attaques_par_rancongiciels_tous_concernes-v1.0.pdf" \
   "anssi_guide_ransomware.pdf" && \
convert_ingest "anssi_guide_ransomware.pdf" "ANSSI_GUIDE_RANSOMWARE" \
  "ANSSI — Attaques par rançongiciels, tous concernés" "ANSSI" \
  "2020-09-01" "2020-09-01" "cybersecurite,ransomware,incident"

# ════════════════════════════════════════════════
# THÈME 2 — SÉCURITÉ DES MACHINES
# ════════════════════════════════════════════════
echo ""
echo "── Thème 2 : Sécurité des machines ──"

# Directive Machines 2006/42/CE — TÉLÉCHARGEMENT MANUEL
echo "# Directive Machines : télécharger manuellement et placer dans data/raw/directive_machines_2006_42.pdf"
echo "# URL : https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32006L0042"

# Règlement Machines 2023/1230 — TÉLÉCHARGEMENT MANUEL
echo "# Règlement Machines 2023 : télécharger manuellement et placer dans data/raw/reglement_machines_2023_1230.pdf"
echo "# URL : https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32023R1230"

# Directive ATEX 2014/34/UE — TÉLÉCHARGEMENT MANUEL
echo "# ATEX 2014/34 : télécharger manuellement et placer dans data/raw/atex_2014_34.pdf"
echo "# URL : https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32014L0034"

# Directive ATEX 1999/92/CE — TÉLÉCHARGEMENT MANUEL
echo "# ATEX 1999/92 : télécharger manuellement et placer dans data/raw/atex_1999_92.pdf"
echo "# URL : https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:31999L0092"

# Guide INRS sécurité machines
dl "https://www.inrs.fr/dms/inrs/CataloguePapier/ED/TI-ED-6122/ed6122.pdf" \
   "inrs_securite_machines.pdf" && \
convert_ingest "inrs_securite_machines.pdf" "INRS_SECURITE_MACHINES" \
  "INRS — Sécurité des machines" "Autre" \
  "2020-01-01" "2020-01-01" "securite_machines,directive_machine,prevention"

# ════════════════════════════════════════════════
# THÈME 3 — SANTÉ ET SÉCURITÉ AU TRAVAIL
# ════════════════════════════════════════════════
echo ""
echo "── Thème 3 : Santé et sécurité au travail ──"

# Directive cadre 89/391/CEE — TÉLÉCHARGEMENT MANUEL
echo "# Directive cadre SST : télécharger manuellement et placer dans data/raw/directive_sst_89_391.pdf"
echo "# URL : https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:31989L0391"

# Guide INRS Document Unique
dl "https://www.inrs.fr/dms/inrs/CataloguePapier/ED/TI-ED-6139/ed6139.pdf" \
   "inrs_document_unique.pdf" && \
convert_ingest "inrs_document_unique.pdf" "INRS_DOCUMENT_UNIQUE" \
  "INRS — Document unique d'évaluation des risques" "Autre" \
  "2021-01-01" "2021-01-01" "sante_travail,evaluation_risques,document_unique"

# Guide INRS risques chimiques
dl "https://www.inrs.fr/dms/inrs/CataloguePapier/ED/TI-ED-697/ed697.pdf" \
   "inrs_risques_chimiques.pdf" && \
convert_ingest "inrs_risques_chimiques.pdf" "INRS_RISQUES_CHIMIQUES" \
  "INRS — Risques chimiques en entreprise" "Autre" \
  "2019-01-01" "2019-01-01" "sante_travail,risques_chimiques,prevention"

# ════════════════════════════════════════════════
# THÈME 4 — ENVIRONNEMENT
# ════════════════════════════════════════════════
echo ""
echo "── Thème 4 : Environnement ──"

# Directive IED 2010/75/UE — TÉLÉCHARGEMENT MANUEL
echo "# Directive IED : télécharger manuellement et placer dans data/raw/directive_ied_2010_75.pdf"
echo "# URL : https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32010L0075"

# Directive Déchets 2008/98/CE — TÉLÉCHARGEMENT MANUEL
echo "# Directive Déchets : télécharger manuellement et placer dans data/raw/directive_dechets_2008_98.pdf"
echo "# URL : https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32008L0098"

# REACH 2006/1907/CE — TÉLÉCHARGEMENT MANUEL
echo "# REACH : télécharger manuellement et placer dans data/raw/reach_2006_1907.pdf"
echo "# URL : https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32006R1907"

# Guide INERIS ICPE
dl "https://www.ineris.fr/sites/ineris.fr/files/contribution/Documents/guide_icpe_ineris.pdf" \
   "ineris_guide_icpe.pdf" && \
convert_ingest "ineris_guide_icpe.pdf" "INERIS_GUIDE_ICPE" \
  "INERIS — Guide installations classées ICPE" "INERIS" \
  "2022-01-01" "2022-01-01" "environnement,icpe,installations_classees"

# ════════════════════════════════════════════════
# THÈME 5 — ÉNERGIE
# ════════════════════════════════════════════════
echo ""
echo "── Thème 5 : Énergie ──"

# Décret tertiaire (Décret n°2019-771)
dl "https://www.legifrance.gouv.fr/download/pdf?id=tXbNbYXFQi2S1ysXBf4sGUZxMT7Pf8D4BFSMv8hOIDA=" \
   "decret_tertiaire_2019.pdf" && \
convert_ingest "decret_tertiaire_2019.pdf" "DECRET_TERTIAIRE_2019" \
  "Décret tertiaire n°2019-771 — Obligations de réduction de consommation énergétique" \
  "Légifrance" "2019-07-25" "2019-10-01" "energie,efficacite_energetique,batiments_tertiaires"

# Directive EED 2023 — TÉLÉCHARGEMENT MANUEL
echo "# Directive EED révisée 2023 : télécharger manuellement et placer dans data/raw/directive_eed_2023.pdf"
echo "# URL : https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32023L1791"

# Directive EPBD 2024 — TÉLÉCHARGEMENT MANUEL
echo "# Directive EPBD 2024 : télécharger manuellement et placer dans data/raw/directive_epbd_2024.pdf"
echo "# URL : https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX:32024L1275"

# ════════════════════════════════════════════════
# INGESTION DES PDFs TÉLÉCHARGÉS MANUELLEMENT
# ════════════════════════════════════════════════
echo ""
echo "── Ingestion des PDFs téléchargés manuellement ──"

# RGPD
[ -f "data/raw/rgpd_2016_679.pdf" ] && \
convert_ingest "rgpd_2016_679.pdf" "RGPD_2016_679_PDF" \
  "Règlement UE 2016/679 — RGPD" "EUR-Lex" \
  "2016-05-04" "2018-05-25" "protection_donnees,vie_privee,numerique"

# NIS2
[ -f "data/raw/nis2_2022_2555.pdf" ] && \
convert_ingest "nis2_2022_2555.pdf" "NIS2_2022_2555_PDF" \
  "Directive UE 2022/2555 — NIS2" "EUR-Lex" \
  "2022-12-27" "2024-10-17" "cybersecurite,securite_reseaux,incidents"

# DORA
[ -f "data/raw/dora_2022_2554.pdf" ] && \
convert_ingest "dora_2022_2554.pdf" "DORA_2022_2554_PDF" \
  "Règlement UE 2022/2554 — DORA" "EUR-Lex" \
  "2022-12-27" "2025-01-17" "cybersecurite,finance,resilience_numerique"

# Directive Machines
[ -f "data/raw/directive_machines_2006_42.pdf" ] && \
convert_ingest "directive_machines_2006_42.pdf" "DIRECTIVE_MACHINES_2006_42_PDF" \
  "Directive 2006/42/CE — Directive Machines" "EUR-Lex" \
  "2006-05-17" "2009-12-29" "securite_machines,directive_machine,marquage_ce"

# Règlement Machines 2023
[ -f "data/raw/reglement_machines_2023_1230.pdf" ] && \
convert_ingest "reglement_machines_2023_1230.pdf" "REGLEMENT_MACHINES_2023_1230_PDF" \
  "Règlement UE 2023/1230 — Machines" "EUR-Lex" \
  "2023-06-29" "2027-01-20" "securite_machines,directive_machine,marquage_ce"

# ATEX 2014/34
[ -f "data/raw/atex_2014_34.pdf" ] && \
convert_ingest "atex_2014_34.pdf" "ATEX_2014_34_PDF" \
  "Directive 2014/34/UE — ATEX équipements" "EUR-Lex" \
  "2014-02-26" "2016-04-20" "securite_machines,atex,zones_explosives"

# ATEX 1999/92
[ -f "data/raw/atex_1999_92.pdf" ] && \
convert_ingest "atex_1999_92.pdf" "ATEX_1999_92_PDF" \
  "Directive 1999/92/CE — ATEX lieux de travail" "EUR-Lex" \
  "1999-12-16" "2003-06-30" "sante_travail,atex,zones_explosives"

# Directive cadre SST
[ -f "data/raw/directive_sst_89_391.pdf" ] && \
convert_ingest "directive_sst_89_391.pdf" "DIRECTIVE_SST_89_391_PDF" \
  "Directive 89/391/CEE — Sécurité et santé au travail" "EUR-Lex" \
  "1989-06-12" "1992-12-31" "sante_travail,securite_travail,prevention_risques"

# Directive IED
[ -f "data/raw/directive_ied_2010_75.pdf" ] && \
convert_ingest "directive_ied_2010_75.pdf" "DIRECTIVE_IED_2010_75_PDF" \
  "Directive 2010/75/UE — Émissions industrielles IED" "EUR-Lex" \
  "2010-11-24" "2013-01-07" "environnement,emissions_industrielles,icpe"

# Directive Déchets
[ -f "data/raw/directive_dechets_2008_98.pdf" ] && \
convert_ingest "directive_dechets_2008_98.pdf" "DIRECTIVE_DECHETS_2008_98_PDF" \
  "Directive 2008/98/CE — Déchets" "EUR-Lex" \
  "2008-11-19" "2010-12-12" "environnement,dechets,recyclage"

# REACH
[ -f "data/raw/reach_2006_1907.pdf" ] && \
convert_ingest "reach_2006_1907.pdf" "REACH_2006_1907_PDF" \
  "Règlement CE 1907/2006 — REACH substances chimiques" "EUR-Lex" \
  "2006-12-18" "2007-06-01" "environnement,chimie,substances_dangereuses"

# EED 2023
[ -f "data/raw/directive_eed_2023.pdf" ] && \
convert_ingest "directive_eed_2023.pdf" "DIRECTIVE_EED_2023_PDF" \
  "Directive UE 2023/1791 — Efficacité énergétique" "EUR-Lex" \
  "2023-09-13" "2023-10-10" "energie,efficacite_energetique,audit_energetique"

# EPBD 2024
[ -f "data/raw/directive_epbd_2024.pdf" ] && \
convert_ingest "directive_epbd_2024.pdf" "DIRECTIVE_EPBD_2024_PDF" \
  "Directive UE 2024/1275 — Performance énergétique bâtiments" "EUR-Lex" \
  "2024-04-24" "2024-05-28" "energie,batiments,performance_energetique"

echo ""
echo "════════════════════════════════════════════════"
echo "  Ingestion terminée"
echo "  Vérification Qdrant :"
echo "  Vérification : connectez-vous sur mini-1 et lancez : python3 -c \"from qdrant_client import QdrantClient; c=QdrantClient(host='127.0.0.1',port=6335); print(c.get_collection('regulatory_chunks').points_count)\""
echo "════════════════════════════════════════════════"
