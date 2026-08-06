/* Contrôleur XML / XSD — interface.
   Tout s'exécute dans le navigateur : le moteur Python (xsdfix) tourne via
   Pyodide (WebAssembly). Aucun fichier n'est envoyé sur un serveur. */

const PYODIDE_VERSION = "314.0.4";
const PYODIDE_MJS = `https://cdn.jsdelivr.net/npm/pyodide@${PYODIDE_VERSION}/pyodide.mjs`;
const PYODIDE_INDEX = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const ENGINE_MODULES = ["__init__", "schema_model", "validator", "corrector",
                        "service", "webapi"];

const state = { xsd: [], xml: [], results: [], webapi: null };

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
  const sources = await Promise.all(
    ENGINE_MODULES.map(async (name) => {
      const response = await fetch(`xsdfix/${name}.py`);
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

function addFiles(kind, files) {
  for (const file of files) {
    const name = file.name.toLowerCase();
    if (kind === "xsd" && !name.endsWith(".xsd")) continue;
    if (kind === "xml" && !name.endsWith(".xml")) continue;
    if (state[kind].some((f) => f.name === file.name && f.size === file.size)) continue;
    state[kind].push(file);
  }
  renderFiles();
}

function renderFiles() {
  for (const kind of ["xsd", "xml"]) {
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
      option.value = file.name;
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

const nextFrame = () => new Promise((resolve) => requestAnimationFrame(() => resolve()));

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
      name: file.name, content: await readAsBase64(file),
    })));

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
  const chips = [
    ["total", state.results.length, "fichier(s)"],
    ["valid", counts.valid, "déjà conforme(s)"],
    ["fixed", counts.fixed, "corrigé(s)"],
    ["partial", counts.partial, "partiellement corrigé(s)"],
    ["failed", counts.failed + counts.error, "en échec"],
  ];
  const box = $("summary");
  box.innerHTML = "";
  for (const [kind, count, text] of chips) {
    if (kind !== "total" && !count) continue;
    box.appendChild(el("span", "chip " + kind, count + " " + text));
  }
}

function buildCard(result) {
  const card = el("div", "card");
  if (result.status !== "valid") card.classList.add("open");

  const head = el("div", "card-head");
  head.appendChild(el("span", "arrow", "▶"));
  head.appendChild(el("span", "name", result.name));
  const remaining = result.errorsAfter.length;
  let meta = result.errorsBefore.length + " erreur(s)";
  if (result.status === "valid") meta = "aucune erreur";
  else meta += " · " + result.changes.length + " correction(s)" +
               (remaining ? " · " + remaining + " restante(s)" : "");
  head.appendChild(el("span", "meta", meta));
  head.appendChild(el("span", "badge " + result.status, STATUS_LABEL[result.status]));
  head.onclick = () => card.classList.toggle("open");
  card.appendChild(head);

  const body = el("div", "card-body");
  body.appendChild(el("p", "meta", STATUS_TEXT[result.status]));
  if (result.fatal) body.appendChild(el("div", "notice", result.fatal));

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
      entry.file((file) => { out.push(file); resolve(); }, resolve);
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

setupDropzone("dz-xsd");
setupDropzone("dz-xml");

$("btn-run").onclick = run;

$("btn-reset").onclick = () => {
  state.xsd = [];
  state.xml = [];
  state.results = [];
  renderFiles();
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

$("btn-zip").onclick = async () => {
  const webapi = await engine();
  const b64 = webapi.zip_base64();
  if (b64) saveFile("xml-corriges.zip", base64ToBytes(b64), "application/zip");
};

renderFiles();
// le moteur se charge en tâche de fond : il est prêt avant que l'utilisateur
// ait fini de déposer ses fichiers
engine().catch(() => {});
