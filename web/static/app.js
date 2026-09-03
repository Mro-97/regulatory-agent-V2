"use strict";
const API={ask:"/ask",askStream:"/ask/stream",pending:"/pending",approve:"/approve",reject:"/reject",health:"/health",feedback:"/feedback"};
// C1: la clé API n'est plus injectée dans le HTML. L'utilisateur la saisit
// une fois par onglet, elle est conservée en sessionStorage (jamais persistée).
// Sur 401, apiFetch purge la clé, re-prompte, et retente UNE seule fois.
function _demanderCle(msg){
  const t=msg||"Clé API (X-API-Key) — conservée uniquement pour cet onglet :";
  const saisie=window.prompt(t,"");
  const k=(saisie||"").trim();
  if(k){try{sessionStorage.setItem("apiKey",k);}catch(_){}}
  return k;
}
function _obtenirCle(){
  let k="";try{k=(sessionStorage.getItem("apiKey")||"").trim();}catch(_){}
  return k||_demanderCle();
}
let API_KEY=_obtenirCle();
// Identifiant de navigateur stable (pas un secret) : permet de distinguer
// les postes dans le journal d'accès serveur, même derrière un tunnel SSH
// où toutes les requêtes arrivent en 127.0.0.1.
function _clientId(){
  try{
    let c=localStorage.getItem("clientId");
    if(!c){c=(crypto.randomUUID?crypto.randomUUID():String(Date.now()+Math.random())).slice(0,18);localStorage.setItem("clientId",c);}
    return c;
  }catch(_){return "anon";}
}
const CLIENT_ID=_clientId();
async function apiFetch(url,opts){
  opts=opts||{};
  const h=Object.assign({},opts.headers||{},{"X-API-Key":API_KEY,"X-Client-Id":CLIENT_ID});
  if(opts.body&&!h["Content-Type"])h["Content-Type"]="application/json";
  const r=await fetch(url,Object.assign({},opts,{headers:h}));
  if(r.status===401){
    try{sessionStorage.removeItem("apiKey");}catch(_){}
    _marquerAuth(false,"Clé API refusée");
    const k=_demanderCle("Clé API invalide — veuillez la ressaisir :");
    if(!k)return r;
    API_KEY=k;
    const h2=Object.assign({},opts.headers||{},{"X-API-Key":API_KEY,"X-Client-Id":CLIENT_ID});
    if(opts.body&&!h2["Content-Type"])h2["Content-Type"]="application/json";
    const r2=await fetch(url,Object.assign({},opts,{headers:h2}));
    if(r2.ok)_marquerAuth(true,"Clé API validée");
    return r2;
  }
  return r;
}

// Indicateur visuel d'authentification (sidebar).
function _marquerAuth(ok,sub){
  const dot=document.getElementById("auth-dot");
  const lab=document.getElementById("auth-label");
  const sb=document.getElementById("auth-sub");
  if(!dot||!lab||!sb)return;
  if(ok){
    dot.className="sys-dot";
    lab.textContent="AUTH VALIDÉE";
  }else{
    dot.className="sys-dot error";
    lab.textContent="AUTH REQUISE";
  }
  if(sub)sb.textContent=sub;
}

// Valider la clé au démarrage contre /pending — sans quoi n'importe quelle
// saisie donnait l'illusion de connexion (l'interface `/` et `/health` sont
// publics). Boucle jusqu'à validation ou annulation explicite.
async function validerCleAuDemarrage(){
  while(API_KEY){
    let r;
    try{
      r=await fetch(API.pending,{headers:{"X-API-Key":API_KEY}});
    }catch(_){
      _marquerAuth(false,"API injoignable");
      return false;
    }
    if(r.ok){
      _marquerAuth(true,"Clé API validée");
      return true;
    }
    if(r.status===401){
      try{sessionStorage.removeItem("apiKey");}catch(_){}
      _marquerAuth(false,"Clé refusée par le serveur");
      const k=_demanderCle("Clé API refusée par le serveur — veuillez ressaisir :");
      if(!k){_marquerAuth(false,"Saisie annulée");return false;}
      API_KEY=k;
      continue;
    }
    // 5xx, 503 (API_KEY non configurée côté serveur), etc. — laisser passer.
    _marquerAuth(false,"Serveur indisponible");
    return false;
  }
  _marquerAuth(false,"Aucune clé saisie");
  return false;
}
let enCours=false,sessionQueries=0,filtreActif="all",tachesData=[],activiteSession=[],historiqueSession=[];
const chatMessages=document.getElementById("chat-messages"),champQuestion=document.getElementById("champ-question"),champDate=document.getElementById("champ-date"),btnEnvoyer=document.getElementById("btn-envoyer"),toastZone=document.getElementById("toast-zone");

function switchView(id){
  document.querySelectorAll(".view").forEach(v=>v.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach(n=>n.classList.remove("active"));
  const v=document.getElementById("view-"+id);if(v)v.classList.add("active");
  const n=document.querySelector(`.nav-item[data-view="${id}"]`);if(n)n.classList.add("active");
  const titres={accueil:["Bonjour,","Voici l'essentiel de votre veille réglementaire."],chat:["Chat / Analyse","Interrogez votre corpus réglementaire"],validation:["Validation humaine","Éléments en attente de décision"],historique:["Historique","Toutes les analyses de la session"],sources:["Sources","Corpus réglementaire indexé"],parametres:["Paramètres","Configuration du système"]};
  const t=titres[id]||["Regulatory Agent V2",""];
  document.querySelector(".topbar-left h1").textContent=t[0];
  document.querySelector(".topbar-left p").textContent=t[1];
  if(id==="validation")chargerTaches();
  if(id==="historique")rendrHisto();
}
document.querySelectorAll("[data-view]").forEach(el=>{el.addEventListener("click",e=>{e.preventDefault();switchView(el.dataset.view);});});
document.getElementById("btn-theme").addEventListener("click",()=>document.body.classList.toggle("light"));

function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;").replace(/`/g,"&#96;");}
function heure(iso){return new Date(iso).toLocaleTimeString("fr-FR",{hour:"2-digit",minute:"2-digit"});}
function cls_conf(n){return{élevé:"eleve",moyen:"moyen",faible:"faible"}[n]||"incertain";}
function lbl_conf(n){return{élevé:"Confiance élevée",moyen:"Confiance moyenne",faible:"Confiance faible",incertain:"Incertain"}[n]||n;}
function conf_bandeau(n,enAttente){
  let msg="";
  if(n==="moyen")msg="Fiabilité moyenne — recoupez avec les textes officiels avant tout usage.";
  else if(n!=="élevé")msg="Réponse peu fiable — vérifiez chaque source citée, ne pas utiliser telle quelle.";
  if(enAttente)msg=(msg?msg+" ":"")+"Cette réponse a été transmise pour validation humaine.";
  if(!msg)return"";
  const cls=n==="élevé"?"cb-info":(n==="moyen"?"cb-moyen":"cb-faible");
  return `<div class="conf-bandeau ${cls}">⚠️ ${esc(msg)}</div>`;
}
function est_abroge(vt){if(!vt)return false;const d=new Date(vt);return !isNaN(d.getTime())&&d<new Date();}
// Lien de recherche EUR-Lex pour un document_id qui encode année+numéro
// (ex. RGPD_2016_679, DIRECTIVE_2002_58). Recherche « rapide » : toujours
// une page de résultats, jamais un 404 sur un CELEX deviné. Renvoie "" si
// pas de motif année/numéro (textes nationaux CNIL/ANSSI/INRS).
function lien_eurlex(docId){
  const m=String(docId||"").match(/((?:19|20)\d{2})[_/](\d{1,4})/);
  if(!m)return"";
  return `https://eur-lex.europa.eu/search.html?scope=EURLEX&lang=fr&type=quick&text=${m[1]}%2F${m[2]}`;
}
function wireSuivi(el){
  const b=el.querySelector(".btn-suivi");if(!b)return;
  b.addEventListener("click",async()=>{
    const tid=b.dataset.tid;b.disabled=true;const t0=b.textContent;b.textContent="…";
    try{
      const r=await apiFetch(`/tache/${tid}`);
      if(!r.ok){b.textContent=r.status===404?"Tâche introuvable":"Erreur";return;}
      const t=await r.json();
      const map={"approuvé":["✓ Réponse validée","badge-eleve"],"rejeté":["✗ Réponse rejetée","badge-faible"],"escaladé":["⚠️ Escaladée","badge-moyen"],"en_attente":["⏳ Toujours en attente","badge-attente"]};
      const [txt,cls]=map[t.statut]||[`Statut : ${t.statut}`,"badge-incertain"];
      const span=document.createElement("span");span.className=`badge ${cls}`;span.textContent=txt;b.replaceWith(span);
      if(t.commentaire_validateur)toast(`Validateur : ${t.commentaire_validateur}`,"info");
    }catch{b.textContent=t0;b.disabled=false;toast("Serveur injoignable","error");}
  });
}
function wireSignaler(el){
  const b=el.querySelector(".btn-signaler");if(!b)return;
  b.addEventListener("click",()=>{
    const rid=b.dataset.rid;
    const box=document.createElement("span");box.className="signaler-box";
    box.innerHTML=`<select class="signaler-motif"><option value="reponse_incorrecte">Réponse incorrecte</option><option value="source_hors_sujet">Source hors sujet</option><option value="source_manquante">Source manquante</option><option value="confiance_trompeuse">Confiance trompeuse</option><option value="autre">Autre</option></select><button class="signaler-go">Envoyer</button><button class="signaler-x">×</button>`;
    b.replaceWith(box);
    box.querySelector(".signaler-x").addEventListener("click",()=>box.replaceWith(b));
    box.querySelector(".signaler-go").addEventListener("click",async()=>{
      const motif=box.querySelector(".signaler-motif").value;const go=box.querySelector(".signaler-go");go.disabled=true;
      try{
        const r=await apiFetch(API.feedback,{method:"POST",body:JSON.stringify({request_id:rid,motif})});
        if(r.ok){box.replaceWith(document.createTextNode("✓ Signalé — merci"));toast("Signalement enregistré","info");}
        else{toast("Échec du signalement","error");go.disabled=false;}
      }catch{toast("Serveur inaccessible","error");go.disabled=false;}
    });
  });
}

function toast(msg,type="info",dur=3500){
  const ic={success:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>',error:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',warning:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',info:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>'};
  const el=document.createElement("div");el.className=`toast ${type}`;el.innerHTML=`${ic[type]||ic.info}<span>${esc(msg)}</span>`;toastZone.appendChild(el);
  setTimeout(()=>{el.classList.add("out");setTimeout(()=>el.remove(),210);},dur);
}

async function majKPIs(){
  try{const r=await apiFetch(API.pending);if(r.ok){const d=await r.json();const total=d.total??0;document.getElementById("kpi-pending").textContent=total;const badge=document.getElementById("nav-badge");if(total>0){badge.textContent=total;badge.style.display="inline-flex";document.getElementById("kpi-pending-sub").textContent=`${total} critique${total>1?"s":""}`;}else{badge.style.display="none";document.getElementById("kpi-pending-sub").textContent="aucun en attente";}rendrPendingPreview(d.taches||[]);}}catch{}
  try{const r=await apiFetch(API.health,{signal:AbortSignal.timeout(3000)});if(r.ok){document.getElementById("sys-dot").className="sys-dot";document.getElementById("sys-label").textContent="SYSTÈME OPÉRATIONNEL";document.getElementById("sys-sub").textContent="Tous les services sont actifs";}else throw new Error();}catch{document.getElementById("sys-dot").className="sys-dot error";document.getElementById("sys-label").textContent="SYSTÈME HORS LIGNE";document.getElementById("sys-sub").textContent="API inaccessible";}
  document.getElementById("kpi-analyses").textContent=sessionQueries;
  if(historiqueSession.length>0){const map={élevé:95,moyen:70,faible:40,incertain:30};const moy=Math.round(historiqueSession.reduce((s,h)=>s+(map[h.conf]||50),0)/historiqueSession.length);document.getElementById("kpi-conf").textContent=moy+"%";document.getElementById("kpi-bar").style.width=moy+"%";document.getElementById("kpi-conf-sub").textContent=`session en cours`;}
}

function ajouterActivite(question,conf,ts){
  activiteSession.unshift({question,conf,ts:ts||new Date().toISOString()});
  if(activiteSession.length>8)activiteSession.pop();
  rendrActivite();
}

function rendrActivite(){
  const el=document.getElementById("activity-list");
  if(!activiteSession.length){el.innerHTML=`<div class="activity-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>Aucune activité pour l'instant</div>`;return;}
  el.innerHTML=activiteSession.map(a=>{
    const cls={élevé:"green",moyen:"amber",faible:"red",incertain:"amber"}[a.conf]||"blue";
    const ico=cls==="green"?'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>';
    return `<div class="activity-item"><span class="act-time">${heure(a.ts)}</span><div class="act-icon ${cls}">${ico}</div><div class="act-content"><div class="act-title">Analyse complétée</div><div class="act-sub">${esc(a.question.slice(0,60))}${a.question.length>60?"…":""}</div></div></div>`;
  }).join("");
}

function rendrPendingPreview(taches){
  const el=document.getElementById("pending-preview");
  if(!taches.length){el.innerHTML=`<div class="activity-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>Aucun élément en attente</div>`;return;}
  const niveaux={pending_responses:"eleve",pending_alerts:"critique",pending_links:"moyen"};
  const labels={pending_responses:"ÉLEVÉ",pending_alerts:"CRITIQUE",pending_links:"MOYEN"};
  el.innerHTML=taches.slice(0,3).map(t=>{
    const q=t.contenu?.question||t.contenu?.description||"—";
    const niv=niveaux[t.type_file]||"moyen";
    const lbl=labels[t.type_file]||"ÉLEVÉ";
    const conf=Math.floor(Math.random()*30+60);
    const col=niv==="critique"?"#f85149":niv==="eleve"?"#f0883e":"#4a90d9";
    const circ=2*Math.PI*16;const dash=circ*(1-conf/100);
    return `<div class="pending-item"><div class="pending-head"><span class="pending-level ${niv}">${lbl}</span></div><div class="pending-body"><div><div class="pending-title">${esc(q.slice(0,50))}${q.length>50?"…":""}</div></div><div class="pending-conf"><div class="pending-conf-label">Confiance</div><svg class="conf-ring" viewBox="0 0 36 36"><circle cx="18" cy="18" r="16" fill="none" stroke="var(--border)" stroke-width="3"/><circle cx="18" cy="18" r="16" fill="none" stroke="${col}" stroke-width="3" stroke-dasharray="${circ}" stroke-dashoffset="${dash}" transform="rotate(-90 18 18)" stroke-linecap="round"/></svg><div class="pending-conf-val">${conf}%</div></div><button class="btn-examiner" onclick="switchView('validation')">Examiner</button></div></div>`;
  }).join("");
}

function supprimerWelcome(){document.getElementById("chat-welcome")?.remove();}
function scrollBas(){chatMessages.scrollTop=chatMessages.scrollHeight;}

function ajouterMsgUser(texte){const el=document.createElement("div");el.className="msg-user";el.innerHTML=`<div class="msg-user-bubble">${esc(texte)}</div>`;chatMessages.appendChild(el);scrollBas();}

const ETAPES_PIPELINE=[[0,"Recherche des textes applicables…"],[2200,"Analyse de la validité temporelle…"],[5200,"Rédaction de la synthèse…"],[9000,"Vérification des citations…"]];
let _typingTimers=[];
function ajouterTyping(){
  const el=document.createElement("div");el.className="msg-typing";el.id="typing-tmp";
  el.innerHTML=`<div class="msg-avatar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg></div><div class="typing-bubble"><div class="tp"></div><div class="tp"></div><div class="tp"></div><span class="typing-etape"></span></div>`;
  chatMessages.appendChild(el);scrollBas();
  const lab=el.querySelector(".typing-etape");
  _typingTimers=ETAPES_PIPELINE.map(([ms,txt])=>setTimeout(()=>{if(lab)lab.textContent=txt;},ms));
}
function supprimerTyping(){_typingTimers.forEach(clearTimeout);_typingTimers=[];document.getElementById("typing-tmp")?.remove();}

function md_export(data,question,dateCtx){
  const nl="\n";
  const src=(data.evidences||[]).map(ev=>{
    const fin=ev.valid_to||"en vigueur";const ab=est_abroge(ev.valid_to)?" — n'est plus en vigueur":"";
    const ex=ev.texte_extrait?`${nl}> ${String(ev.texte_extrait).replace(/\s*\n+\s*/g," ")}${nl}`:"";
    const url=lien_eurlex(ev.document_id);
    const lien=url?`${nl}${url}${nl}`:"";
    return `### ${ev.document_id} / ${ev.article_id}${nl}Validité : ${ev.valid_from} → ${fin}${ab}${nl}${lien}${ex}`;
  }).join(nl);
  return `# Question réglementaire${nl}${nl}`+
    `**Question :** ${question||"(non disponible)"}${nl}`+
    (dateCtx?`**Date de contexte :** ${dateCtx}${nl}`:"")+
    `**Généré le :** ${new Date().toISOString()}${nl}`+
    `**Niveau de confiance :** ${lbl_conf(data.niveau_confiance)}${nl}${nl}`+
    `## Réponse${nl}${nl}${data.reponse}${nl}${nl}`+
    `## Sources (${(data.evidences||[]).length})${nl}${nl}${src}${nl}${nl}`+
    `---${nl}⚠️ Réponse générée automatiquement à partir du corpus réglementaire indexé. `+
    `Ne constitue pas un avis juridique — vérifier les textes officiels (EUR-Lex, Légifrance).${nl}`;
}
function telecharger_texte(nom,contenu){
  const blob=new Blob([contenu],{type:"text/markdown;charset=utf-8"});
  const url=URL.createObjectURL(blob);
  const a=document.createElement("a");a.href=url;a.download=nom;
  document.body.appendChild(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function wireExport(el,build){
  const b=el.querySelector(".btn-export");if(!b)return;
  b.addEventListener("click",()=>{
    const d=new Date();const p=n=>String(n).padStart(2,"0");
    const nom=`reponse-reglementaire-${d.getFullYear()}${p(d.getMonth()+1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}.md`;
    telecharger_texte(nom,build());toast("Export Markdown téléchargé","info");
  });
}
function afficherReponse(data,question,dateCtx){
  const nc=cls_conf(data.niveau_confiance);
  let sources="";
  if(data.evidences?.length){
    const items=data.evidences.slice(0,8).map(ev=>{const abroge=est_abroge(ev.valid_to);const fin=ev.valid_to||"en vigueur";const vmark=abroge?" · n'est plus en vigueur":"";const ex=ev.texte_extrait?`<div class="src-excerpt">${esc(ev.texte_extrait.slice(0,160))}...</div>`:"";const url=lien_eurlex(ev.document_id);const ref=`${esc(ev.document_id)} / ${esc(ev.article_id)}`;const refHtml=url?`<a class="src-ref" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${ref} ↗</a>`:`<div class="src-ref">${ref}</div>`;return `<div class="src-item${abroge?" src-abroge":""}">${refHtml}<div class="src-valid${abroge?" abroge":""}">${esc(String(ev.valid_from))} → ${esc(String(fin))}${vmark}</div>${ex}</div>`;}).join("");
    sources=`<button class="sources-toggle"><span>📎 ${data.evidences.length} source${data.evidences.length>1?"s":""} citée${data.evidences.length>1?"s":""}</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg></button><div class="sources-body">${items}</div>`;
  }
  const suivi=data.tache_validation_id?`<button class="btn-suivi" data-tid="${esc(String(data.tache_validation_id))}">Vérifier le statut</button>`:"";
  const attente=data.en_attente_validation?`<span class="badge badge-attente">⏳ En attente de validation</span>${suivi}`:"";
  const bandeau=conf_bandeau(data.niveau_confiance,data.en_attente_validation);
  const el=document.createElement("div");el.className="msg-sys";
  const signaler=data.request_id?`<button class="btn-signaler" data-rid="${esc(String(data.request_id))}">⚑ Signaler</button>`:"";
  const exporter=`<button class="btn-export">⬇ Exporter</button>`;
  el.innerHTML=`<div class="msg-avatar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg></div><div class="msg-sys-inner"><div class="msg-card">${bandeau}<div>${esc(data.reponse)}</div>${sources}</div><div class="msg-meta"><span class="badge badge-${nc}">${lbl_conf(data.niveau_confiance)}</span>${attente}${signaler}${exporter}</div></div>`;
  const btn=el.querySelector(".sources-toggle");const body=el.querySelector(".sources-body");
  if(btn&&body){btn.addEventListener("click",()=>{const o=body.classList.toggle("visible");btn.classList.toggle("open",o);});}
  wireSignaler(el);wireSuivi(el);wireExport(el,()=>md_export(data,question,dateCtx));
  chatMessages.appendChild(el);scrollBas();
}

function afficherErreurChat(msg){const el=document.createElement("div");el.className="msg-sys";el.innerHTML=`<div class="msg-avatar" style="background:var(--red-bg);border-color:rgba(248,81,73,.3);color:var(--red)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg></div><div class="msg-sys-inner"><div class="msg-card" style="color:var(--red)">${esc(msg)}</div></div>`;chatMessages.appendChild(el);scrollBas();}

const PHASES_STREAM={recherche:"Recherche des textes applicables…",temporel:"Analyse de la validité temporelle…",synthese:"Rédaction de la synthèse…",citations:"Vérification des citations…"};

function demarrerCarteStream(){
  const el=document.createElement("div");el.className="msg-sys";
  el.innerHTML=`<div class="msg-avatar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg></div><div class="msg-sys-inner"><div class="msg-card"><div class="stream-txt"></div><div class="stream-etape"></div></div></div>`;
  chatMessages.appendChild(el);scrollBas();
  return {el,txt:el.querySelector(".stream-txt"),etape:el.querySelector(".stream-etape")};
}

function _parseFrameSSE(frame){
  let ev="message",data="";
  for(const ligne of frame.split("\n")){
    if(ligne.startsWith("event:"))ev=ligne.slice(6).trim();
    else if(ligne.startsWith("data:"))data+=ligne.slice(5).trim();
  }
  return {ev,data};
}

async function envoyerQuestionStream(body,question,date,ts){
  let r;
  try{r=await apiFetch(API.askStream,{method:"POST",body:JSON.stringify(body)});}
  catch{return false;}
  if(!r.ok||!r.body||!r.body.getReader)return false;
  const carte=demarrerCarteStream();
  const reader=r.body.getReader();const dec=new TextDecoder();
  let buf="",texte="",fin=null,err=null;
  try{
    for(;;){
      const {value,done}=await reader.read();if(done)break;
      buf+=dec.decode(value,{stream:true});
      let i;
      while((i=buf.indexOf("\n\n"))>=0){
        const frame=buf.slice(0,i);buf=buf.slice(i+2);
        if(!frame.trim())continue;
        const {ev,data}=_parseFrameSSE(frame);
        if(ev==="token"){texte+=JSON.parse(data).t;carte.txt.textContent=texte;scrollBas();}
        else if(ev==="etape"){carte.etape.textContent=PHASES_STREAM[JSON.parse(data).phase]||"";}
        else if(ev==="fin"){fin=JSON.parse(data);}
        else if(ev==="erreur"){err=JSON.parse(data).detail;}
      }
    }
  }catch{err=err||"Flux interrompu.";}
  carte.el.remove();
  if(fin){
    sessionQueries++;
    afficherReponse(fin,question,date);ajouterActivite(question,fin.niveau_confiance,ts);
    historiqueSession.unshift({question,reponse:fin.reponse,conf:fin.niveau_confiance,ts});
    majKPIs();if(fin.en_attente_validation)toast("Réponse soumise à validation humaine","warning");
  }else{
    afficherErreurChat(err||"Réponse incomplète.");toast(err||"Erreur serveur","error");
  }
  return true;
}

async function envoyerQuestionSimple(body,question,date,ts){
  ajouterTyping();
  try{
    const r=await apiFetch(API.ask,{method:"POST",body:JSON.stringify(body)});
    supprimerTyping();
    if(!r.ok){
      const e=await r.json().catch(()=>({}));
      let m=e.detail||`Erreur ${r.status}`;
      if(r.status===429)m="Trop de requêtes — patientez environ une minute avant de réessayer.";
      else if(r.status===503)m="Service momentanément indisponible (modèle occupé ou file d'attente). Réessayez dans quelques instants.";
      afficherErreurChat(m);toast(m,"error");return;
    }
    const data=await r.json();sessionQueries++;
    afficherReponse(data,question,date);ajouterActivite(question,data.niveau_confiance,ts);
    historiqueSession.unshift({question,reponse:data.reponse,conf:data.niveau_confiance,ts});
    majKPIs();if(data.en_attente_validation)toast("Réponse soumise à validation humaine","warning");
  }catch{supprimerTyping();afficherErreurChat("Impossible de joindre l'API. Vérifiez que le serveur est démarré.");toast("Serveur inaccessible","error");}
}

document.getElementById("src-filtres")?.addEventListener("click",e=>{
  const b=e.target.closest(".src-tag");if(b)b.classList.toggle("actif");
});
function sourcesSelectionnees(){
  return [...document.querySelectorAll("#src-filtres .src-tag.actif")].map(b=>b.dataset.src);
}

async function envoyerQuestion(){
  const question=champQuestion.value.trim();if(!question||enCours)return;
  supprimerWelcome();enCours=true;btnEnvoyer.disabled=true;
  const date=champDate.value||null;champQuestion.value="";champQuestion.style.height="46px";
  ajouterMsgUser(question);const ts=new Date().toISOString();
  const body={question};if(date)body.date_contexte=date;
  const srcs=sourcesSelectionnees();if(srcs.length)body.filtres_sources=srcs;
  try{
    const stream=await envoyerQuestionStream(body,question,date,ts);
    if(!stream)await envoyerQuestionSimple(body,question,date,ts);
  }finally{enCours=false;btnEnvoyer.disabled=false;champQuestion.focus();}
}

document.getElementById("btn-new-chat").addEventListener("click",()=>{chatMessages.innerHTML=`<div class="chat-welcome" id="chat-welcome"><div class="welcome-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg></div><h2>Que souhaitez-vous analyser ?</h2><p>Le système interroge le corpus réglementaire indexé,<br>identifie les versions applicables et cite chaque source précisément.</p><div class="chips"><button class="chip" data-q="Quelles sont les obligations de notification d'une violation de données selon le RGPD ?">Notification violation · RGPD art. 33</button><button class="chip" data-q="Quelles mesures de sécurité techniques sont requises par l'article 32 du RGPD ?">Mesures sécurité · art. 32</button><button class="chip" data-q="Y a-t-il une contradiction entre les délais de notification du RGPD et de NIS2 ?">Conflit RGPD / NIS2</button><button class="chip" data-q="Quelles sont les obligations des entités essentielles selon NIS2 ?">Entités essentielles · NIS2</button></div></div>`;activerChips();});

async function chargerTaches(){
  try{const r=await apiFetch(API.pending);if(!r.ok)return;const d=await r.json();tachesData=d.taches||[];const pf=d.par_file||{};
  document.getElementById("vk-total").textContent=d.total??0;document.getElementById("vk-responses").textContent=pf.pending_responses??0;document.getElementById("vk-alerts").textContent=pf.pending_alerts??0;document.getElementById("vk-links").textContent=pf.pending_links??0;
  const badge=document.getElementById("nav-badge");if((d.total||0)>0){badge.textContent=d.total;badge.style.display="inline-flex";}else badge.style.display="none";
  rendrVal();rendrPendingPreview(tachesData);}catch{}
}

function rendrVal(){
  const liste=document.getElementById("val-list");
  const filtrees=filtreActif==="all"?tachesData:tachesData.filter(t=>t.type_file===filtreActif);
  if(!filtrees.length){liste.innerHTML=`<div class="activity-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg><p>Aucune tâche en attente</p></div>`;return;}
  liste.innerHTML="";
  const labels={pending_responses:"Réponse critique",pending_alerts:"Alerte réglementaire",pending_links:"Lien proposé",pending_weights:"Paramètre"};
  filtrees.forEach(t=>{
    const dt=new Date(t.horodatage_creation);const ds=dt.toLocaleString("fr-FR",{day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"});
    const q=t.contenu?.question||t.contenu?.description||"—";const tp=t.type_file||"pending_responses";
    const el=document.createElement("div");el.className="val-card";
    el.innerHTML=`<div class="val-card-head"><span class="val-type t-${tp}">${labels[tp]||tp}</span><span class="val-date">${ds}</span></div><div class="val-card-body"><div class="val-q">${esc(q)}</div><div class="val-actions"><button class="btn-approve"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>Approuver</button><button class="btn-reject"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>Rejeter</button></div></div>`;
    el.querySelector(".btn-approve").onclick=()=>decider(t.tache_id,"approve",el);
    el.querySelector(".btn-reject").onclick=()=>decider(t.tache_id,"reject",el);
    liste.appendChild(el);
  });
}

async function decider(id,action,el){
  const ep=action==="approve"?API.approve:API.reject;
  try{const r=await apiFetch(ep,{method:"POST",body:JSON.stringify({tache_id:id})});
  if(!r.ok){toast("Erreur lors du traitement","error");return;}
  toast(action==="approve"?"Tâche approuvée":"Tâche rejetée","success");
  el.style.transition="opacity 180ms,transform 180ms";el.style.opacity="0";el.style.transform="translateX(12px)";
  setTimeout(()=>{el.remove();chargerTaches();majKPIs();},200);}catch{toast("Serveur inaccessible","error");}
}

document.querySelectorAll(".filter").forEach(f=>{f.addEventListener("click",()=>{document.querySelectorAll(".filter").forEach(x=>x.classList.remove("active"));f.classList.add("active");filtreActif=f.dataset.filter;rendrVal();});});
document.getElementById("btn-refresh-val")?.addEventListener("click",chargerTaches);

function rendrHisto(){
  const el=document.getElementById("hist-list");
  if(!historiqueSession.length){el.innerHTML=`<div class="activity-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg><p>Aucune analyse dans l'historique</p></div>`;return;}
  el.innerHTML=historiqueSession.map(h=>{const nc=cls_conf(h.conf);return `<div class="hist-item"><div class="hist-head"><div class="hist-q">${esc(h.question)}</div><div class="hist-time">${heure(h.ts)}</div></div><div class="hist-preview">${esc(h.reponse.slice(0,200))}...</div><div class="hist-meta"><span class="badge badge-${nc}">${lbl_conf(h.conf)}</span></div></div>`;}).join("");
}

function activerChips(){document.querySelectorAll(".chip[data-q]").forEach(c=>{c.addEventListener("click",()=>{champQuestion.value=c.dataset.q;champQuestion.dispatchEvent(new Event("input"));switchView("chat");champQuestion.focus();});});}
activerChips();
btnEnvoyer.addEventListener("click",envoyerQuestion);
champQuestion.addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();envoyerQuestion();}});
champQuestion.addEventListener("input",()=>{champQuestion.style.height="46px";champQuestion.style.height=Math.min(champQuestion.scrollHeight,130)+"px";});

// Validation de la clé AVANT tout polling, pour éviter l'illusion de
// connexion quand /health (public) affiche "OPÉRATIONNEL" alors que la
// clé saisie est en fait rejetée.
validerCleAuDemarrage().finally(()=>{
  majKPIs();chargerTaches();
  setInterval(majKPIs,30000);setInterval(chargerTaches,30000);
});