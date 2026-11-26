# Third-party notices

The page runs in the browser from the files in `vendor/`, served from the same origin as the
page. None of them is modified. Each is listed below with its licence and its source. The full
licence texts are in `licenses/` or inside the wheel named. `VENDOR.md` gives the sha256 of
every file.

## Pyodide 314.0.7: MPL-2.0

`vendor/pyodide-314.0.7/pyodide.mjs`, `pyodide.asm.mjs`, `pyodide.asm.wasm` and
`pyodide-lock.json`, from the release asset `pyodide-core-314.0.7.tar.bz2`.

- Licence: Mozilla Public License 2.0, [`licenses/MPL-2.0.txt`](licenses/MPL-2.0.txt).
- Source code: <https://github.com/pyodide/pyodide/tree/314.0.7>, released at
  <https://github.com/pyodide/pyodide/releases/tag/314.0.7>.

## CPython 3.14.2: PSF License

The interpreter is compiled into `pyodide.asm.wasm`, and its standard library is
`python_stdlib.zip`.

- Licence: [`licenses/CPython-LICENSE.txt`](licenses/CPython-LICENSE.txt), the LICENSE file of
  CPython 3.14.2. It also covers the third-party code that CPython includes.
- Source code: <https://github.com/python/cpython/tree/v3.14.2>.

## regex 2026.3.32: Apache-2.0 and CNRI-Python

`vendor/pyodide-314.0.7/regex-2026.3.32-cp314-cp314-pyemscripten_2026_0_wasm32.whl`, Pyodide's
build.

- Licence: inside the wheel, `regex-2026.3.32.dist-info/licenses/LICENSE.txt`.
- Source code: <https://pypi.org/project/regex/2026.3.32/>.

## lxml 6.1.3: BSD-3-Clause, with statically linked libraries

`vendor/pyodide-314.0.7/lxml-6.1.3-cp314-cp314-pyemscripten_2026_0_wasm32.whl`, Pyodide's build.
The page loads it only when a `.docx` is opened.

- Licence: inside the wheel, `lxml-6.1.3.dist-info/licenses/LICENSE.txt` and `LICENSES.txt`.
  `LICENSES.txt` also carries the MIT texts of libxml2, libxslt and libexslt.
- Source code: <https://pypi.org/project/lxml/6.1.3/>.
- Build: the `lxml`, `libxml`, `libxslt`, `libzlib` and `libiconv` recipes in
  <https://github.com/pyodide/pyodide-recipes>. Pyodide builds each library as a static library
  and links all of them into lxml's `etree` module, with `-liconv` passed to the lxml build.

The statically linked libraries are:

| library | licence |
|---|---|
| libxml2, libxslt, libexslt | MIT (texts in the wheel's `LICENSES.txt`) |
| zlib | zlib licence |
| GNU libiconv | LGPL-2.1, see below |

### GNU libiconv: LGPL-2.1

lxml's `etree` module in this wheel contains GNU libiconv, statically linked. The binary
carries libiconv's own charset table. Pyodide's `libiconv` recipe builds version 1.16 as a
static library, and the `libxml` recipe is configured `--with-iconv`. GNU libiconv is free
software under the GNU Lesser General Public License, version 2.1:
[`licenses/LGPL-2.1.txt`](licenses/LGPL-2.1.txt).

- libiconv source code: <https://ftp.gnu.org/pub/gnu/libiconv/libiconv-1.16.tar.gz>. Pyodide's
  recipe builds from a mirror of that tarball.
- To use a modified libiconv, rebuild the lxml wheel against it from lxml's source and the
  Pyodide recipes above. Then replace the file in `vendor/pyodide-314.0.7/` and update its
  sha256 in `pyodide-lock.json`. The page loads whichever wheel is there.

## pypdf 7.1.1 (BSD-3-Clause)

The page loads `vendor/pypdf-7.1.1-py3-none-any.whl` on PDF import.

- Licence: `pypdf-7.1.1.dist-info/licenses/LICENSE` in the wheel.
- Source: [pypdf 7.1.1 on PyPI](https://pypi.org/project/pypdf/7.1.1/).

## pseudonimizzatore-legale 0.8.0: MIT

`vendor/pseudonimizzatore_legale-0.8.0-py3-none-any.whl`, built by `build_engine_wheel.sh` from
commit `7ff5f69` of <https://github.com/giovannipiccioli/pseudonimizzatore-legale>.

- Licence: inside the wheel, `LICENSE` and `THIRD_PARTY_NOTICES.md`. The latter covers the
  bundled list of first names and the list of ISTAT comuni, both CC BY 4.0.
