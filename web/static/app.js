/**
 * app.js — Interface Regulatory Agent V2
 * JS vanilla, aucune dépendance externe.
 * Communique avec l'API FastAPI via fetch().
 */

"use strict";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const API = {
  ask:     "/ask",
  pending: "/pending",
  approve: "/approve",
  reject:  "/reject",
};

// ---------------------------------------------------------------------------
// État
// ---------------------------------------------------------------------------

let enCours = false;

// ---------------------------------------------------------------------------
// Utilitaires DOM
// ---------------------------------------------------------------------------

const filMessages    = document.getElementById("fil-messages");
const champQuestion  = document.getElementById("champ-question");
const champDate      = document.getElementById("champ-date");
const btnEnvoyer     = document.getElementById("btn-envoyer");
const listeTaches    = document.getElementById("liste-taches");
const compteurTaches = document.getElementById("compteur-taches");
const toast          = document.getElementById("toast");

function afficherToast(message, duree = 3000) {
  toast.textContent = message;
  toast.classList.add("visible");
  setTimeout(() => toast.classList.remove("visible"), duree);
}

function scrollBas() {
  filMessages.scrollTop = filMessages.scrollHeight;
}

function echapper(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// Affichage des messages
// ---------------------------------------------------------------------------

function ajouterMessageUtilisateur(texte) {
  const el = document.createElement("div");
  el.className = "message-utilisateur";
  el.textContent = texte;
  filMessages.appendChild(el);
  scrollBas();
}

function ajouterIndicateurSaisie() {
  const el = document.createElement("div");
  el.className = "message-systeme";
  el.id = "indicateur-saisie-tmp";
  el.innerHTML = `
    <div class="indicateur-saisie">
      <div class="point"></div>
      <div class="point"></div>
      <div class="point"></div>
    </div>`;
  filMessages.appendChild(el);
  scrollBas();
  return el;
}

function supprimerIndicateur() {
  const el = document.getElementById("indicateur-saisie-tmp");
  if (el) el.remove();
}

function niveauVersClasse(niveau) {
  const map = {
    "élevé":    "eleve",
    "moyen":    "moyen",
    "faible":   "faible",
    "incertain":"incertain",
  };
  return map[niveau] || "incertain";
}

function niveauVersLibelle(niveau) {
  const map = {
    "élevé":    "Confiance élevée",
    "moyen":    "Confiance moyenne",
    "faible":   "Confiance faible",
    "incertain":"Confiance incertaine",
  };
  return map[niveau] || niveau;
}

function afficherReponse(data) {
  const niveauClasse = niveauVersClasse(data.niveau_confiance);

  // Bloc sources
  let sourcesHTML = "";
  if (data.evidences && data.evidences.length > 0) {
    const items = data.evidences
      .slice(0, 6)
      .map(ev => {
        const fin = ev.valid_to || "en vigueur";
        return `<div class="source-item">${echapper(ev.document_id)} / ${echapper(ev.article_id)} [${echapper(ev.valid_from)} → ${echapper(fin)}]</div>`;
      })
      .join("");
    sourcesHTML = `
      <div class="sources-bloc">
        <div class="sources-label">Sources</div>
        ${items}
      </div>`;
  }

  // Badge validation en attente
  const badgeValidation = data.en_attente_validation
    ? `<div class="badge-validation">⏳ En attente de validation humaine</div>`
    : "";

  const el = document.createElement("div");
  el.className = "message-systeme";
  el.innerHTML = `
    <div class="bulle-systeme">
      <div class="barre-confiance ${niveauClasse}"></div>
      <div class="bulle-corps">${echapper(data.reponse)}</div>
      <div class="badge-confiance ${niveauClasse}">${niveauVersLibelle(data.niveau_confiance)}</div>
      ${badgeValidation}
      ${sourcesHTML}
    </div>`;

  filMessages.appendChild(el);
  scrollBas();
}

function afficherErreur(message) {
  const el = document.createElement("div");
  el.className = "message-systeme";
  el.innerHTML = `
    <div class="bulle-systeme">
      <div class="barre-confiance faible"></div>
      <div class="bulle-corps" style="color:#f87171">⚠ ${echapper(message)}</div>
    </div>`;
  filMessages.appendChild(el);
  scrollBas();
}

// ---------------------------------------------------------------------------
// Envoi d'une question
// ---------------------------------------------------------------------------

async function envoyerQuestion() {
  const question = champQuestion.value.trim();
  if (!question || enCours) return;

  const dateContexte = champDate.value || null;

  // Retirer le message de bienvenue si présent
  const bienvenue = document.getElementById("bienvenue");
  if (bienvenue) bienvenue.remove();

  enCours = true;
  btnEnvoyer.disabled = true;
  champQuestion.value = "";
  champQuestion.style.height = "44px";

  ajouterMessageUtilisateur(question);
  const indicateur = ajouterIndicateurSaisie();

  try {
    const corps = { question };
    if (dateContexte) corps.date_contexte = dateContexte;

    const rep = await fetch(API.ask, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corps),
    });

    supprimerIndicateur();

    if (!rep.ok) {
      const erreur = await rep.json().catch(() => ({}));
      afficherErreur(erreur.detail || `Erreur ${rep.status}`);
      return;
    }

    const data = await rep.json();
    afficherReponse(data);

    // Rafraîchir le panneau de validation si réponse soumise
    if (data.en_attente_validation) {
      setTimeout(chargerTachesPendantes, 500);
    }

  } catch (err) {
    supprimerIndicateur();
    afficherErreur("Impossible de joindre l'API. Vérifiez que le serveur est démarré.");
    console.error(err);
  } finally {
    enCours = false;
    btnEnvoyer.disabled = false;
    champQuestion.focus();
  }
}

// ---------------------------------------------------------------------------
// Panneau de validation humaine
// ---------------------------------------------------------------------------

async function chargerTachesPendantes() {
  try {
    const rep = await fetch(API.pending);
    if (!rep.ok) return;
    const data = await rep.json();

    compteurTaches.textContent = data.total;
    listeTaches.innerHTML = "";

    if (!data.taches || data.taches.length === 0) {
      listeTaches.innerHTML = `
        <div class="tache-vide">
          Aucune tâche en attente.<br>
          <small>Les réponses à valider apparaissent ici.</small>
        </div>`;
      return;
    }

    data.taches.forEach(tache => {
      const carte = creerCarteTache(tache);
      listeTaches.appendChild(carte);
    });

  } catch (err) {
    console.error("Erreur chargement tâches :", err);
  }
}

function creerCarteTache(tache) {
  const date = new Date(tache.horodatage_creation);
  const dateStr = date.toLocaleString("fr-FR", {
    day: "2-digit", month: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });

  const question = tache.contenu?.question || "—";
  const typeFile = tache.type_file || "pending_responses";
  const typeLabel = typeFile.replace("pending_", "").toUpperCase();

  const carte = document.createElement("div");
  carte.className = "carte-tache";
  carte.dataset.tacheId = tache.tache_id;

  carte.innerHTML = `
    <div class="carte-tache-entete">
      <span class="type-file ${typeFile}">${typeLabel}</span>
      <span class="tache-date">${dateStr}</span>
    </div>
    <div class="carte-tache-corps">
      <div class="tache-question">${echapper(question)}</div>
      <div class="tache-actions">
        <button class="btn-approuver" data-id="${echapper(tache.tache_id)}">✓ Approuver</button>
        <button class="btn-rejeter"   data-id="${echapper(tache.tache_id)}">✗ Rejeter</button>
      </div>
    </div>`;

  carte.querySelector(".btn-approuver").addEventListener("click", () =>
    deciderTache(tache.tache_id, "approuver", carte)
  );
  carte.querySelector(".btn-rejeter").addEventListener("click", () =>
    deciderTache(tache.tache_id, "rejeter", carte)
  );

  return carte;
}

async function deciderTache(tacheId, action, carteEl) {
  const endpoint = action === "approuver" ? API.approve : API.reject;

  try {
    const rep = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tache_id: tacheId }),
    });

    if (!rep.ok) {
      afficherToast("Erreur lors du traitement de la tâche.");
      return;
    }

    const label = action === "approuver" ? "approuvée" : "rejetée";
    afficherToast(`Tâche ${label}.`);

    // Animation de suppression
    carteEl.style.transition = "opacity 200ms, transform 200ms";
    carteEl.style.opacity = "0";
    carteEl.style.transform = "translateX(10px)";
    setTimeout(() => {
      carteEl.remove();
      const restantes = listeTaches.querySelectorAll(".carte-tache").length;
      compteurTaches.textContent = restantes;
      if (restantes === 0) {
        listeTaches.innerHTML = `
          <div class="tache-vide">
            Aucune tâche en attente.<br>
            <small>Les réponses à valider apparaissent ici.</small>
          </div>`;
      }
    }, 200);

  } catch (err) {
    afficherToast("Impossible de joindre l'API.");
    console.error(err);
  }
}

// ---------------------------------------------------------------------------
// Événements
// ---------------------------------------------------------------------------

btnEnvoyer.addEventListener("click", envoyerQuestion);

champQuestion.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    envoyerQuestion();
  }
});

// Redimensionnement automatique du textarea
champQuestion.addEventListener("input", () => {
  champQuestion.style.height = "44px";
  champQuestion.style.height = Math.min(champQuestion.scrollHeight, 120) + "px";
});

document.getElementById("btn-rafraichir").addEventListener("click", chargerTachesPendantes);

// ---------------------------------------------------------------------------
// Initialisation
// ---------------------------------------------------------------------------

chargerTachesPendantes();
setInterval(chargerTachesPendantes, 30000); // rafraîchissement toutes les 30 s
