# pseudonimizzatore_web

A browser page for reviewing what
[pseudonimizzatore-legale](../anonymizer) does to a document before exporting it. The engine
runs in the browser tab under Pyodide (CPython compiled to WebAssembly), so the document never
leaves the device. Developed here as a standalone tool, it is meant to become a page of
Tributar-IA once mature.

It opens `.txt`, `.md` and `.docx` (body, headers, footers, notes and comments; a file with
tracked changes is refused until they are accepted or rejected in Word). It saves `.txt` or
`.docx`. A `.docx` opened as such comes back with its formatting and the tags in place, its
properties, authors, thumbnail and building blocks cleared; if a redacted text would survive
elsewhere in the file, such as in a link, the page says where and saves a plain `.docx` instead.
Other inputs give the plain `.docx`: one paragraph per line, no formatting or metadata. Images
are kept in the formatted `.docx` and are not checked.

| file | role |
|---|---|
| `index.html`, `app.css` | the page |
| `app.js` | the review loop: highlights, panel, keys, undo/redo, export |
| `worker.js` | Pyodide in a Web Worker, so a long document never freezes the page |
| `bridge.py` | the only Python the page calls: `review`, `docx_text`, `docx_pseudonymized`, `docx_bytes` |
| `test_bridge.py`, `conftest.py` | bridge tests under CPython, against the vendored engine wheel |
| `vendor/` | pinned, self-hosted Pyodide runtime and wheels (see VENDOR.md) |
| `build_engine_wheel.sh` | rebuilds the engine wheel in `vendor/` from `../anonymizer` |

Rules that keep it small and replaceable:

- The engine knows nothing about this page. The page pins a wheel of it.
- Only `bridge.py` crosses into Python. Swapping the runtime touches `worker.js` only.
- Plain HTML, CSS and two ES modules (the page and its worker): no framework, no npm, no
  bundler, no CDN.
- All URLs are relative, so the folder can be copied as-is into the site.

Run:

```bash
/opt/anaconda3/envs/ml/bin/python -m http.server 8766 --bind 127.0.0.1 --directory src/pseudonimizzatore_web
```

Pyodide does not load from `file://`, so the page needs a server, any static one. Tests:
`python -m pytest --doctest-modules` here, with `regex` and `lxml` installed. `conftest.py` puts
the vendored engine wheel first on `sys.path`, so the tests run the engine the page ships.

Performance and browser numbers, and `selftest.html`, which drives this page through its UI in
any browser, are in `../prototyping/anonimizzatore_pyodide` (2026-10-21).

Licence: MIT, see [`LICENSE`](LICENSE). The files in `vendor/` keep their own licences (MPL-2.0,
PSF, Apache-2.0, BSD-3-Clause, MIT, zlib and LGPL-2.1); see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
