// Pyodide in a Web Worker, so a long document never freezes the page. The page posts
// {id, name, args}; the worker answers {id, result} from bridge.<name>(...args), or
// {id, error, refused}, where `refused` marks a bridge.Refused: a message for the reader.
import { loadPyodide } from "./vendor/pyodide-314.0.7/pyodide.mjs";

const ENGINE_WHEEL = "vendor/pseudonimizzatore_legale-0.8.0-py3-none-any.whl";
const PDF_WHEEL = "vendor/pypdf-7.1.1-py3-none-any.whl";
const url = (path) => new URL(path, import.meta.url).href;
const quiet = { messageCallback: () => {} };

async function start() {
  const pyodide = await loadPyodide({ indexURL: url("vendor/pyodide-314.0.7/") });
  await pyodide.loadPackage(["regex", url(ENGINE_WHEEL)], quiet);
  const bridgeURL = new URL("bridge.py", import.meta.url);
  bridgeURL.search = new URL(import.meta.url).search;
  const response = await fetch(bridgeURL);
  if (!response.ok) throw new Error("Impossibile caricare il modulo Python.");
  pyodide.FS.writeFile("bridge.py", await response.text());
  return { pyodide, bridge: pyodide.pyimport("bridge") };
}
const ready = start();

const calls = {
  ready: () => true,
  review: ({ bridge }, raw, state, sanitize, action) => bridge.review(raw, state, sanitize, action),
  async pdf_text({ pyodide, bridge }, buffer) {
    await pyodide.loadPackage(url(PDF_WHEEL), quiet);
    return bridge.pdf_text(pyodide.toPy(new Uint8Array(buffer)));
  },
  async docx_text({ pyodide, bridge }, buffer) {
    await pyodide.loadPackage("lxml", quiet);     // the first .docx only
    return bridge.docx_text(pyodide.toPy(new Uint8Array(buffer)));
  },
  docx_pseudonymized: ({ pyodide, bridge }, buffer, redactionsJson) =>
    bytes(bridge.docx_pseudonymized(pyodide.toPy(new Uint8Array(buffer)), redactionsJson)),
  docx_bytes: ({ bridge }, text) => bytes(bridge.docx_bytes(text)),
};

// Python bytes as a Uint8Array the page can be sent.
function bytes(proxy) {
  try {
    return proxy.toJs();
  } finally {
    proxy.destroy();
  }
}

self.onmessage = async ({ data: { id, name, args } }) => {
  if (!Object.hasOwn(calls, name)) {
    self.postMessage({ id, error: "Operazione non disponibile. Ricarica la pagina e riprova.", refused: true });
    return;
  }
  try {
    const result = await calls[name](await ready, ...args);
    self.postMessage({ id, result }, result instanceof Uint8Array ? [result.buffer] : []);
  } catch (error) {
    // A Python error's message is its traceback, whose last line is "bridge.Refused: …".
    const refused = error.type === "Refused";
    const message = refused ? error.message.trim().split("\n").at(-1).replace(/^[\w.]*: /, "")
                            : String(error.message || error);
    self.postMessage({ id, error: message, refused });
  }
};
