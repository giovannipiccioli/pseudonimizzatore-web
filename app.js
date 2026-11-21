// The pseudonymizer page. The engine runs under Pyodide in a Web Worker (worker.js), so
// the document never leaves the device and a long one never freezes the page. Python
// is reached through bridge.py only:
//
//   review(raw, stateJson, sanitize, actionJson) -> resultJson      docx_text(bytes) -> text
//   pdf_text(bytes) -> resultJson
//   docx_pseudonymized(bytes, redactionsJson) -> bytes  docx_bytes(text) -> bytes
//
// Each correction starts from the preceding report.state. Undo/redo restore saved
// states. Removing an older correction replays only the later commands on its predecessor.

const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const REASONS = {
  "verification:structured": "dato identificativo rimasto",
  "verification:private-role": "nome dopo il ruolo di una parte",
  "verification:name-shape": "sembra un nome",
  "verification:title-name": "nome dopo un titolo",
  "verification:alias": "variante di un nome oscurato",
  "verification:coverage": "documento lungo in cui non è stato oscurato nulla",
};

const $ = (id) => document.getElementById(id);
const quote = (texts) => texts.map((text) => `«${text}»`).join(", ");
const reason = (finding) => REASONS[finding.source] ?? finding.reason;

const describe = {
  person: (a) => `Oscurato come persona: «${a.text}»`,
  term: (a) => `Oscurato come dato: «${a.text}»`,
  include: (a) => `Oscurato qui: «${a.text}»`,
  keep: (a) => `In chiaro qui: «${a.text}»`,
  exclude: (a) => `In chiaro ovunque: ${quote(a.aliases)}`,
  redact: (a) => `Oscurato in tutta l’entità: «${a.text}»${a.tag ? " → " + a.tag : ""}`,
  merge: (a) => `${a.from} unito a ${a.to}`,
  bulk_findings: (a) => a.treatment === "dismiss" ? "Segnalazioni ignorate in blocco" : "Segnalazioni oscurate in blocco",
  ignore: (a) => `Ignorato: «${a.text}»`,
};

let doc = null;         // { name, raw, docx, pdfNote }
let initialState = null;
let actions = [];       // { command, state } after each accepted correction
let past = [];
let future = [];
let nextEntity = 0;     // unique even when a branch of history is undone
let result = null;     // the last analysis
let items = [];        // findings the reviewer has not answered yet
let cursor = null;     // start of the item n/p last moved to
let latest = 0;        // the last analysis asked for: older answers are dropped
let saving = null;     // the format to save once the reviewer confirms
let editing = null;    // spans currently shown in the review popup
let draft = null;      // imported file and the text shown before analysis
let menuMode = null;
let draggingSelection = false;

function status(message, state = "ready") {
  $("status").textContent = message;
  $("status").dataset.state = state;
}

// --- the engine, behind the worker -----------------------------------------------------

// Use the same release URL for the page, worker and Python bridge.
const workerURL = new URL("worker.js", import.meta.url);
workerURL.search = new URL(import.meta.url).search;
const worker = new Worker(workerURL, { type: "module" });
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
  await call("ready");
  enableOpening(true);
  status("");
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
  let raw, docx = null, pdfNote = "";
  if (extension === "docx") {
    status("Lettura del documento Word…", "busy");
    docx = await file.arrayBuffer();
    raw = await call("docx_text", docx.slice(0));
  } else if (extension === "pdf") {
    status("Estrazione del testo dal PDF…", "busy");
    const { text, empty_pages } = JSON.parse(await call("pdf_text", await file.arrayBuffer()));
    raw = text;
    if (empty_pages.length === 1) {
      pdfNote = `Nessun testo estraibile dalla pagina ${empty_pages[0]} del PDF.`;
    } else if (empty_pages.length > 1) {
      pdfNote = `Nessun testo estraibile dalle pagine ${empty_pages.join(", ")} del PDF.`;
    }
  } else if (extension === "txt" || extension === "md") {
    raw = await readText(file);
  } else {
    return status("Formato non supportato. Sono accettati file .txt, .md, .docx e .pdf.", "error");
  }
  if (!raw.trim()) return status(`${file.name} non contiene testo.`, "error");
  $("source-text").value = raw;
  draft = { name: file.name, raw, docx, pdfNote, display: $("source-text").value };
  $("document-title").textContent = file.name;
  status("Documento caricato. Selezionare Analizza testo per iniziare la revisione.");
}

function enableOpening(enabled) {
  $("open").disabled = $("example").disabled = !enabled;
  $("source-text").readOnly = !enabled;
  updateComposer();
}

function showReview(visible) {
  $("composer").hidden = $("review-empty").hidden = visible;
  for (const id of ["document", "document-tools", "review-sections", "exports", "legend"]) $(id).hidden = !visible;
}

async function openText(name, raw, { docx = null, pdfNote = "" } = {}) {
  if (!raw.trim()) return status(`${name} non contiene testo.`, "error");
  doc = { name, raw, docx, pdfNote };
  actions = [];
  past = [];
  future = [];
  initialState = null;
  result = null;
  cursor = null;
  await analyse();
}

// --- the review loop -------------------------------------------------------------------

function change(action) {
  if (!reviewReady()) return;
  if (action.type === "person" || action.type === "term") action.id = `web-${++nextEntity}`;
  analyse(actions, [action], remember);
}

function remember() {
  past.push(actions);
  future = [];
}

function removeAction(index) {
  if (!reviewReady()) return;
  analyse(actions.slice(0, index), actions.slice(index + 1).map((step) => step.command), remember);
}

function undo() {
  if (!reviewReady() || !past.length) return;
  analyse(past.at(-1), [], () => { future.push(actions); past.pop(); });
}

function redo() {
  if (!reviewReady() || !future.length) return;
  analyse(future.at(-1), [], () => { past.push(actions); future.pop(); });
}

async function analyse(prefix = [], commands = [], onSuccess = () => {}) {
  const id = ++latest;
  hideMenu();
  $("selection-actions").disabled = true;
  $("export-txt").disabled = $("export-docx").disabled = true;
  $("workspace").setAttribute("aria-busy", "true");
  status("Analisi in corso…", "busy");
  const next = [...prefix];
  let state = prefix.at(-1)?.state ?? initialState;
  let answer;
  try {
    // Only selective undo needs more than one pass. Keep the last successful
    // review intact if the engine refuses a selection or processing fails.
    for (const command of commands.length ? commands : [null]) {
      answer = JSON.parse(await call("review", doc.raw, JSON.stringify(state), !doc.docx, JSON.stringify(command)));
      if (id !== latest) return;
      state = answer.state;
      if (answer.applied) {
        // Selective undo may remove an earlier allocation. Keep a new item's
        // original label, even if a lower number becomes available on replay.
        let added;
        if (command.type === "include") added = state.occurrences.at(-1);
        if (command.type === "term") added = state.data.find((row) => row.id === command.id);
        if (command.type === "person") added = state.people.find((row) => row.id === command.id);
        next.push({ command: { ...command, placeholder: added?.placeholder }, state });
      }
    }
  } catch (error) {
    if (id === latest) {
      $("workspace").setAttribute("aria-busy", "false");
      $("export-txt").disabled = $("export-docx").disabled = !result;
      status(error.refused ? error.message : `Errore nell'analisi: ${error.message}`, "error");
    }
    return;
  }
  onSuccess();
  actions = next;
  initialState ??= answer.state;
  result = answer;
  // An explicit local keep already answers the question for this passage.
  // The panel still warns if the same name is redacted elsewhere.
  const keptHere = result.decisions.filter((d) => d.action === "keep" && d.source === "user:occurrence");
  items = result.findings.filter((f) => f.text && !keptHere.some((d) => d.start <= f.start && f.end <= d.end));
  showDocument();
  showPanel();
  showReview(true);
  $("document-title").textContent = doc.name;
  $("document-format").textContent = (doc.name.match(/\.([^.]+)$/)?.[1] ?? "txt").toUpperCase();
  $("document-format").hidden = false;
  $("undo").disabled = !past.length;
  $("redo").disabled = !future.length;
  $("workspace").setAttribute("aria-busy", "false");
  showCurrent();
  $("export-txt").disabled = $("export-docx").disabled = false;
  if (answer.bulk) {
    const { count, skipped } = answer.bulk;
    const processed = count === 1 ? "1 segnalazione trattata" : `${count} segnalazioni trattate`;
    const remaining = skipped ? ` · ${skipped} da trattare singolarmente` : "";
    status(`${processed}${remaining}.`);
  } else {
    status("Analisi completata.");
  }
}

function warnings() {
  const conflict = /^conflicting judicial\/private-role evidence for (['"])(.*)\1$/;
  return [
    ...(doc.pdfNote ? [doc.pdfNote] : []),
    ...result.partial_keeps.map((keep) => `«${keep.text}» è in chiaro in un punto ma oscurato `
      + `altrove come ${keep.tags.join(", ")}: chi legge può ricollegarli.`),
    ...result.findings.filter((f) => !f.text).map((f) => `${reason(f)}.`),
    ...result.unresolved.map((choice) =>
      `Non è stato possibile applicare la scelta a «${choice.anchor.quote}». È necessaria la verifica del passo.`),
    ...result.warnings.filter((w) => w !== "some occurrence decisions could not be applied uniquely").map((warning) => warning.replace(
      conflict, "«$2» compare sia come giudice sia come parte: verificare il trattamento applicato.")),
  ];
}

function accept(item) {
  cursor = item.start;
  change({ type: item.kind === "PERSON" ? "person" : "term", text: item.text });
}

function dismiss(item) {
  cursor = item.start;
  change({ type: "ignore", spans: item.spans, text: item.text });
}

// The reader and the sidebar edit the same spans. Only a grouped sidebar row
// needs a passage picker when a reviewer switches from the entity to one mention.
function openChoices(trigger, spans, { finding = false, grouped = false } = {}) {
  if (!reviewReady()) return;
  const first = spans[0];
  const rows = [...result.state.people, ...result.state.data];
  const owner = rows.find((r) => r.id === first.owner_id);
  editing = { trigger, spans, finding, owner, rows, labelEdited: false };
  $("choice-status").textContent = finding ? "Da verificare" : first.action === "keep" ? "In chiaro" : "Oscurato";
  $("choice-title").textContent = finding ? first.text : textOf(first);
  $("treatment").replaceChildren(new Option("Oscura", "redact"),
    finding ? new Option("Ignora segnalazione", "dismiss")
      : new Option("Lascia in chiaro", "keep"));
  $("treatment").value = finding ? "redact" : first.action;
  const scope = $("scope");
  scope.replaceChildren(new Option("Solo questa occorrenza", "local"));
  if (owner) scope.add(new Option(`Tutta l’entità · ${owner.name ?? owner.value}`, "entity"));
  if (finding) {
    scope.add(new Option("Tutte le occorrenze dello stesso testo", "term"));
    scope.add(new Option("Persona e varianti del nome", "person"));
  }
  scope.value = grouped && owner ? "entity" : "local";
  $("passage").replaceChildren();
  if (spans.length > 1) {
    const chars = Array.from(result.text);
    spans.forEach((span, index) => $("passage").add(new Option(
      `${index + 1}. ${chars.slice(Math.max(0, span.start - 20), span.end + 20).join("").trim()}`, index)));
  }
  hideMenu();
  $("choices").showModal();
  refreshChoice();
  $("treatment").focus({ preventScroll: true });
}

const choiceSpan = () => editing.spans[$("passage").selectedIndex] ?? editing.spans[0];
const textOf = (span) => Array.from(result.text).slice(span.start, span.end).join("");

function refreshChoice() {
  const local = $("scope").value === "local";
  const span = choiceSpan();
  $("scope").disabled = $("treatment").value === "dismiss" || $("scope").options.length === 1;
  $("passage-field").hidden = !local || editing.spans.length < 2;
  if (!editing.finding) $("choice-title").textContent = local ? textOf(span) : (editing.owner?.name ?? editing.owner?.value ?? textOf(span));
  $("label-field").hidden = $("treatment").value !== "redact";
  if (!editing.labelEdited) $("choice-label").value = (local ? span.tag : editing.owner?.placeholder) ?? "";
  explainChoice();
}

function explainChoice() {
  const { owner, rows, finding, trigger } = editing;
  const tag = $("choice-label").value.trim();
  const target = tag && rows.find((r) => r.placeholder === tag);
  const local = $("scope").value === "local";
  let message = "", error = "", submit = "Applica";
  if ($("treatment").value === "redact" && tag) {
    if (target && local && target.id !== owner?.id) {
      message = `Solo questo passo userà il segnaposto di «${target.name ?? target.value}».`;
      submit = "Collega questa occorrenza";
    } else if (target && !local && target.id !== owner?.id) {
      if (finding || !owner || ("name" in owner) !== ("name" in target)) {
        error = "Questo segnaposto appartiene a un’altra entità. È possibile scegliere un segnaposto libero o collegare solo questa occorrenza.";
      } else {
        message = `«${owner.name ?? owner.value}» e «${target.name ?? target.value}» formeranno una sola entità.`;
        submit = "Unisci le due entità";
      }
    } else if (!target && result.state.occurrences.some((c) => c.placeholder === tag)
               && !(local && choiceSpan().tag === tag)
               && !(!local && owner && result.decisions.some((d) => d.tag === tag && d.owner_id === owner.id))) {
      error = "Questo segnaposto è riservato a un’altra occorrenza.";
    }
  }
  $("choice-help").textContent = message;
  $("choice-help").hidden = !message;
  $("choice-error").textContent = error;
  $("choice-error").hidden = !error;
  $("choice-apply").textContent = submit;
  $("choice-apply").disabled = Boolean(error);

  const dialog = $("choices");
  const box = trigger.getBoundingClientRect();
  dialog.style.left = `${Math.max(8, Math.min(box.left, innerWidth - dialog.offsetWidth - 8))}px`;
  const top = box.bottom + dialog.offsetHeight + 14 <= innerHeight ? box.bottom + 6 : box.top - dialog.offsetHeight - 6;
  dialog.style.top = `${Math.max(8, Math.min(top, innerHeight - dialog.offsetHeight - 8))}px`;
}

function applyChoice(event) {
  event.preventDefault();
  if (!editing || $("choice-apply").disabled) return;
  const { owner, rows, finding } = editing;
  const treatment = $("treatment").value;
  const span = choiceSpan();
  const text = finding ? span.text : textOf(span);
  const scope = $("scope").value;
  const tag = $("choice-label").value.trim();
  const target = (tag && rows.find((r) => r.placeholder === tag)) || (!tag && owner);
  const passage = { start: span.start, end: span.end, text };
  $("choices").close();
  if (treatment === "dismiss") return dismiss(span);
  if (scope === "local") {
    if (treatment === "keep") return change({ type: "keep", ...passage });
    return change({ type: "include", ...passage, target: target?.id, tag: target ? undefined : tag });
  }
  if (scope === "person" || scope === "term") return change({ type: scope, text, tag });
  if (treatment === "keep") return change({ type: "exclude", id: owner.id, aliases: [owner.name ?? owner.value] });
  if (target && target.id !== owner.id) {
    return change({ type: "merge", id: owner.id, into: target.id, from: owner.name ?? owner.value, to: tag });
  }
  change({ type: "redact", id: owner.id, text: owner.name ?? owner.value, tag });
}

$("choice-form").onsubmit = applyChoice;
$("choice-cancel").onclick = () => $("choices").close();
$("choices").onclose = () => { editing = null; };
$("choices").onclick = (event) => {
  if (event.target !== $("choices")) return;
  const r = $("choices").getBoundingClientRect();
  if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) $("choices").close();
};
$("scope").onchange = $("treatment").onchange = refreshChoice;
$("passage").onchange = () => { editing.labelEdited = false; refreshChoice(); };
$("choice-label").oninput = () => { editing.labelEdited = true; explainChoice(); };

// --- the text --------------------------------------------------------------------------

function showDocument() {
  const spans = [
    ...result.decisions.map((decision, index) => ({ ...decision, mark: decision.action, index })),
    ...items.map((item, index) => ({ ...item, mark: "verify", index })),
  ].sort((a, b) => a.start - b.start || (a.mark === "verify") - (b.mark === "verify"));
  const text = Array.from(result.text); // Python spans count code points, not UTF-16 units.
  const parts = document.createDocumentFragment();
  let at = 0;
  for (const span of spans) {
    if (span.start < at) continue;       // a finding inside a highlight is listed only
    parts.append(text.slice(at, span.start).join(""));
    const mark = document.createElement("mark");
    mark.className = span.mark;
    mark.textContent = text.slice(span.start, span.end).join("");
    mark.dataset[span.mark === "verify" ? "item" : "decision"] = span.index;
    if (span.tag) mark.dataset.tag = span.tag;
    mark.tabIndex = 0;
    mark.setAttribute("role", "button");
    mark.setAttribute("aria-haspopup", "dialog");
    const treatment = span.tag || (span.mark === "keep" ? "in chiaro" : "da verificare");
    mark.setAttribute("aria-label", `${mark.textContent}: ${treatment}. Mostra azioni`);
    parts.append(mark);
    at = span.end;
  }
  parts.append(text.slice(at).join(""));
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

function showMenu(box, title, choices, { mode = "mark", focus = true } = {}) {
  const menu = $("menu");
  const heading = document.createElement("p");
  heading.id = "menu-title";
  heading.textContent = title;
  menu.replaceChildren(heading, ...choices.map(([label, action]) =>
    button(label, () => { hideMenu(); action(); }, !action)));
  menuMode = mode;
  menu.hidden = false;
  const below = box.bottom + 6;
  const above = box.top - menu.offsetHeight - 6;
  menu.style.left = `${Math.max(8, Math.min(box.left, innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(8, below + menu.offsetHeight <= innerHeight - 8 ? below : above)}px`;
  if (focus) menu.querySelector("button:enabled")?.focus({ preventScroll: true });
}

function hideMenu() {
  $("menu").hidden = true;
  menuMode = null;
}

$("document").onclick = (event) => {
  const mark = event.target.closest("mark");
  if (!mark || !reviewReady() || getSelection().toString()) return;
  if (mark.dataset.item) {
    const item = items[mark.dataset.item];
    cursor = item.start;
    openChoices(mark, [item], { finding: true });
  } else {
    openChoices(mark, [result.decisions[mark.dataset.decision]]);
  }
};
document.addEventListener("pointerdown", (event) => {
  if (!event.target.closest("#menu, #selection-actions")) hideMenu();
  draggingSelection = event.pointerType !== "touch" && $("document").contains(event.target);
});
document.addEventListener("pointerup", () => {
  if (!draggingSelection) return;
  draggingSelection = false;
  syncSelectionMenu();
});
document.addEventListener("pointercancel", () => { draggingSelection = false; });
$("document").addEventListener("scroll", hideMenu);
window.addEventListener("scroll", hideMenu);
window.addEventListener("resize", hideMenu);
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

function applyToFindings(treatment) {
  if (!reviewReady() || !items.length) return;
  const findings = items.map((item) =>
    treatment === "dismiss" ? item.spans : [[item.start, item.end]]);
  change({ type: "bulk_findings", treatment, findings });
}

$("findings-ignore-all").onclick = () => applyToFindings("dismiss");
$("findings-redact-all").onclick = () => applyToFindings("redact");

function showPanel() {
  $("findings-count").textContent = items.length;
  $("findings-actions").hidden = !items.length;
  $("findings").replaceChildren(...items.map((item) => {
    const li = row(`«${item.text}»: ${reason(item)}`,
                   button("Modifica", (e) => openChoices(e.currentTarget, [item], { finding: true })),
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
  // A mixed entity has a row on each side, with the count for that action.
  // Keep different labels in separate rows, including any local overrides.
  const entities = new Map(result.entities.map((e) => [e.entity_id, e]));
  const chars = Array.from(result.text);
  for (const [action, list] of [["redact", "redacted"], ["keep", "kept"]]) {
    const groups = new Map();
    for (const d of result.decisions.filter((d) => d.action === action)) {
      const key = JSON.stringify([d.owner_id ?? d.start, d.tag]);
      if (!groups.has(key)) groups.set(key, { spans: [], aliases: new Set() });
      const group = groups.get(key);
      group.spans.push(d);
      group.aliases.add(chars.slice(d.start, d.end).join(""));
    }
    const rows = Array.from(groups.values(), ({ spans, aliases }) => {
      const first = spans[0];
      const entity = entities.get(first.owner_id);
      const label = first.tag ?? entity?.role ?? first.kind;
      return row(`${label}: ${quote([...aliases])} ×${spans.length}`,
        button("Modifica", (e) => openChoices(e.currentTarget, spans, { grouped: true })));
    });
    $(list).replaceChildren(...rows);
    $(list + "-count").textContent = rows.length;
  }
  $("edits").replaceChildren(...actions.map(({ command }, index) => row(
    describe[command.type](command), button("Annulla", () => removeAction(index)))));
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
  if (!result || event.target.closest?.("dialog, input, textarea, select, [contenteditable]")) return;
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
    event.preventDefault();
    if (event.shiftKey) redo(); else undo();
    return;
  }
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const item = items[currentIndex()];
  if (event.key === "Escape" && menuMode) {
    if (menuMode === "selection" && $("menu").contains(document.activeElement)) {
      $("selection-actions").focus({ preventScroll: true });
    }
    hideMenu();
  }
  else if (event.key === "n") move(1);
  else if (event.key === "p") move(-1);
  else if (event.key === "Enter" && item && !event.target.closest?.("button, summary, [role=button], a")) accept(item);
  else if ((event.key === "Delete" || event.key === "Backspace") && item) dismiss(item);
  else return;
  event.preventDefault();
});

// --- selection and export --------------------------------------------------------------

const reviewReady = () => result && $("workspace").getAttribute("aria-busy") === "false";

// Map the DOM range onto the same normalized input and code-point offsets as the engine.
function selection() {
  const selected = getSelection();
  if (!reviewReady() || !selected.rangeCount || selected.isCollapsed) return null;
  const range = selected.getRangeAt(0);
  const reader = $("document");
  if (!reader.contains(range.commonAncestorContainer)) return null;
  const raw = range.toString();
  const trimmed = raw.trim();
  const text = trimmed.replace(/\s+/g, " ");
  if (!text) return null;
  const before = document.createRange();
  before.selectNodeContents(reader);
  before.setEnd(range.startContainer, range.startOffset);
  const start = Array.from(before.toString()).length + raw.length - raw.trimStart().length;
  const end = start + Array.from(trimmed).length;
  const overlaps = result.decisions.filter((d) => d.start < end && start < d.end);
  if (overlaps.some((d) => d.action === "redact" && d.start <= start && end <= d.end)) return null;
  // A new span must not replace part of an existing redaction or an explicit keep.
  const single = !overlaps.some((d) => d.action === "redact" || d.source === "user:occurrence");
  return { range, text, start, end, single };
}

function showSelectionMenu(selected, focus = false) {
  const { range, text, start, end, single } = selected;
  const apply = (type) => () => {
    getSelection().removeAllRanges();
    change({ type, text, start, end });
  };
  const box = Array.from(range.getClientRects()).at(-1) ?? range.getBoundingClientRect();
  showMenu(box, "Oscurare il testo selezionato?", [
    ["Solo questa occorrenza", single && apply("include")],
    ["Tutte le occorrenze dello stesso testo", apply("term")],
    ["Persona e varianti del nome", apply("person")],
  ], { mode: "selection", focus });
}

function syncSelectionMenu() {
  if (draggingSelection || (menuMode === "selection" && $("menu").contains(document.activeElement))) return;
  const selected = selection();
  $("selection-actions").disabled = !selected;
  if (selected) showSelectionMenu(selected);
  else if (menuMode === "selection") hideMenu();
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
  const name = `${doc.name.replace(/\.(txt|md|docx|pdf)$/i, "")}.pseudonimizzato.${extension}`;
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
      status(`${error.message} Salvato il .docx semplice, senza formattazione.`);
    }
  }
  download(name, new Blob([await call("docx_bytes", result.output)], { type: DOCX }));
}

function updateComposer() {
  $("analyse-source").disabled = $("open").disabled || !$("source-text").value.trim();
  const pdfNote = $("pdf-note");
  pdfNote.textContent = draft?.pdfNote ?? "";
  pdfNote.hidden = !pdfNote.textContent;
  $("word-note").hidden = !draft?.docx || $("source-text").value === draft.display;
}

async function importDocument(readFile) {
  if ($("open").disabled) return;
  enableOpening(false);
  try {
    await openFile(await readFile());
  } catch (error) {
    status(error.refused ? error.message : `Impossibile aprire il documento: ${error.message}`, "error");
  } finally {
    enableOpening(true);
  }
}
$("open").onchange = (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (file) importDocument(() => file);
};
$("example").onclick = () => importDocument(async () => {
  const response = await fetch(new URL("examples/esempio_sentenza.txt", import.meta.url));
  if (!response.ok) throw new Error("esempio non disponibile");
  return new File([await response.blob()], "esempio_sentenza.txt");
});
$("composer").ondragover = (event) => {
  if (!event.dataTransfer.types.includes("Files")) return;
  event.preventDefault();
  $("composer").classList.add("dragging");
};
$("composer").ondragleave = (event) => {
  if (!$("composer").contains(event.relatedTarget)) $("composer").classList.remove("dragging");
};
$("composer").ondrop = (event) => {
  if (!event.dataTransfer.types.includes("Files")) return;
  event.preventDefault();
  $("composer").classList.remove("dragging");
  const files = event.dataTransfer.files;
  if (files.length !== 1) return status("È possibile caricare un documento alla volta.", "error");
  const file = files[0];
  importDocument(() => file);
};
$("source-text").oninput = updateComposer;
$("composer").onsubmit = async (event) => {
  event.preventDefault();
  if ($("analyse-source").disabled) return;
  const value = $("source-text").value;
  const original = draft && draft.display === value;
  enableOpening(false);
  try {
    await openText(draft?.name ?? "Testo inserito", original ? draft.raw : value, {
      docx: original ? draft.docx : null,
      pdfNote: draft?.pdfNote,
    });
  } finally {
    enableOpening(true);
  }
};
$("new-document").onclick = () => {
  if (!reviewReady()) return;
  result = doc = draft = initialState = null;
  actions = []; past = []; future = []; items = []; cursor = null;
  for (const id of ["document", "findings", "redacted", "kept", "warnings", "edits"]) $(id).replaceChildren();
  $("source-text").value = "";
  hideMenu();
  $("document-title").textContent = "Preparazione del documento";
  $("document-format").hidden = true;
  showReview(false);
  updateComposer();
  status("Caricare un documento o inserire il testo.");
  $("source-text").focus();
};
document.addEventListener("selectionchange", syncSelectionMenu);
$("selection-actions").onmousedown = (event) => event.preventDefault();
$("selection-actions").onclick = () => {
  const selected = selection();
  if (selected) showSelectionMenu(selected, true);
};
$("undo").onclick = undo;
$("redo").onclick = redo;
$("export-txt").onclick = () => exportAs("txt");
$("export-docx").onclick = () => exportAs("docx");
$("confirm-export").onclick = () => save(saving);   // the dialog's form closes it
if (!/Mac|iPhone|iPad/.test(navigator.userAgent)) {
  for (const key of document.querySelectorAll("kbd")) {
    key.textContent = key.textContent.replace("⇧⌘", "Ctrl+Maiusc+").replace("⌘", "Ctrl+");
  }
}

start().catch((error) => status(`Impossibile avviare il motore: ${error.message}`, "error"));
