# pseudonimizzatore-web

[Italiano](README.it.md) · **English**

A web page for pseudonymizing Italian documents, legal ones above all, **without sending them
anywhere**. Open a document and the page shows every name and piece of personal data it
would replace. Check its choices, correct what it got wrong, and save a copy in which people
have become tags such as `Nominativo_1`, `CF_1` or `Indirizzo_1`.

The work is done by [pseudonimizzatore-legale](https://github.com/giovannipiccioli/pseudonimizzatore-legale),
a Python engine that here runs inside the browser tab. The document never leaves your computer.

![A decision opened in the page: names and data highlighted, the review panel on the right](docs/images/1-documento-analizzato.png)

> [!IMPORTANT]
> This is **pseudonymization, not anonymization**. The engine finds a lot, but not everything.
> The page helps you check, and the decision to share the result is yours. A pseudonymized
> document is still personal data under the GDPR.

## Contents

1. [Start the page](#1-start-the-page)
2. [A first document](#2-a-first-document)
3. [Correcting the result](#3-correcting-the-result)
4. [Saving](#4-saving)
5. [Documents it reads, and its limits](#5-documents-it-reads-and-its-limits)
6. [Where your data goes](#6-where-your-data-goes)
7. [Architecture](#7-architecture)
8. [Development](#8-development)
9. [Licence](#9-licence)

## 1. Start the page

You need a recent browser (Chrome, Edge, Firefox or Safari) and, only to serve the folder,
Python 3. Download the repository and start a small local server inside it:

```bash
git clone https://github.com/giovannipiccioli/pseudonimizzatore-web.git
cd pseudonimizzatore-web
python3 -m http.server 8766 --bind 127.0.0.1
```

Then open <http://127.0.0.1:8766> in the browser. The server is needed only because browsers
refuse to run WebAssembly from a file opened with a double click. It serves the files on your
own computer and nothing else.

The first time, the page loads the engine (about 7 MB, from your own server) and in a second
or so enables the document controls. From then on, each document takes a fraction of a second.

## 2. A first document

Type or paste in **Testo da analizzare**, drop a .txt, .md, .docx or .pdf into it, or choose
**Apri documento**. The box shows the imported text before analysis. **Prova con un esempio**
fills it with an [invented decision](examples/esempio_sentenza.txt). Choose **Analizza testo**
to start reviewing. **Nuovo documento** opens a fresh input box.

The text appears on the left with three kinds of highlight:

| highlight | meaning |
|---|---|
| **yellow**, with a small tag | will be replaced by that tag: `Ferruccio Malaspina` → `Nominativo_1` |
| **green** | recognised and kept on purpose: the judges in their role, the case number |
| **pink**, *da verificare* | the final check is unsure: take a look |

The same person gets the same tag everywhere. "Ferruccio Malaspina" in the heading and "il
Malaspina" in the body both become `Nominativo_1`, and the sister mentioned by first name
alone (`Ortensia`) gets the same tag as `Ortensia Vimercati`.

The web app uses the engine’s `keep_roles=("giudice",)` setting for Italian court decisions:

| replaced | kept |
|---|---|
| parties, counsel, witnesses and other private persons | judges, named in their role (Presidente, Relatore…) |
| codici fiscali, VAT numbers of individuals, IBANs and account numbers | public bodies (Agenzia delle Entrate, Comuni…) and their offices' addresses |
| e-mail and PEC addresses, phone numbers, ID documents, register numbers | case numbers, ECLI and the numbers of acts |
| dates and places of birth, residence and studio addresses | courts and other place names |
|  | companies (by default) |

The panel on the right lists everything, in five sections:

- **Da verificare** lists the passages flagged by the final check.
- **Avvisi**: warnings, for example a name you kept in one place but not in another.
- **Oscurati**: every replaced entity, its tag and how many times it appears.
- **Lasciati in chiaro**: what is kept, and why.
- **Modifiche**: the corrections you have made, each one undoable.

## 3. Correcting the result

Changes take effect as you make them, and the tags keep their numbers.

### Something should be replaced but isn't

Select text in the reader, for example “Wolfgang Hartmann” in the sample. A menu
opens next to the selection. **Oscura la selezione…** in the toolbar opens the same menu.

- **Solo questa occorrenza** replaces the selected passage. Other occurrences keep their
  existing treatment. The engine uses a `Dato` tag for a new selection.
- **Tutte le occorrenze dello stesso testo** finds that text throughout the document, even
  with different capitalization or spacing. This works for references and other literal data.
- **Persona e varianti del nome** looks for the same person, including recognised variants
  such as the surname alone or “SURNAME Name” when you select a full name.

### Review a highlight or a sidebar entry

Click a yellow, green or pink highlight, or choose **Modifica** in the sidebar. Both open
the same form. The document starts on **Solo questa occorrenza**. A grouped sidebar entry
starts on **Tutta l’entità**. Switch to a single occurrence to pick the passage by its context.

- Choose **Oscura** or **Lascia in chiaro**. For a redaction you can enter the replacement
  directly in **Segnaposto**. A choice for the whole entity also updates previously
  overridden mentions of that entity.
- For **Da verificare**, you can redact just that passage, all copies of the expression,
  or the person and their name variants. **Ignora segnalazione** dismisses that finding.
- **Oscura tutti** in **Da verificare** redacts the listed passages with automatic
  placeholders. **Ignora tutti** dismisses the findings and leaves the text visible. Passages that overlap
  an existing decision during bulk redaction, or cannot be anchored uniquely, need individual
  review. Undo restores the whole batch in one step.
- To reuse another entity’s label at one passage, choose **Collega questa occorrenza**.
  To merge two people or two data entries, select the whole entity, enter the other label
  and confirm **Unisci le due entità**.

The sidebar keeps the counts, aliases, warnings and correction history. A partial keep
can reveal a connection to a redacted name elsewhere. **Avvisi** flags this for review.
Use **n** and **p** to navigate findings, **Enter** to accept the suggested category and
**Delete** to dismiss the finding.

### Undo

<kbd>⌘Z</kbd> undoes and <kbd>⇧⌘Z</kbd> redoes (<kbd>Ctrl</kbd> on Windows and Linux). In
**Modifiche**, each correction also has its own **Annulla** (undo) button. The document toolbar also
has **Annulla** and **Ripeti** buttons.

## 4. Saving

Two buttons, **Salva .txt** and **Salva .docx**. If something is still on the list to
check, the page shows it first and asks for confirmation:

![The check before saving](docs/images/4-controllo-prima-di-scaricare.png)

- **An imported .docx keeps its formatting if the source text is unchanged**: styles, tables, headers and
  images stay, and the tags sit where the names were. What says who wrote or edited the file
  is removed: document properties, comment authors, revision authors, the preview thumbnail.
  If a replaced text survives somewhere the page cannot rewrite, such as a link address, the
  page says where and saves a plain .docx instead.
- **For PDFs, .txt, .md, and typed, pasted or edited text**, the Word copy is plain, one paragraph per line.

The file takes the original name plus `.pseudonimizzato`, for example
`ricorso.pseudonimizzato.docx`.

## 5. Documents it reads, and its limits

**It reads**:
- `.txt`, in UTF-8 or Windows-1252;
- `.md`;
- `.docx`: body, headers, footers, footnotes and endnotes, comments.
- `.pdf`: embedded text in page order. The app flags pages without extractable text.
  The result is available as `.txt` or a plain `.docx`.

A `.docx` with **tracked changes** is refused. Accept or reject them in Word first.

Scans need a selectable text layer. For protected PDFs, open an unprotected copy.
Old `.doc` files can be converted to `.docx` first. Images inside an imported `.docx` stay
in the formatted copy and are outside text review.

**How much it finds.** The engine was built on Italian court decisions. There, it fully
removes 96.7% of identities on a benchmark of 124 decisions. On other documents (briefs,
contracts, letters, e-mails) a benchmark of 31 invented documents gives 95.9%. The engine
was then corrected on those same documents, so that figure is optimistic. The details are in
[the engine's repository](https://github.com/giovannipiccioli/pseudonimizzatore-legale).
**Always read the result.**

Tags are counted per document: `Nominativo_1` in two different files are not the same
person.

## 6. Where your data goes

Nowhere. The page downloads its code once, from the same server that serves it (the
`vendor/` folder), and then works offline in your tab. The document is read, analysed and
rewritten inside the browser. You can check this in the developer tools' **Network** tab:
the only requests go to the server that serves the page, never to another site.

Nothing is stored. When you close the tab, the document and your corrections are gone.

## 7. Architecture

The page is static (HTML, CSS and two JavaScript modules) and the engine is Python, run in
the browser by [Pyodide](https://pyodide.org), CPython compiled to WebAssembly. Pyodide lives
in a *Web Worker*, so a long document never freezes the page.

```text
  page (main thread)                                  Web Worker
  index.html · app.css · app.js                       worker.js
 ┌────────────────────────────────┐   message    ┌──────────────────────────────────┐
 │ reads the file                 │ ───────────▶ │ Pyodide (CPython in WebAssembly)  │
 │ draws highlights and panel     │              │   bridge.py                       │
 │ keeps the list of corrections  │ ◀─────────── │     └─ pseudonimizzatore_legale   │
 │ handles keys, undo, saving     │    JSON      │        (wheel in vendor/)         │
 └────────────────────────────────┘              └──────────────────────────────────┘
```

**What happens when you open a document:**

1. The page reads the file. A `.docx` goes to `bridge.docx_text`, which extracts the text
   with lxml. A `.pdf` goes to `bridge.pdf_text`, which loads and extracts the text with pypdf in the worker.
2. `bridge.review(text)` calls the engine. `analyze` normalizes the text,
   finds people and data, resolves who is who and assigns the tags. `render` rewrites the
   text once and runs the final check.
3. The bridge returns JSON: the normalized text, the decisions with their positions, the
   panel rows, the tags, and the points to check, mapped back onto the text you see.
4. The page draws the highlights and the panel from that JSON.

The browser keeps the engine’s `report.state` in memory for the open document. A rename
changes the placeholder on one person or data entry. Keeping a single mention or dismissing a
finding records its quote and surrounding text. The bridge returns the complete state to the
engine on the next pass, including the source revision. Reviewing the same source with the
same state reproduces the same output. Undo restores the previous state. Removing an older
correction reapplies the later choices that still have a target.

**Saving.** The `.txt` is the rewritten text. The formatted `.docx` comes from
`bridge.docx_pseudonymized`, which writes the tags into the original file's text runs and
removes the metadata. The plain one comes from `bridge.docx_bytes`.

**Rules that keep it small:**

- The engine knows nothing about the page. The page carries a fixed copy of it (a wheel in
  `vendor/`), updated on purpose.
- Only `bridge.py` crosses into Python. Changing the runtime touches `worker.js` alone.
- No framework, npm, bundler or CDN. Every URL is relative, so the folder can be copied
  as-is into a website.

**Files:**

| file | role |
|---|---|
| `index.html`, `app.css` | the page |
| `app.js` | highlights, panel, keys, the list of corrections, saving |
| `worker.js` | starts Pyodide and calls `bridge.py` |
| `bridge.py` | the only Python the page uses: `review`, `pdf_text`, `docx_text`, `docx_pseudonymized`, `docx_bytes` |
| `test_bridge.py`, `conftest.py` | tests of the bridge, against the same engine the page ships |
| `vendor/` | Pyodide, `regex`, `lxml`, `pypdf` and the engine, pinned and self-hosted ([VENDOR.md](VENDOR.md)) |
| `build_engine_wheel.sh` | rebuilds the engine wheel from a tag of its repository |
| `examples/` | an invented document to try the page on |
| `docs/images/` | the screenshots in this README |
| `LICENSE`, `THIRD_PARTY_NOTICES.md`, `licenses/` | licences |

How the engine works inside (detectors, identities, policy, final check) is explained in
[its ARCHITECTURE.md](https://github.com/giovannipiccioli/pseudonimizzatore-legale/blob/main/ARCHITECTURE.md).

## 8. Development

When changing `app.js`, `worker.js` or `bridge.py`, increment the `?v=` number on
`app.js` in `index.html`. All three files share that version in their URLs so a
browser can load a consistent set of files after an update.

Bridge tests, under ordinary Python with `regex` and `lxml` installed:

```bash
python -m pytest --doctest-modules
```

`conftest.py` puts the engine and pypdf wheels from `vendor/` first on the path, so the tests check the
same engine and PDF importer the page loads.

The browser uses the engine wheel in `vendor/`. Changes in `../anonymizer` become
available to the web app after you rebuild and ship that wheel:

1. Run `./build_engine_wheel.sh vX.Y.Z` here with a sibling checkout of
   `pseudonimizzatore-legale` in `../anonymizer`. Use a new engine version for releases.
   For a local trial, omit the tag to build from the engine working tree.
2. Update `ENGINE_WHEEL` in `worker.js` if the filename changed and update the version
   and SHA-256 in `VENDOR.md`.
3. Run the bridge tests above and try a document in the browser. Commit the wheel and
   associated changes in this repository when releasing the web app. If the engine's
   public API changed, update `bridge.py` and its tests as part of the same release.

To **update Pyodide**, follow `VENDOR.md`, which lists each file with its SHA-256.

## 9. Licence

MIT, see [`LICENSE`](LICENSE). The files in `vendor/` keep their own licences: MPL-2.0 for
Pyodide, PSF for CPython, Apache-2.0 for regex, BSD-3-Clause for pypdf and lxml with MIT and zlib for
its libraries, and LGPL-2.1 for GNU libiconv. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
