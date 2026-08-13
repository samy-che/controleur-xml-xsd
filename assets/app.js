/* Contrôleur XML / XSD — interface.
   Tout s'exécute dans le navigateur : le moteur Python (xsdfix) tourne via
   Pyodide (WebAssembly). Aucun fichier n'est envoyé sur un serveur. */

const PYODIDE_VERSION = "314.0.4";
const PYODIDE_MJS = `https://cdn.jsdelivr.net/npm/pyodide@${PYODIDE_VERSION}/pyodide.mjs`;
const PYODIDE_INDEX = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const ENGINE_MODULES = ["__init__", "schema_model", "validator", "corrector",
                        "flat_schema", "referentiel", "service", "webapi"];

const state = { xsd: [], xml: [], ref: [], results: [], webapi: null };

const $ = (id) => document.getElementById(id);

const STATUS_LABEL = {
  valid: "conforme", fixed: "corrigé", partial: "partiel",
  failed: "échec", error: "illisible",
};

const STATUS_TEXT = {
  valid: "Déjà conforme au XSD, aucune modification nécessaire.",
  fixed: "Corrigé automatiquement : le fichier est désormais conforme au XSD.",
  partial: "Partiellement corrigé : il reste des erreurs à traiter à la main.",
  failed: "Aucune correction automatique possible.",
  error: "Fichier illisible.",
};

const CHANGE_LABEL = {
  order: "ordre", namespace: "espace de noms", insert: "ajout",
  remove: "suppression", trim: "valeur",
};

/* ------------------------------------------------------------------ moteur */

let enginePromise = null;

function setEngineStatus(text, ready) {
  const node = $("engine-status");
  node.textContent = text;
  node.classList.toggle("ready", !!ready);
}

async function bootEngine() {
  if (location.protocol === "file:") {
    throw new Error(
      "Ouvrez la page via un serveur (python3 -m http.server) ou via son adresse " +
      "en ligne : un navigateur interdit à une page file:// de charger ses ressources.");
  }
  setEngineStatus("Chargement du moteur (≈8 Mo au premier passage, puis en cache)…");
  const { loadPyodide } = await import(PYODIDE_MJS);
  const pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX });

  setEngineStatus("Chargement de lxml…");
  await pyodide.loadPackage("lxml");

  setEngineStatus("Chargement du moteur de correction…");
  pyodide.FS.mkdirTree("/engine/xsdfix");
  // « no-cache » force la revalidation auprès du serveur : sans cela, un
  // visiteur déjà venu continuerait d'exécuter l'ancien moteur après une mise
  // en ligne. Les fichiers sont petits et renvoient un 304 quand rien n'a changé.
  const sources = await Promise.all(
    ENGINE_MODULES.map(async (name) => {
      const response = await fetch(`xsdfix/${name}.py`, { cache: "no-cache" });
      if (!response.ok) throw new Error(`xsdfix/${name}.py introuvable`);
      return [name, await response.text()];
    })
  );
  for (const [name, code] of sources) {
    pyodide.FS.writeFile(`/engine/xsdfix/${name}.py`, code);
  }
  pyodide.runPython('import sys\nif "/engine" not in sys.path: sys.path.insert(0, "/engine")');
  state.webapi = pyodide.pyimport("xsdfix.webapi");
  setEngineStatus("Moteur prêt — tout s'exécute sur votre machine.", true);
  return state.webapi;
}

function engine() {
  if (!enginePromise) {
    enginePromise = bootEngine().catch((err) => {
      enginePromise = null;               // permet une nouvelle tentative
      setEngineStatus("Moteur indisponible : " + err.message);
      throw err;
    });
  }
  return enginePromise;
}

/* ------------------------------------------------------------------ fichiers */

function humanSize(bytes) {
  if (bytes < 1024) return bytes + " o";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " Ko";
  return (bytes / 1024 / 1024).toFixed(1) + " Mo";
}

function bytesToBase64(bytes) {
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

function base64ToBytes(b64) {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => resolve(bytesToBase64(new Uint8Array(reader.result)));
    reader.readAsArrayBuffer(file);
  });
}

function saveFile(name, bytes, mime) {
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/* Nom transmis au moteur : on garde l'arborescence quand on connaît, car les
   schémas normalisés (UBL…) s'importent par chemins relatifs `../common/x.xsd`. */
function relName(file) {
  return file.relPath || file.webkitRelativePath || file.name;
}

const EXTENSIONS = {
  xsd: [".xsd"],
  xml: [".xml"],
  ref: [".xlsx", ".xlsm", ".csv", ".txt"],
};

function addFiles(kind, files) {
  for (const file of files) {
    const name = file.name.toLowerCase();
    if (!EXTENSIONS[kind].some((ext) => name.endsWith(ext))) continue;
    if (kind === "ref") state.ref = [];          // un seul référentiel à la fois
    if (state[kind].some((f) => f.name === file.name && f.size === file.size)) continue;
    state[kind].push(file);
  }
  renderFiles();
  if (kind === "ref") loadReferentiel();
}

async function loadReferentiel() {
  const zone = $("dz-ref");
  zone.classList.toggle("filled", state.ref.length > 0);
  if (!state.ref.length) {
    $("ref-status").textContent = "";
    try {
      const webapi = await engine();
      webapi.load_referentiel(JSON.stringify({ file: null }));
    } catch (e) { /* moteur pas encore prêt : rien à décharger */ }
    return;
  }
  $("ref-status").textContent = "Lecture du référentiel…";
  try {
    const webapi = await engine();
    const file = state.ref[0];
    const res = JSON.parse(webapi.load_referentiel(JSON.stringify({
      file: { name: file.name, content: await readAsBase64(file) },
    })));
    if (!res.ok) {
      $("ref-status").textContent = res.error || "Référentiel inexploitable.";
      return;
    }
    let texte = `${res.rules} règle(s) chargée(s).`;
    if (res.problems && res.problems.length) {
      texte += " " + res.problems.length + " ligne(s) ignorée(s) : " + res.problems[0];
    }
    $("ref-status").textContent = texte;
  } catch (err) {
    $("ref-status").textContent = "Échec : " + err.message;
  }
}

function renderFiles() {
  for (const kind of ["xsd", "xml", "ref"]) {
    const list = $("list-" + kind);
    list.innerHTML = "";
    state[kind].forEach((file, index) => {
      const li = document.createElement("li");
      li.innerHTML = '<span class="fname"></span><span class="fsize"></span>' +
                     '<button class="rm" title="Retirer">✕</button>';
      li.querySelector(".fname").textContent = file.name;
      li.querySelector(".fsize").textContent = humanSize(file.size);
      li.querySelector(".rm").onclick = () => {
        state[kind].splice(index, 1);
        renderFiles();
        if (kind === "ref") loadReferentiel();
      };
      list.appendChild(li);
    });
    $("dz-" + kind).classList.toggle("filled", state[kind].length > 0);
  }

  const row = $("mainxsd-row");
  const select = $("select-main");
  if (state.xsd.length > 1) {
    const current = select.value;
    select.innerHTML = '<option value="">détection automatique</option>';
    state.xsd.forEach((file) => {
      const option = document.createElement("option");
      option.value = relName(file);
      option.textContent = file.name;
      select.appendChild(option);
    });
    select.value = current;
    row.classList.remove("hidden");
  } else {
    row.classList.add("hidden");
    select.value = "";
  }

  $("btn-run").disabled = !(state.xsd.length && state.xml.length);
}

/* ------------------------------------------------------------------ analyse */

/* Rend la main au navigateur pour qu'il rafraîchisse l'affichage.
   requestAnimationFrame seul ne suffit pas : il ne se déclenche pas quand
   l'onglet est masqué, ce qui figerait l'analyse. On double donc d'un
   minuteur, et le premier des deux qui répond débloque la suite. */
const nextFrame = () => new Promise((resolve) => {
  let done = false;
  const finish = () => { if (!done) { done = true; resolve(); } };
  requestAnimationFrame(finish);
  setTimeout(finish, 50);
});

async function run() {
  const button = $("btn-run");
  button.disabled = true;
  state.results = [];
  $("results").classList.remove("hidden");
  $("summary").innerHTML = "";
  $("cards").innerHTML = "";
  $("btn-zip").classList.add("hidden");

  try {
    const webapi = await engine();

    setProgress("Lecture des fichiers…", 0);
    const xsd = await Promise.all(state.xsd.map(async (file) => ({
      name: relName(file), content: await readAsBase64(file),
    })));

    // Un XSD généré depuis un XML d'exemple a perdu ses espaces de noms : il ne
    // validera jamais rien. On le détecte avant de lancer l'analyse et on
    // propose la conversion, plutôt que de noyer l'utilisateur d'erreurs.
    const flat = JSON.parse(webapi.inspect_xsd(JSON.stringify({ xsd })));
    if (flat.flat) {
      setProgress("", 0);
      offerConversion(flat, xsd);
      return;
    }

    const opened = JSON.parse(webapi.open_session(JSON.stringify({
      xsd,
      mainXsd: $("select-main").value || null,
      options: {
        reorder: $("opt-reorder").checked,
        fix_namespace: $("opt-namespace").checked,
        trim_values: $("opt-trim").checked,
        insert_missing: $("opt-insert").checked,
        remove_unknown: $("opt-remove").checked,
      },
    })));

    if (!opened.ok) {
      setProgress("", 0);
      showNotice(opened.error);
      return;
    }
    if (opened.warnings && opened.warnings.length) {
      const div = document.createElement("div");
      div.className = "notice info";
      div.textContent = "Schémas non chargés : " + opened.warnings.join(" ; ");
      $("cards").appendChild(div);
    }

    const total = state.xml.length;
    for (let i = 0; i < total; i++) {
      const file = state.xml[i];
      setProgress(`Analyse de ${file.name} (${i + 1}/${total})…`, (i / total) * 100);
      await nextFrame();          // laisse le navigateur rafraîchir l'affichage
      const content = await readAsBase64(file);
      const result = JSON.parse(webapi.check_one(file.name, content));
      state.results.push(result);
      $("cards").appendChild(buildCard(result));
      renderSummary();
    }

    setProgress("", 100);
    renderSummary(JSON.parse(webapi.finish()));
    $("btn-zip").classList.toggle(
      "hidden", !state.results.some((r) => r.correctedB64));
    $("results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    setProgress("", 0);
    showNotice("Échec de l'analyse : " + err.message);
  } finally {
    button.disabled = false;
  }
}

/* ------------------------------------------------------------------ schéma « à plat » */

function offerConversion(info, xsdPayload) {
  const box = el("div", "notice info convert");
  box.appendChild(el("h3", null, "Ce schéma a été généré depuis un XML d'exemple"));

  const p1 = el("p", null,
    `« ${info.file} » ne déclare aucun targetNamespace, et ${info.prefixed} de ses ` +
    `${info.total} balises portent un préfixe collé au nom (${info.prefixes.join(", ")}). ` +
    "C'est la signature des générateurs en ligne, qui ne gèrent pas les espaces de noms : " +
    "ils écrivent « cbc.ID » là où il faudrait « cbc:ID ».");
  const p2 = el("p", null,
    "Pour un validateur, ces deux écritures désignent des balises sans aucun rapport. " +
    "Ce schéma ne peut donc valider aucun de vos XML — pas même celui dont il est issu. " +
    "Je peux lui rendre ses espaces de noms sans toucher à l'ordre des balises que vous " +
    "y avez défini.");
  box.appendChild(p1);
  box.appendChild(p2);

  const relax = el("label", "opt");
  const check = document.createElement("input");
  check.type = "checkbox";
  check.checked = true;
  check.id = "opt-relax";
  relax.appendChild(check);
  const texte = el("span");
  texte.appendChild(el("strong", null, "Assouplir les types (recommandé)"));
  texte.appendChild(document.createTextNode(
    "Le générateur a deviné les types depuis un seul exemple : un identifiant " +
    "numérique dans ce fichier est devenu « xs:short », et toute facture dont " +
    "l'identifiant contient une lettre serait rejetée à tort. Décochez si vous " +
    "voulez aussi contrôler les types, et pas seulement l'ordre des balises."));
  relax.appendChild(texte);
  box.appendChild(relax);

  const actions = el("div", "card-actions");
  const go = el("button", "btn primary small", "Convertir ce XSD et relancer l'analyse");
  go.onclick = () => runConversion(info, xsdPayload, box, go);
  actions.appendChild(go);
  box.appendChild(actions);

  $("cards").appendChild(box);
  $("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function runConversion(info, xsdPayload, box, button) {
  button.disabled = true;
  button.textContent = "Conversion…";
  try {
    const webapi = await engine();
    const sample = await readAsBase64(state.xml[0]);
    const relaxCheck = document.getElementById("opt-relax");
    const result = JSON.parse(webapi.convert_flat(JSON.stringify({
      xsd: xsdPayload, sampleXml: sample,
      relaxTypes: relaxCheck ? relaxCheck.checked : true,
    })));
    if (!result.ok) {
      box.appendChild(el("p", "convert-error", result.error));
      button.disabled = false;
      button.textContent = "Convertir ce XSD et relancer l'analyse";
      return;
    }

    // les fichiers produits remplacent le XSD d'origine dans la zone de dépôt
    state.xsd = result.files.map((f) => {
      const file = new File([base64ToBytes(f.content)], f.name,
                            { type: "application/xml" });
      return file;
    });
    renderFiles();
    $("select-main").value = result.mainXsd || "";

    const done = el("div", "notice info");
    done.appendChild(el("h3", null, "Schéma converti"));
    done.appendChild(el("p", null,
      `${result.files.length} fichiers produits (${result.files.map((f) => f.name).join(", ")}), ` +
      `« ${result.mainXsd} » désigné comme schéma principal. ` +
      "Ils ont remplacé votre XSD dans la zone de dépôt : téléchargez-les pour les réutiliser."));
    if (result.relaxed) {
      done.appendChild(el("p", null,
        "Types assouplis : le contrôle porte sur la structure et l'ordre des balises, " +
        "pas sur le format des valeurs. C'est le réglage adapté à un schéma déduit " +
        "d'un seul exemple."));
    }

    if (result.conflicts.length) {
      done.appendChild(el("p", null,
        "Le générateur avait déclaré certains noms sous des formes différentes selon " +
        "le contexte. En XSD un élément global n'a qu'une définition : voici les " +
        "arbitrages retenus, à relire."));
      const list = el("ul", "items");
      result.conflicts.forEach((c) => list.appendChild(el("li", "left", c)));
      done.appendChild(list);
    }

    const actions = el("div", "card-actions");
    const dl = el("button", "btn ghost small", "Télécharger le schéma converti (ZIP)");
    dl.onclick = () => saveConvertedZip(result.files);
    actions.appendChild(dl);
    done.appendChild(actions);
    box.replaceWith(done);

    await run();          // on relance l'analyse avec le schéma réparé
  } catch (err) {
    box.appendChild(el("p", "convert-error", "Échec de la conversion : " + err.message));
    button.disabled = false;
    button.textContent = "Convertir ce XSD et relancer l'analyse";
  }
}

function saveConvertedZip(files) {
  // archive « stored » minimale : évite d'embarquer une bibliothèque
  files.forEach((f) => saveFile(f.name, base64ToBytes(f.content), "application/xml"));
}

function setProgress(text, percent) {
  $("status").textContent = text;
  const bar = $("progress");
  bar.classList.toggle("hidden", !text);
  bar.querySelector("span").style.width = Math.max(2, percent) + "%";
}

function showNotice(text, kind) {
  $("results").classList.remove("hidden");
  const div = document.createElement("div");
  div.className = "notice" + (kind ? " " + kind : "");
  div.textContent = text;
  $("cards").appendChild(div);
}

/* ------------------------------------------------------------------ rendu */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderSummary(counts) {
  if (!counts) {
    counts = { valid: 0, fixed: 0, partial: 0, failed: 0, error: 0 };
    for (const result of state.results) counts[result.status]++;
  }
  const ecarts = state.results.reduce(
    (total, r) => total + ((r.ecarts && r.ecarts.length) || 0), 0);
  const chips = [
    ["total", state.results.length, "fichier(s)"],
    ["valid", counts.valid, "déjà conforme(s)"],
    ["fixed", counts.fixed, "corrigé(s)"],
    ["partial", counts.partial, "partiellement corrigé(s)"],
    ["failed", counts.failed + counts.error, "en échec"],
    ["partial", ecarts, "écart(s) de données"],
  ];
  const box = $("summary");
  box.innerHTML = "";
  for (const [kind, count, text] of chips) {
    if (kind !== "total" && !count) continue;
    box.appendChild(el("span", "chip " + kind, count + " " + text));
  }
}

function buildCard(result) {
  const ecarts = (result.ecarts && result.ecarts.length) || 0;
  const card = el("div", "card");
  // un fichier conforme au XSD mais en écart avec le référentiel doit s'ouvrir :
  // sinon l'écart resterait invisible derrière un badge « conforme »
  if (result.status !== "valid" || ecarts) card.classList.add("open");

  const head = el("div", "card-head");
  head.appendChild(el("span", "arrow", "▶"));
  head.appendChild(el("span", "name", result.name));
  const remaining = result.errorsAfter.length;
  let meta = result.errorsBefore.length + " erreur(s)";
  if (result.status === "valid") meta = "conforme au XSD";
  else meta += " · " + result.changes.length + " correction(s)" +
               (remaining ? " · " + remaining + " restante(s)" : "");
  if (ecarts) meta += " · " + ecarts + " écart(s) de données";
  head.appendChild(el("span", "meta", meta));
  head.appendChild(el("span", "badge " + result.status, STATUS_LABEL[result.status]));
  head.onclick = () => card.classList.toggle("open");
  card.appendChild(head);

  const body = el("div", "card-body");
  body.appendChild(el("p", "meta", STATUS_TEXT[result.status]));
  if (result.fatal) body.appendChild(el("div", "notice", result.fatal));
  if (result.note) body.appendChild(el("div", "notice info", result.note));

  const errorItem = (cls) => (error) => {
    const li = el("li", cls);
    li.appendChild(el("span", "tag", error.category));
    li.appendChild(document.createTextNode(error.label + " "));
    if (error.line) li.appendChild(el("span", "loc", "(ligne " + error.line + ")"));
    return li;
  };

  if (result.errorsBefore.length) {
    body.appendChild(block("Erreurs détectées", result.errorsBefore.map(errorItem("err"))));
  }
  if (result.changes.length) {
    body.appendChild(block("Corrections appliquées", result.changes.map((change) => {
      const li = el("li", "fix");
      li.appendChild(el("span", "tag", CHANGE_LABEL[change.kind] || change.kind));
      li.appendChild(document.createTextNode(change.detail + " "));
      li.appendChild(el("span", "loc", change.path));
      return li;
    })));
  }
  if (result.errorsAfter.length) {
    body.appendChild(block("À traiter manuellement",
                           result.errorsAfter.map(errorItem("left"))));
  }

  if (result.ecarts && result.ecarts.length) {
    body.appendChild(block("Écarts avec le référentiel", result.ecarts.map((ecart) => {
      const li = el("li", ecart.ambigu ? "err" : "left");
      li.appendChild(el("span", "tag", ecart.ambigu ? "ambigu" : "donnée"));
      li.appendChild(document.createTextNode(ecart.label + " "));
      li.appendChild(el("span", "loc", "(ligne " + ecart.ligne + " du référentiel)"));
      return li;
    })));
    body.appendChild(el("p", "meta",
      "Ces écarts portent sur les données, pas sur la structure : ils ne sont jamais " +
      "corrigés automatiquement. Le fichier reste tel quel sur ce point."));
  }

  if (result.corrected) {
    const wrapper = el("div", "block");
    wrapper.appendChild(el("h3", null, "XML corrigé"));
    wrapper.appendChild(el("pre", "xml", result.corrected));
    body.appendChild(wrapper);

    const actions = el("div", "card-actions");
    const download = el("button", "btn primary small",
                        "Télécharger " + result.correctedName);
    download.onclick = () => saveFile(result.correctedName,
                                      base64ToBytes(result.correctedB64),
                                      "application/xml");
    actions.appendChild(download);

    const copy = el("button", "btn ghost small", "Copier le XML");
    copy.onclick = async () => {
      try {
        await navigator.clipboard.writeText(result.corrected);
        copy.textContent = "Copié ✓";
        setTimeout(() => (copy.textContent = "Copier le XML"), 1500);
      } catch (e) {
        copy.textContent = "Copie impossible";
      }
    };
    actions.appendChild(copy);
    body.appendChild(actions);
  }

  card.appendChild(body);
  return card;
}

function block(title, items) {
  const wrapper = el("div", "block");
  wrapper.appendChild(el("h3", null, title));
  const list = el("ul", "items");
  items.forEach((li) => list.appendChild(li));
  wrapper.appendChild(list);
  return wrapper;
}

/* ------------------------------------------------------------------ dépôt de fichiers */

function collectEntries(dataTransfer) {
  const files = [];
  const items = dataTransfer.items;
  if (items && items.length && items[0].webkitGetAsEntry) {
    const walkers = [];
    for (const item of items) {
      const entry = item.webkitGetAsEntry();
      if (entry) walkers.push(walkEntry(entry, files));
    }
    return Promise.all(walkers).then(() => files);
  }
  return Promise.resolve(Array.from(dataTransfer.files));
}

function walkEntry(entry, out) {
  return new Promise((resolve) => {
    if (entry.isFile) {
      entry.file((file) => {
        // fullPath vaut par ex. "/xsd/common/UBL-CommonBasicComponents-2.1.xsd"
        if (entry.fullPath) file.relPath = entry.fullPath.replace(/^\/+/, "");
        out.push(file);
        resolve();
      }, resolve);
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const readBatch = () => {
        reader.readEntries(async (entries) => {
          if (!entries.length) return resolve();
          await Promise.all(entries.map((child) => walkEntry(child, out)));
          readBatch();
        }, resolve);
      };
      readBatch();
    } else {
      resolve();
    }
  });
}

function setupDropzone(id) {
  const zone = $(id);
  const kind = zone.dataset.kind;
  ["dragenter", "dragover"].forEach((type) =>
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.classList.add("hot");
    }));
  ["dragleave", "drop"].forEach((type) =>
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      if (type === "dragleave" && zone.contains(event.relatedTarget)) return;
      zone.classList.remove("hot");
    }));
  zone.addEventListener("drop", async (event) => {
    addFiles(kind, await collectEntries(event.dataTransfer));
  });
}

/* ------------------------------------------------------------------ démarrage */

document.querySelectorAll("[data-browse]").forEach((button) => {
  button.onclick = () => $(button.dataset.browse).click();
});
$("input-xsd").onchange = (e) => { addFiles("xsd", e.target.files); e.target.value = ""; };
$("input-xml").onchange = (e) => { addFiles("xml", e.target.files); e.target.value = ""; };
$("input-ref").onchange = (e) => { addFiles("ref", e.target.files); e.target.value = ""; };

setupDropzone("dz-xsd");
setupDropzone("dz-xml");
setupDropzone("dz-ref");

$("btn-run").onclick = run;

$("btn-reset").onclick = () => {
  state.xsd = [];
  state.xml = [];
  state.ref = [];
  state.results = [];
  renderFiles();
  loadReferentiel();
  $("ref-status").textContent = "";
  $("results").classList.add("hidden");
};

$("btn-sample").onclick = async () => {
  $("status").textContent = "Chargement du jeu d'exemple…";
  try {
    const manifest = await (await fetch("samples/manifest.json")).json();
    for (const item of manifest) {
      const bytes = new Uint8Array(
        await (await fetch("samples/" + item.name)).arrayBuffer());
      addFiles(item.kind, [new File([bytes], item.name, { type: "application/xml" })]);
    }
    $("status").textContent = "";
  } catch (e) {
    $("status").textContent = "Jeu d'exemple indisponible.";
  }
};

$("btn-modele").onclick = async () => {
  if (!state.xml.length) {
    $("ref-status").textContent = "Déposez d'abord vos XML : le modèle en est déduit.";
    return;
  }
  $("ref-status").textContent = "Construction du modèle…";
  try {
    const webapi = await engine();
    const xml = await Promise.all(state.xml.map(async (f) => ({
      name: f.name, content: await readAsBase64(f),
    })));
    const res = JSON.parse(webapi.template_base64(JSON.stringify({ xml })));
    if (!res.ok) {
      $("ref-status").textContent = res.error;
      return;
    }
    saveFile("referentiel-modele.xlsx", base64ToBytes(res.content),
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    const onglets = state.xml.length > 1
      ? `${res.sheets} onglets : un par facture, plus « Toutes les factures » pour ` +
        "les valeurs communes"
      : "1 onglet";
    $("ref-status").textContent =
      `Modèle téléchargé — ${onglets}. Remplissez la colonne « Valeur attendue » ` +
      "puis redéposez le fichier ici.";
  } catch (err) {
    $("ref-status").textContent = "Échec : " + err.message;
  }
};

$("btn-zip").onclick = async () => {
  const webapi = await engine();
  const b64 = webapi.zip_base64();
  if (b64) saveFile("xml-corriges.zip", base64ToBytes(b64), "application/zip");
};

renderFiles();
// le moteur se charge en tâche de fond : il est prêt avant que l'utilisateur
// ait fini de déposer ses fichiers
engine().catch(() => {});
