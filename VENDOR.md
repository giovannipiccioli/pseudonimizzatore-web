# Vendored files

Everything the page loads is in `vendor/`, served from our own origin. Nothing is fetched from
a CDN at run time.

| file | version | source | sha256 | licence |
|---|---|---|---|---|
| `pyodide-314.0.7/pyodide.mjs` | Pyodide 314.0.7 (Python 3.14.2) | `pyodide-core-314.0.7.tar.bz2`, GitHub release `pyodide/pyodide` 314.0.7 (tarball sha256 `2abdcc2e…3f95`, matches GitHub's digest) | `6f1d60f7…6109` | MPL-2.0 |
| `pyodide-314.0.7/pyodide.asm.mjs` | ″ | ″ | `f7cdc8ec…3635` | MPL-2.0 |
| `pyodide-314.0.7/pyodide.asm.wasm` | ″ | ″ | `cc36e3ca…d5e6` | MPL-2.0, plus CPython (PSF) |
| `pyodide-314.0.7/python_stdlib.zip` | ″ | ″ | `fa1957e5…dd7a` | PSF |
| `pyodide-314.0.7/pyodide-lock.json` | ″ | ″ | `5dc2fc11…c010` | MPL-2.0 |
| `pyodide-314.0.7/regex-2026.3.32-cp314-cp314-pyemscripten_2026_0_wasm32.whl` | regex 2026.3.32 | `cdn.jsdelivr.net/pyodide/v314.0.7/full/`, matches the sha256 in `pyodide-lock.json` | `872e5bba…c1ac` | Apache-2.0 and CNRI-Python (in the wheel) |
| `pyodide-314.0.7/lxml-6.1.3-cp314-cp314-pyemscripten_2026_0_wasm32.whl` | lxml 6.1.3, loaded only when a .docx is opened | `cdn.jsdelivr.net/pyodide/v314.0.7/full/`, matches the sha256 in `pyodide-lock.json` | `6a0b98f5…281f` | BSD-3-Clause; bundled libxml2/libxslt in the wheel's `LICENSES.txt` |
| `pseudonimizzatore_legale-0.4.0-py3-none-any.whl` | engine 0.4.0, tag `v0.4.0` | `./build_engine_wheel.sh v0.4.0` | `3d8c6aa8…abd1` | MIT (in the wheel) |

Licences and sources are in THIRD_PARTY_NOTICES.md, with the texts no wheel carries in
`licenses/`. Pyodide's lxml links GNU libiconv statically (LGPL-2.1), like the PyPI wheels: its
charset table is in `etree`'s binary, and the Pyodide recipes link lxml with `-liconv`. Keep
THIRD_PARTY_NOTICES.md in step when a file here changes.

Update:

- **Engine:** run `./build_engine_wheel.sh` (working tree) or `./build_engine_wheel.sh <tag>`
  (release), then change `ENGINE_WHEEL` in `worker.js` if the version changed.
- **Pyodide:** replace `pyodide-314.0.7/` with a new release's `pyodide.mjs`, `pyodide.asm.mjs`,
  `pyodide.asm.wasm`, `python_stdlib.zip` and `pyodide-lock.json`, plus the `regex` and `lxml`
  wheels listed in its lock file. Check every sha256, change the folder name in `worker.js`, then rerun
  the benchmark in `../prototyping/anonimizzatore_pyodide`.
