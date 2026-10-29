// The pseudonymizer page. The engine runs under Pyodide in a Web Worker (worker.js), so
// the document never leaves the device and a long one never freezes the page. Python
// is reached through bridge.py only:
//
//   review(raw, editsJson, sanitize) -> resultJson      docx_text(bytes) -> text
//   docx_pseudonymized(bytes, redactionsJson) -> bytes  docx_bytes(text) -> bytes
//
// The reviewer's changes are a list of actions. Every change analyses the document
// again with the whole list, and undo or redo swaps one list for another.

const PERSON = "Nominativo";   // role of a person the reviewer adds
const OTHER = "Dato";          // label of any other text the reviewer adds
const TAG = /^[A-Za-zÀ-ÖØ-öø-ÿ]+_\d+$/;   // what verification reads as a tag, not a name
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const REASONS = {
  "verification:structured": "dato identificativo rimasto",
  "verification:private-role": "nome dopo il ruolo di una parte",
  "verification:name-shape": "sembra un nome",
  "verification:alias": "variante di un nome oscurato",
  "verification:coverage": "documento lungo in cui non è stato oscurato nulla",
};

const $ = (id) => document.getElementById(id);
const fold = (text) => text.replace(/\s+/g, " ").trim().toLowerCase();
const quote = (texts) => texts.map((text) => `«${text}»`).join(", ");
const reason = (finding) => REASONS[finding.source] ?? finding.reason;

// Each action type: [Edits field or null, its value, key of the one action of that type
// it replaces, line in "Le tue modifiche"].
const ACTIONS = {
  person: (a) => ["people", [a.text, PERSON], fold(a.text), `Oscurato come persona: «${a.text}»`],
  term: (a) => ["terms", [a.text, OTHER], fold(a.text), `Oscurato come dato: «${a.text}»`],
  keep: (a) => ["keep_spans", [a.start, a.end], `${a.start}:${a.end}`, `In chiaro qui: «${a.text}»`],
  exclude: (a) => ["exclude", a.value, fold(a.value), `In chiaro ovunque: ${quote(a.aliases)}`],
  rename: (a) => ["placeholders", [a.value, a.tag], fold(a.value), `${a.from} rinominato ${a.tag}`],
  merge: (a) => ["merge", [a.value, a.into], fold(a.value), `${a.from} unito a ${a.to}`],
  ignore: (a) => [null, null, `${a.start}:${a.end}`, `Ignorato: «${a.text}»`],
};

let doc = null;        // { name, raw }
let actions = [];      // the reviewer's changes, oldest first
let past = [];         // earlier lists of actions, for undo
let future = [];       // undone lists, for redo
let priorTags = {};    // the first tag each value was given, so tags keep their numbers
let result = null;     // the last analysis
let items = [];        // findings the reviewer has not answered yet
let cursor = null;     // start of the item n/p last moved to
let latest = 0;        // the last analysis asked for: older answers are dropped
let saving = null;     // the format to save once the reviewer confirms
let retagging = null;  // entity currently open in the change-placeholder dialog

function status(message, state = "ready") {
  $("status").textContent = message;
  $("status").dataset.state = state;
}

// --- the engine, behind the worker -----------------------------------------------------

const worker = new Worker(new URL("worker.js", import.meta.url), { type: "module" });
const pending = new Map();
let calls = 0;
worker.onmessage = ({ data }) => {
  const { resolve, reject } = pending.get(data.id);
  pending.delete(data.id);
  if ("error" in data) reject(Object.assign(new Error(data.error), { refused: data.refused }));
  else resolve(data.result);
};
worker.onerror = (event) => status(`Impossibile avviare il motore: ${event.message || "errore nel worker"}`, "error");

function call(name, ...args) {
  return new Promise((resolve, reject) => {
    pending.set(++calls, { resolve, reject });
    worker.postMessage({ id: calls, name, args }, args.filter((arg) => arg instanceof ArrayBuffer));
  });
}

async function start() {
  const begin = performance.now();
  await call("ready");
  $("open").disabled = false;
  $("example").disabled = false;
  status(`Motore pronto in ${((performance.now() - begin) / 1000).toFixed(1)} s. `
         + "Apri un file .txt, .md o .docx.");
}

// --- opening ---------------------------------------------------------------------------

// A .txt from Word on Windows is often not UTF-8.
async function readText(file) {
  const bytes = await file.arrayBuffer();
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return new TextDecoder("windows-1252").decode(bytes);
  }
}

async function openFile(file) {
  const extension = file.name.split(".").pop().toLowerCase();
  let raw;
  let docx = null;     // a .docx's own bytes, to write the tags back into
  try {
    if (extension === "docx") {
      status("Lettura del documento Word…", "busy");
      docx = await file.arrayBuffer();
      raw = await call("docx_text", docx.slice(0));
    } else if (extension === "txt" || extension === "md") {
      raw = await readText(file);
    } else {
      return status("Formato non supportato: apri un file .txt, .md o .docx.", "error");
    }
  } catch (error) {
    return status(error.refused ? error.message : `Impossibile leggere ${file.name}: ${error.message}`, "error");
  }
  if (!raw.trim()) return status(`${file.name} non contiene testo.`, "error");
  doc = { name: file.name, raw, docx };
  actions = [];
  past = [];
  future = [];
  priorTags = {};
  cursor = null;
  await analyse();
}

// --- the review loop -------------------------------------------------------------------

function change(action) {
  const key = ACTIONS[action.type](action)[2];
  const same = (other) => other.type === action.type && ACTIONS[other.type](other)[2] === key;
  commit([...actions.filter((other) => !same(other)), action]);
}

function commit(next) {
  past.push(actions);
  future = [];
  actions = next;
  analyse();
}

function undo() {
  if (!past.length) return;
  future.push(actions);
  actions = past.pop();
  analyse();
}

function redo() {
  if (!future.length) return;
  past.push(actions);
  actions = future.pop();
  analyse();
}

async function analyse() {
  const id = ++latest;
  hideMenu();
  $("export-txt").disabled = $("export-docx").disabled = true;
  $("workspace").setAttribute("aria-busy", "true");
  status("Analisi in corso…", "busy");
  const edits = { people: [], terms: [], exclude: [], keep_spans: [], merge: [], placeholders: [],
                  prior_tags: Object.entries(priorTags) };
  for (const action of actions) {
    const [field, value] = ACTIONS[action.type](action);
    if (field) edits[field].push(value);
  }
  const begin = performance.now();
  let answer;
  try {
    // A .docx's text is analysed as it is, so offsets point into its runs.
    answer = JSON.parse(await call("review", doc.raw, JSON.stringify(edits), !doc.docx));
  } catch (error) {
    if (id === latest) {
      $("workspace").setAttribute("aria-busy", "false");
      status(`Errore nell'analisi: ${error.message}`, "error");
    }
    return;
  }
  if (id !== latest) return;
  result = answer;
  // The engine reads one tag per value. Keeping the first means a renamed entity gets
  // its number back when the rename is undone.
  for (const [value, tag] of Object.entries(result.mapping)) priorTags[value] ??= tag;
  items = reviewItems();
  showDocument();
  showPanel();
  $("empty-state").hidden = $("review-empty").hidden = true;
  $("document").hidden = $("document-tools").hidden = $("review-sections").hidden = false;
  $("document-title").textContent = doc.name;
  $("document-format").textContent = doc.name.split(".").pop().toUpperCase();
  $("document-format").hidden = false;
  $("undo").disabled = !past.length;
  $("redo").disabled = !future.length;
  $("add-person").disabled = $("add-other").disabled = !selection();
  $("workspace").setAttribute("aria-busy", "false");
  showCurrent();
  $("export-txt").disabled = $("export-docx").disabled = false;
  status(`${doc.name}: analizzato in ${Math.round(performance.now() - begin)} ms.`);
}

// The findings the reviewer has not answered: not ignored, not inside one of their
// keeps, not a spelling of an entity they chose to leave in clear.
function reviewItems() {
  const ignored = new Set(actions.filter((a) => a.type === "ignore").map((a) => `${a.start}:${a.end}`));
  const excluded = new Set(actions.filter((a) => a.type === "exclude").flatMap((a) => a.aliases.map(fold)));
  const keeps = result.decisions.filter((d) => d.kind === "USER");
  return result.findings.filter((f) => f.text && !ignored.has(`${f.start}:${f.end}`)
    && !excluded.has(fold(f.text)) && !keeps.some((k) => k.start <= f.start && f.end <= k.end));
}

function warnings() {
  const conflict = /^conflicting judicial\/private-role evidence for (['"])(.*)\1$/;
  return [
    ...result.partial_keeps.map((keep) => `«${keep.text}» è in chiaro in un punto ma oscurato `
      + `altrove come ${keep.tags.join(", ")}: chi legge può ricollegarli.`),
    ...result.findings.filter((f) => !f.text).map((f) => `${reason(f)}.`),
    ...result.warnings.map((warning) => warning.replace(
      conflict, "«$2» compare sia come giudice sia come parte: controlla come è stato trattato.")),
  ];
}

// The text an edit names an entity by: its value in the mapping.
const valueOf = (tag) => Object.keys(result.mapping).find((value) => result.mapping[value] === tag);
// A surname shared by several people has a neutral tag of its own, which no edit names.
const editable = (entity) => entity && !entity.sources.every((source) => source === "alias:ambiguous");

function accept(item) {
  cursor = item.start;
  change({ type: item.kind === "PERSON" ? "person" : "term", text: item.text });
}

function dismiss(item) {
  cursor = item.start;
  change({ type: "ignore", start: item.start, end: item.end, text: item.text });
}

function keepHere(decision) {
  change({ type: "keep", start: decision.start, end: decision.end,
           text: result.text.slice(decision.start, decision.end) });
}

function keepEverywhere(entity) {
  change({ type: "exclude", value: valueOf(entity.entity_id), aliases: entity.aliases });
}

// A page dialog, rather than window.prompt(), works in embedded browsers too.  A typed
// tag either renames this entity or merges it into the matching redacted entity.
function retag(entity) {
  const from = entity.entity_id;
  retagging = entity;
  $("retag-title").textContent = `Cambia ${from}`;
  $("retag-input").value = from;
  $("retag-error").hidden = true;
  $("retag").showModal();
  $("retag-input").focus();
  $("retag-input").select();
}

function applyRetag(event) {
  event.preventDefault();
  const entity = retagging;
  if (!entity) return;
  const from = entity.entity_id;
  const tag = $("retag-input").value.trim();
  const showError = (message) => {
    $("retag-error").textContent = message;
    $("retag-error").hidden = false;
    $("retag-input").focus();
  };
  if (!tag || tag === from) return $("retag").close();
  const other = result.entities.find((e) => e.action === "redact" && e.entity_id === tag);
  if (other && editable(other)) {
    $("retag").close();
    change({ type: "merge", value: valueOf(from), into: valueOf(tag), from, to: tag });
  } else if (other) {
    showError(`${tag} è un cognome condiviso da più persone: non si può unire.`);
  } else if (!TAG.test(tag)) {
    showError("Segnaposto non valido: usa lettere, un trattino basso e un numero, es. Attore_1.");
  } else {
    $("retag").close();
    change({ type: "rename", value: valueOf(from), from, tag });
  }
}

// --- the text --------------------------------------------------------------------------

function showDocument() {
  const spans = [
    ...result.decisions.map((decision, index) => ({ ...decision, mark: decision.action, index })),
    ...items.map((item, index) => ({ ...item, mark: "verify", index })),
  ].sort((a, b) => a.start - b.start || (a.mark === "verify") - (b.mark === "verify"));
  const parts = document.createDocumentFragment();
  let at = 0;
  for (const span of spans) {
    if (span.start < at) continue;       // a finding inside a highlight is listed only
    parts.append(result.text.slice(at, span.start));
    const mark = document.createElement("mark");
    mark.className = span.mark;
    mark.textContent = result.text.slice(span.start, span.end);
    mark.dataset[span.mark === "verify" ? "item" : "decision"] = span.index;
    if (span.tag) mark.dataset.tag = span.tag;
    if (span.mark !== "keep") {
      mark.tabIndex = 0;
      mark.setAttribute("role", "button");
      mark.setAttribute("aria-label", `${mark.textContent}: ${span.tag || "da verificare"}. Mostra azioni`);
    }
    parts.append(mark);
    at = span.end;
  }
  parts.append(result.text.slice(at));
  $("document").replaceChildren(parts);
}

function button(label, action, disabled = false) {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = label;
  element.disabled = disabled;
  element.onclick = action;
  return element;
}

function showMenu(mark, title, choices) {
  const heading = document.createElement("p");
  heading.textContent = title;
  $("menu").replaceChildren(heading, ...choices.map(([label, action]) =>
    button(label, () => { hideMenu(); action(); }, !action)));
  const box = mark.getBoundingClientRect();
  $("menu").style.left = `${Math.max(8, Math.min(box.left, innerWidth - 300))}px`;
  $("menu").style.top = `${box.bottom + 4}px`;
  $("menu").hidden = false;
  // Keep the menu on screen even for a highlight near the bottom of the reader.
  $("menu").style.top = `${Math.max(8, Math.min(box.bottom + 4, innerHeight - $("menu").offsetHeight - 8))}px`;
  $("menu").querySelector("button:enabled")?.focus();
}

function hideMenu() {
  $("menu").hidden = true;
}

$("document").onclick = (event) => {
  const mark = event.target.closest("mark");
  if (!mark || getSelection().toString()) return;
  if (mark.dataset.item) {
    const item = items[mark.dataset.item];
    cursor = item.start;
    showCurrent();
    showMenu(mark, `Da verificare: ${reason(item)}`, [
      [item.kind === "PERSON" ? "Aggiungi come persona" : "Aggiungi come dato", () => accept(item)],
      ["Ignora", () => dismiss(item)],
    ]);
  } else if (mark.classList.contains("redact")) {
    const decision = result.decisions[mark.dataset.decision];
    const entity = result.entities.find((e) => e.entity_id === decision.tag);
    showMenu(mark, `${decision.tag}: «${mark.textContent}»`, [
      ["Lascia in chiaro questa occorrenza", () => keepHere(decision)],
      ["Lascia in chiaro tutta l'entità", editable(entity) && (() => keepEverywhere(entity))],
    ]);
  }
};
document.addEventListener("mousedown", (event) => {
  if (!event.target.closest("#menu, mark")) hideMenu();
});
$("document").addEventListener("scroll", hideMenu);
$("document").addEventListener("keydown", (event) => {
  if (event.target.matches("mark[role=button]") && ["Enter", " "].includes(event.key)) {
    event.preventDefault();
    event.stopPropagation();
    event.target.click();
  }
});

// --- the panel -------------------------------------------------------------------------

function row(text, ...buttons) {
  const li = document.createElement("li");
  const description = document.createElement("p");
  description.textContent = text;
  li.append(description);
  if (buttons.length) {
    const controls = document.createElement("div");
    controls.className = "row-actions";
    controls.append(...buttons);
    li.append(controls);
  }
  return li;
}

function showPanel() {
  $("findings-count").textContent = items.length;
  $("findings").replaceChildren(...items.map((item) => {
    const li = row(`«${item.text}»: ${reason(item)}`,
                   button(item.kind === "PERSON" ? "Aggiungi come persona" : "Aggiungi come dato",
                          () => accept(item)),
                   button("Ignora", () => dismiss(item)));
    li.onclick = (event) => {
      if (!event.target.closest("button")) { cursor = item.start; showCurrent(); }
    };
    return li;
  }));
  const notices = warnings();
  $("warnings").replaceChildren(...notices.map((warning) => row(warning)));
  $("warnings-count").textContent = notices.length;
  $("warnings-section").hidden = !notices.length;
  const redacted = result.entities.filter((entity) => entity.action === "redact");
  const kept = result.entities.filter((entity) => entity.action === "keep");
  $("redacted-count").textContent = redacted.length;
  $("redacted").replaceChildren(...redacted.map((entity) => editable(entity)
    ? row(`${entity.entity_id}: ${quote(entity.aliases)} ×${entity.spans.length}`,
          button("Lascia in chiaro", () => keepEverywhere(entity)),
          button("Cambia segnaposto…", () => retag(entity)))
    : row(`${entity.entity_id}: ${quote(entity.aliases)} ×${entity.spans.length} (cognome condiviso)`)));
  $("kept-count").textContent = kept.length;
  $("kept").replaceChildren(...kept.map((entity) => row(
    `${quote(entity.aliases)} (${entity.kind === "USER" ? "scelta tua" : entity.kind}) ×${entity.spans.length}`)));
  $("edits").replaceChildren(...actions.map((action) => row(
    ACTIONS[action.type](action)[3],
    button("Annulla", () => commit(actions.filter((other) => other !== action))))));
  $("edits-count").textContent = actions.length;
}

// --- keyboard --------------------------------------------------------------------------

function currentIndex() {
  if (cursor === null || !items.length) return -1;
  const index = items.findIndex((item) => item.start >= cursor);
  return index < 0 ? items.length - 1 : index;
}

function move(step) {
  if (!items.length) return;
  const index = currentIndex();
  const next = index < 0 ? (step > 0 ? 0 : items.length - 1)
                         : (index + step + items.length) % items.length;
  cursor = items[next].start;
  showCurrent();
}

function showCurrent() {
  for (const element of document.querySelectorAll(".current")) element.classList.remove("current");
  const index = currentIndex();
  if (index < 0) return;
  $("findings-section").open = true;
  for (const element of [$("document").querySelector(`[data-item="${index}"]`), $("findings").children[index]]) {
    element?.classList.add("current");
    element?.scrollIntoView({ block: "nearest" });
  }
}

document.addEventListener("keydown", (event) => {
  if (!result || $("confirm").open || event.target.closest?.("input, textarea, select, [contenteditable]")) return;
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
    event.preventDefault();
    if (event.shiftKey) redo(); else undo();
    return;
  }
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const item = items[currentIndex()];
  if (event.key === "Escape") hideMenu();
  else if (event.key === "n") move(1);
  else if (event.key === "p") move(-1);
  else if (event.key === "Enter" && item && !event.target.closest?.("button, summary, [role=button], a")) accept(item);
  else if ((event.key === "Delete" || event.key === "Backspace") && item) dismiss(item);
  else return;
  event.preventDefault();
});

// --- selection and export --------------------------------------------------------------

// The selected text, if it lies in the document.
function selection() {
  const selected = getSelection();
  if (!selected.rangeCount || !$("document").contains(selected.getRangeAt(0).commonAncestorContainer)) {
    return "";
  }
  return selected.toString().replace(/\s+/g, " ").trim();
}

function download(name, blob) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

// What is left to check, if anything, needs the reviewer's yes before the file is saved.
function exportAs(extension) {
  const left = [...items.map((item) => `Da verificare: «${item.text}» (${reason(item)})`), ...warnings()];
  if (!left.length) return save(extension);
  saving = extension;
  $("confirm-list").replaceChildren(...left.map((text) => row(text)));
  $("confirm").showModal();
}

// A .docx opened as such comes back with its formatting and the tags in place, unless
// a redacted text would survive outside the text: then it comes back plain.
async function save(extension) {
  const name = `${doc.name.replace(/\.(txt|md|docx)$/i, "")}.pseudonimizzato.${extension}`;
  if (extension === "txt") {
    return download(name, new Blob([result.output], { type: "text/plain;charset=utf-8" }));
  }
  if (doc.docx) {
    const redactions = result.decisions.filter((d) => d.action === "redact").map((d) => [d.start, d.end, d.tag]);
    try {
      download(name, new Blob([await call("docx_pseudonymized", doc.docx.slice(0), JSON.stringify(redactions))],
                              { type: DOCX }));
      return status(`${name}: formattazione conservata. Le eventuali immagini non sono state controllate.`);
    } catch (error) {
      if (!error.refused) return status(`Errore nel salvataggio: ${error.message}`);
      status(`${error.message} Scaricato il .docx semplice, senza formattazione.`);
    }
  }
  download(name, new Blob([await call("docx_bytes", result.output)], { type: DOCX }));
}

$("open").onchange = async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (file) await openFile(file);
};
$("example").onclick = async () => {
  $("example").disabled = $("open").disabled = true;
  status("Apertura dell’esempio…", "busy");
  try {
    const response = await fetch(new URL("examples/esempio_sentenza.txt", import.meta.url));
    if (!response.ok) throw new Error("esempio non disponibile");
    await openFile(new File([await response.blob()], "esempio_sentenza.txt"));
  } catch (error) {
    status(`Impossibile aprire l’esempio: ${error.message}`, "error");
  } finally {
    $("example").disabled = $("open").disabled = false;
  }
};
document.addEventListener("selectionchange", () => {
  $("add-person").disabled = $("add-other").disabled = !(result && selection());
});
for (const id of ["add-person", "add-other"]) {
  // Keep the selection alive while the button is pressed.
  $(id).onmousedown = (event) => event.preventDefault();
}
$("add-person").onclick = () => selection() && change({ type: "person", text: selection() });
$("add-other").onclick = () => selection() && change({ type: "term", text: selection() });
$("undo").onclick = undo;
$("redo").onclick = redo;
$("export-txt").onclick = () => exportAs("txt");
$("export-docx").onclick = () => exportAs("docx");
$("confirm-export").onclick = () => save(saving);   // the dialog's form closes it
$("retag-form").onsubmit = applyRetag;
$("retag-cancel").onclick = () => $("retag").close();
$("retag").onclose = () => { retagging = null; };
if (!/Mac|iPhone|iPad/.test(navigator.userAgent)) {
  for (const key of document.querySelectorAll("kbd")) {
    key.textContent = key.textContent.replace("⇧⌘", "Ctrl+Maiusc+").replace("⌘", "Ctrl+");
  }
}

start().catch((error) => status(`Impossibile avviare il motore: ${error.message}`, "error"));
