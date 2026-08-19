#!/bin/bash
# scripts/convertir_et_ingerer.sh
# Convertit tous les PDFs présents dans data/raw/ et les ingère
# Usage : cd ~/regulatory-agent && bash scripts/convertir_et_ingerer.sh

cd ~/regulatory-agent
source venv/bin/activate

conv() {
  local fichier="$1" id="$2" titre="$3" source="$4" pub="$5" vig="$6" themes="$7"
  [ ! -f "data/raw/$fichier" ] && echo "  ⏭  Absent : $fichier" && return
  echo "=== $id ==="
  python3 scripts/pdf_to_json.py \
    --fichier "data/raw/$fichier" --id "$id" --titre "$titre" \
    --source "$source" --publication "$pub" --vigueur "$vig" --themes "$themes" \
  && python3 scripts/ingest.py --json "data/raw/$id.json" \
  && echo "  ✅ $id ingéré" \
  || echo "  ❌ Erreur sur $id"
}

echo "═══════════════════════════════════════════"
echo "  Conversion et ingestion corpus complet"
echo "═══════════════════════════════════════════"

# ── CYBERSÉCURITÉ / DONNÉES ──
conv "rgpd_2016_679.pdf" "RGPD_2016_679_FULL" "RGPD — Règlement UE 2016/679" "EUR-Lex" "2016-05-04" "2018-05-25" "protection_donnees,vie_privee,numerique"
conv "nis2_2022_2555.pdf" "NIS2_2022_2555_FULL" "NIS2 — Directive UE 2022/2555" "EUR-Lex" "2022-12-27" "2024-10-17" "cybersecurite,securite_reseaux,incidents"
conv "dora_2022_2554.pdf" "DORA_2022_2554_FULL" "DORA — Règlement UE 2022/2554" "EUR-Lex" "2022-12-27" "2025-01-17" "cybersecurite,finance,resilience_numerique"
conv "ai_act_2024_1689.pdf" "AI_ACT_2024_1689" "AI Act — Règlement UE 2024/1689" "EUR-Lex" "2024-07-12" "2026-08-02" "intelligence_artificielle,numerique,conformite"
conv "cra_2024_2847.pdf" "CRA_2024_2847" "Cyber Resilience Act — Règlement UE 2024/2847" "EUR-Lex" "2024-11-20" "2024-12-10" "cybersecurite,produits_numeriques,vulnerabilites"
conv "eidas_2014_910.pdf" "EIDAS_2014_910" "eIDAS — Règlement UE 910/2014" "EUR-Lex" "2014-07-23" "2016-07-01" "numerique,identite_numerique,confiance"
conv "anssi_ebios_rm.pdf" "ANSSI_EBIOS_RM" "ANSSI — Méthode EBIOS Risk Manager" "ANSSI" "2018-10-01" "2018-10-01" "cybersecurite,gestion_risques,methode"
conv "anssi_ransomware.pdf" "ANSSI_RANSOMWARE" "ANSSI — Guide ransomware" "ANSSI" "2020-09-01" "2020-09-01" "cybersecurite,ransomware,incident"
conv "anssi_guide_tpe_pme.pdf" "ANSSI_GUIDE_TPE_PME" "ANSSI — Guide cyber TPE/PME" "ANSSI" "2021-03-01" "2021-03-01" "cybersecurite,tpe_pme,bonnes_pratiques"
conv "anssi_crypto_mecanismes.pdf" "ANSSI_CRYPTO" "ANSSI — Mécanismes cryptographiques" "ANSSI" "2015-10-01" "2015-10-01" "cybersecurite,cryptographie,chiffrement"
conv "cnil_guide_developpeurs.pdf" "CNIL_GUIDE_DEV" "CNIL — Guide RGPD développeurs" "CNIL" "2020-01-01" "2020-01-01" "protection_donnees,developpement,rgpd"
conv "cnil_guide_open_source.pdf" "CNIL_GUIDE_OPENSOURCE" "CNIL — Guide open source" "CNIL" "2023-01-01" "2023-01-01" "protection_donnees,open_source,logiciel"

# ── MACHINES ──
conv "directive_machines_2006_42.pdf" "DIR_MACHINES_2006_42" "Directive Machines 2006/42/CE" "EUR-Lex" "2006-05-17" "2009-12-29" "securite_machines,directive_machine,marquage_ce"
conv "reglement_machines_2023_1230.pdf" "RGT_MACHINES_2023_1230" "Règlement Machines UE 2023/1230" "EUR-Lex" "2023-06-29" "2027-01-20" "securite_machines,directive_machine,marquage_ce"
conv "atex_2014_34.pdf" "ATEX_2014_34" "Directive ATEX équipements 2014/34/UE" "EUR-Lex" "2014-02-26" "2016-04-20" "securite_machines,atex,zones_explosives"
conv "atex_1999_92.pdf" "ATEX_1999_92" "Directive ATEX travailleurs 1999/92/CE" "EUR-Lex" "1999-12-16" "2003-06-30" "sante_travail,atex,zones_explosives"
conv "directive_desp_2014_68.pdf" "DESP_2014_68" "Directive Pression DESP 2014/68/UE" "EUR-Lex" "2014-05-15" "2016-06-01" "securite_machines,pression,equipements"
conv "directive_epi_2016_425.pdf" "EPI_2016_425" "Règlement EPI UE 2016/425" "EUR-Lex" "2016-03-09" "2018-04-21" "sante_travail,epi,protection_individuelle"
conv "inrs_ed6122_securite_machines.pdf" "INRS_ED6122" "INRS ED6122 — Sécurité des machines" "Autre" "2020-01-01" "2020-01-01" "securite_machines,prevention,inrs"
conv "ineris_guide_atex.pdf" "INERIS_GUIDE_ATEX" "INERIS — Guide ATEX zones explosibles" "INERIS" "2013-01-01" "2013-01-01" "securite_machines,atex,zones_explosives"

# ── SANTÉ ET SÉCURITÉ AU TRAVAIL ──
conv "directive_sst_89_391.pdf" "DIR_SST_89_391" "Directive cadre SST 89/391/CEE" "EUR-Lex" "1989-06-12" "1992-12-31" "sante_travail,securite_travail,prevention_risques"
conv "directive_agents_chimiques_98_24.pdf" "DIR_AGENTS_CHIM_98_24" "Directive agents chimiques 98/24/CE" "EUR-Lex" "1998-04-07" "2001-05-05" "sante_travail,agents_chimiques,exposition"
conv "directive_cmr_2004_37.pdf" "DIR_CMR_2004_37" "Directive CMR 2004/37/CE" "EUR-Lex" "2004-04-29" "2005-01-06" "sante_travail,cmr,agents_cancerogenes"
conv "inrs_ed835_duerp.pdf" "INRS_ED835" "INRS ED835 — Document unique" "Autre" "2021-01-01" "2021-01-01" "sante_travail,evaluation_risques,duerp"
conv "inrs_ed6009_equipements_travail.pdf" "INRS_ED6009" "INRS ED6009 — Équipements de travail" "Autre" "2020-01-01" "2020-01-01" "sante_travail,equipements_travail,prevention"
conv "inrs_ed902_agents_chimiques.pdf" "INRS_ED902" "INRS ED902 — Agents chimiques" "Autre" "2019-01-01" "2019-01-01" "sante_travail,agents_chimiques,prevention"
conv "inrs_ed963_cmr.pdf" "INRS_ED963" "INRS ED963 — CMR" "Autre" "2021-01-01" "2021-01-01" "sante_travail,cmr,agents_cancerogenes"
conv "inrs_ed975_bruit.pdf" "INRS_ED975" "INRS ED975 — Bruit au travail" "Autre" "2020-01-01" "2020-01-01" "sante_travail,bruit,exposition"
conv "inrs_ed984_vibrations.pdf" "INRS_ED984" "INRS ED984 — Vibrations" "Autre" "2020-01-01" "2020-01-01" "sante_travail,vibrations,exposition"
conv "inrs_ed628_manutention.pdf" "INRS_ED628" "INRS ED628 — Manutention manuelle" "Autre" "2019-01-01" "2019-01-01" "sante_travail,manutention,prevention"

# ── ENVIRONNEMENT ──
conv "directive_ied_2010_75.pdf" "DIR_IED_2010_75" "Directive IED 2010/75/UE" "EUR-Lex" "2010-11-24" "2013-01-07" "environnement,emissions_industrielles,icpe"
conv "directive_dechets_2008_98.pdf" "DIR_DECHETS_2008_98" "Directive Déchets 2008/98/CE" "EUR-Lex" "2008-11-19" "2010-12-12" "environnement,dechets,recyclage"
conv "reach_2006_1907.pdf" "REACH_2006_1907" "REACH — Règlement CE 1907/2006" "EUR-Lex" "2006-12-18" "2007-06-01" "environnement,chimie,substances_dangereuses"
conv "seveso3_2012_18.pdf" "SEVESO3_2012_18" "Directive SEVESO III 2012/18/UE" "EUR-Lex" "2012-07-04" "2015-06-01" "environnement,risques_majeurs,icpe"
conv "rohs_2011_65.pdf" "ROHS_2011_65" "Directive RoHS 2011/65/UE" "EUR-Lex" "2011-06-08" "2013-01-02" "environnement,substances_dangereuses,electronique"
conv "deee_2012_19.pdf" "DEEE_2012_19" "Directive DEEE 2012/19/UE" "EUR-Lex" "2012-07-04" "2014-02-14" "environnement,dechets,electronique"
conv "directive_eau_2000_60.pdf" "DIR_EAU_2000_60" "Directive Eau 2000/60/CE" "EUR-Lex" "2000-10-23" "2000-12-22" "environnement,eau,pollution"
conv "ademe_audit_energetique.pdf" "ADEME_AUDIT_ENERGETIQUE" "ADEME — Guide audit énergétique" "Autre" "2019-01-01" "2019-01-01" "energie,audit_energetique,efficacite"

# ── ÉNERGIE ──
conv "directive_eed_2023.pdf" "DIR_EED_2023" "Directive Efficacité Énergétique 2023/1791" "EUR-Lex" "2023-09-13" "2023-10-10" "energie,efficacite_energetique,audit"
conv "directive_epbd_2024.pdf" "DIR_EPBD_2024" "Directive Performance Bâtiments 2024/1275" "EUR-Lex" "2024-04-24" "2024-05-28" "energie,batiments,performance_energetique"
conv "eidas_2014_910.pdf" "EIDAS_2014_910" "eIDAS — Règlement UE 910/2014" "EUR-Lex" "2014-07-23" "2016-07-01" "numerique,identite_numerique,confiance"

echo ""
echo "═══════════════════════════════════════════"
echo "  Conversion terminée"
echo "  Synchroniser vers les Mac Mini :"
echo "  bash scripts/sync_corpus_mac_mini.sh"
echo "═══════════════════════════════════════════"
