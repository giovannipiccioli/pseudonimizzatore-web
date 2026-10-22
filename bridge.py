"""The only Python the page calls:

    review(raw, edits_json, sanitize) -> result_json    analyse, render and verify one document
    docx_text(data) -> str                              the text of a .docx (needs lxml)
    docx_pseudonymized(data, redactions_json) -> bytes  that .docx with the tags in place
    docx_bytes(text) -> bytes                           a plain .docx, one paragraph per line

The browser runs this file under Pyodide in a Web Worker, and the tests run it under
CPython. It uses only the engine's public API (`analyze`, `render`, `Edits`), so the
engine never needs to know about the page. `Refused` carries a message for the reader.
"""
from __future__ import annotations

import html
import io
import json
import re
import zipfile
from collections import defaultdict
from dataclasses import asdict, fields
from urllib.parse import unquote
from xml.sax.saxutils import escape

from pseudonimizzatore_legale import Config, Edits, analyze, render

_EDIT_FIELDS = frozenset(field.name for field in fields(Edits))


class Refused(ValueError):
    """A file the page cannot read, with a message in Italian for the reader."""


def _edits(data: dict) -> Edits:
    """`Edits` from JSON: each list becomes the tuple `Edits` expects.

    >>> _edits({"exclude": ["Rossi"], "people": [["Anna Verdi", "Nominativo"]]}).people
    (('Anna Verdi', 'Nominativo'),)
    """
    unknown = set(data) - _EDIT_FIELDS
    if unknown:
        raise ValueError(f"unknown edit fields: {sorted(unknown)}")
    return Edits(**{
        name: tuple(tuple(item) if isinstance(item, list) else item for item in items)
        for name, items in data.items()
    })


def output_to_input(decisions, start: int, end: int) -> tuple[int, int]:
    """Map an output span back to the normalized input, through the rendered decisions.

    Only redactions change lengths. An end inside a tag maps to the end of what the tag
    replaced, and a start inside one to its start:

    >>> from types import SimpleNamespace as D
    >>> rossi = D(start=8, end=19, action="redact", replacement="Nominativo_1")
    >>> output_to_input([rossi], 0, 7), output_to_input([rossi], 21, 28)
    ((0, 7), (20, 27))
    >>> output_to_input([rossi], 8, 13)
    (8, 19)
    """
    def locate(position: int, is_end: bool) -> int:
        shift = 0
        for decision in decisions:
            if decision.action != "redact":
                continue
            out_start = decision.start + shift
            out_end = out_start + len(decision.replacement)
            inside = (out_start < position <= out_end) if is_end else (out_start <= position < out_end)
            if inside:
                return decision.end if is_end else decision.start
            if position <= out_start:
                break
            shift += len(decision.replacement) - (decision.end - decision.start)
        return position - shift

    return locate(start, False), locate(end, True)


def input_findings(text: str, decisions, residuals) -> list[dict]:
    """Verification findings in input offsets, sorted, with overlapping ones merged.

    ``text`` is the normalized input; each finding's ``text`` is read from it, since
    that is what the page shows and what the reviewer may add. A finding about the
    whole document has no span: ``start == end == 0`` and empty ``text``.
    """
    mapped = []
    for finding in residuals:
        start, end = output_to_input(decisions, finding.start, finding.end)
        mapped.append({**asdict(finding), "start": start, "end": end})
    merged: list[dict] = []
    for finding in sorted(mapped, key=lambda finding: (finding["start"], -finding["end"])):
        if merged and finding["start"] < merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], finding["end"])
        else:
            merged.append(finding)
    for finding in merged:
        finding["text"] = text[finding["start"]:finding["end"]]
    return merged


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def partial_keeps(decisions) -> list[dict]:
    """Reviewer keeps whose words are still pseudonymized elsewhere, with those tags.

    Leaving one "Rossi" in clear while "Lucia Rossi" is `Nominativo_1` everywhere else
    lets a reader link the two, and so undo the tag.
    """
    redactions = [(decision.replacement, _words(decision.text))
                  for decision in decisions if decision.action == "redact"]
    warnings = []
    for decision in decisions:
        words = _words(decision.text)
        if decision.kind != "USER" or not words:
            continue
        tags = sorted({tag for tag, other in redactions if words <= other})
        if tags:
            warnings.append({"start": decision.start, "end": decision.end,
                             "text": decision.text, "tags": tags})
    return warnings


def review(raw: str, edits_json: str = "{}", sanitize: bool = True) -> str:
    """Analyse ``raw`` with the reviewer's edits, render it, and return both as JSON.

    Every offset refers to ``text``, the normalized input, which is what the page
    shows: ``findings`` are the engine's residuals mapped back from the output. Send
    ``mapping`` back as ``prior_tags`` so that tags keep their numbers between edits.
    ``sanitize=False`` skips the engine's clean-up pass, so that ``text`` has the
    offsets of ``raw`` (see `docx_text`).
    """
    analysis = analyze(raw, Config(sanitize=sanitize), edits=_edits(json.loads(edits_json)))
    output, report = render(analysis)
    return json.dumps({
        "text": analysis.text,
        "output": output,
        "decisions": [
            {"start": d.start, "end": d.end, "action": d.action, "kind": d.kind,
             "tag": d.replacement}
            for d in analysis.decisions
        ],
        "entities": [asdict(entity) for entity in analysis.entities],
        "mapping": analysis.mapping,
        "status": report.status,
        "findings": input_findings(analysis.text, analysis.decisions, report.residuals),
        "partial_keeps": partial_keeps(analysis.decisions),
        "warnings": report.warnings,
    }, ensure_ascii=False)


# --- .docx -------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
# Revisions that change the text. Tracked formatting changes leave it as it is.
_REVISIONS = tuple(_W + name for name in ("ins", "del", "moveFrom", "moveTo", "cellIns", "cellDel"))
_NOTE_SEPARATORS = {"separator", "continuationSeparator", "continuationNotice"}
# (file name pattern, element holding one item or None for the whole part, heading)
_PARTS = (
    (r"word/header\d*\.xml", None, "[Intestazione]"),
    (r"word/document\.xml", None, None),
    (r"word/footer\d*\.xml", None, "[Piè di pagina]"),
    (r"word/footnotes\.xml", _W + "footnote", "[Note a piè di pagina]"),
    (r"word/endnotes\.xml", _W + "endnote", "[Note di chiusura]"),
    (r"word/comments\.xml", _W + "comment", "[Commenti]"),
)
_NOT_DOCX = ("Il file non è un documento Word .docx leggibile. I file .doc e quelli protetti "
             "da password non sono supportati: salvali da Word come .docx.")
_TRACKED = ("Il documento contiene revisioni (modifiche rilevate). Aprilo in Word, accetta o "
            "rifiuta tutte le modifiche (Revisione › Accetta / Rifiuta) e salvalo di nuovo.")


def _xml(data: bytes):
    from lxml import etree      # loaded on demand: only a .docx needs it

    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))


def _pieces(element):
    """One paragraph's text as ``(text, w:t element or None)`` pieces, without its
    properties, a text box's own paragraphs, or the fallback copy of a drawing."""
    for child in element:
        if child.tag in (_W + "p", _W + "pPr", _W + "rPr", _FALLBACK):
            continue
        if child.tag == _W + "t":
            yield child.text or "", child
        elif child.tag == _W + "tab":
            yield "\t", None
        elif child.tag in (_W + "br", _W + "cr"):
            yield "\n", None
        elif child.tag == _W + "noBreakHyphen":
            yield "-", None
        else:
            yield from _pieces(child)


def _joined(groups, separator: str) -> list:
    pieces = []
    for index, group in enumerate(groups):
        if index:
            pieces.append((separator, None))
        pieces.extend(group)
    return pieces


def _layout(data):
    """Open a .docx: its text as pieces, its parsed text parts, and the archive.

    Joined, the pieces are `docx_text`. A piece read from a ``w:t`` carries that
    element, so an offset into the text finds the run it came from.
    """
    from lxml import etree

    try:
        archive = zipfile.ZipFile(io.BytesIO(bytes(data)))
        names = archive.namelist()
        if "word/document.xml" not in names:
            raise Refused(_NOT_DOCX)
        roots, sections = {}, []
        for pattern, item, heading in _PARTS:
            items = []
            for name in sorted((name for name in names if re.fullmatch(pattern, name)),
                               key=lambda name: (len(name), name)):
                root = roots[name] = _xml(archive.read(name))
                if name == "word/document.xml" and root.tag != _W + "document":
                    raise Refused(_NOT_DOCX)
                if next(root.iter(*_REVISIONS), None) is not None:
                    raise Refused(_TRACKED)
                for part in ([root] if item is None else
                             [part for part in root.iter(item) if part.get(_W + "type") not in _NOTE_SEPARATORS]):
                    pieces = _joined([list(_pieces(paragraph)) for paragraph in part.iter(_W + "p")
                                      if next(paragraph.iterancestors(_FALLBACK), None) is None], "\n")
                    if "".join(text for text, _ in pieces).strip():
                        items.append(pieces)
            if items:
                sections.append(([(heading + "\n", None)] if heading else []) + _joined(items, "\n"))
    except (zipfile.BadZipFile, etree.XMLSyntaxError) as error:
        raise Refused(_NOT_DOCX) from error
    return _joined(sections, "\n\n"), roots, archive


def docx_text(data) -> str:
    """The text of a .docx: headers, body, footers, footnotes, endnotes and comments,
    one paragraph per line, each part after a heading in brackets.

    Raises `Refused` for a file that is not a .docx, and for one with tracked changes,
    whose text depends on whether they are accepted. Analyse the text with
    ``review(..., sanitize=False)``: Word text needs none of the clean-up meant for
    markdown, HTML or OCR, and the offsets then point into it, as
    `docx_pseudonymized` needs.
    """
    return "".join(text for text, _ in _layout(data)[0])


_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
# Parts that say who wrote or edited the file, or keep a template's building blocks
# (which can repeat the text's names), replaced by empty ones of the same kind.
_EMPTIED = {
    "word/glossary/document.xml":
        '<w:glossaryDocument xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
    "docProps/core.xml":
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"/>',
    "docProps/app.xml":
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"/>',
    "docProps/custom.xml":
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"/>',
    "word/people.xml": '<w15:people xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"/>',
}
_THUMBNAIL = re.compile(r"docProps/thumbnail\.\w+")


def _hidden(root):
    """Every text and attribute value of a part but its ``w:t`` text, which is rewritten."""
    from lxml import etree

    for element in root.iter(etree.Element):
        if element.tag != _W + "t" and element.text:
            yield element.text
        yield from element.attrib.values()


def docx_pseudonymized(data, redactions_json: str) -> bytes:
    """The original .docx with each redaction's text replaced by its tag, in place.

    ``redactions_json`` is ``[[start, end, tag], ...]`` in `docx_text` offsets. A tag
    takes the formatting of the run where its text starts, and the rest of that text
    is cut from the runs that follow. Styles, tables, images and notes stay.

    What says who wrote or edited the file is cleared: the document properties, the
    authors of comments and of tracked formatting, the people list, the thumbnail,
    the fallback copies of drawings, and a template's building blocks. Raises `Refused` if a redacted text still
    appears anywhere else in the package, such as a link target or a field code, so
    that the page can offer the plain `docx_bytes` instead.
    """
    from lxml import etree

    pieces, roots, archive = _layout(data)
    redactions = sorted(tuple(redaction) for redaction in json.loads(redactions_json))
    changes = defaultdict(list)          # w:t element -> [(start, end, replacement)]
    placed = set()                       # redactions whose tag is written already
    first = offset = 0
    for text, element in pieces:
        start, end = offset, offset + len(text)
        offset = end
        while first < len(redactions) and redactions[first][1] <= start:
            first += 1
        index = first
        while element is not None and index < len(redactions) and redactions[index][0] < end:
            begin, finish, tag = redactions[index]
            changes[element].append((max(begin, start) - start, min(finish, end) - start,
                                     "" if index in placed else tag))
            placed.add(index)
            index += 1
    for element, edits in changes.items():
        text = element.text or ""
        for start, end, replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[end:]
        element.text = text
        element.set(_XML_SPACE, "preserve")
    for root in roots.values():
        for fallback in list(root.iter(_FALLBACK)):
            fallback.getparent().remove(fallback)
        for element in root.iter(etree.Element):
            for name in (_W + "author", _W + "initials"):
                if name in element.attrib:
                    element.set(name, "")

    whole = "".join(text for text, _ in pieces)
    values = sorted({" ".join(whole[start:end].split()) for start, end, _ in redactions} - {""},
                    key=len, reverse=True)
    leak = re.compile("|".join(r"(?<!\w)" + r"\s+".join(map(re.escape, value.split())) + r"(?!\w)"
                               for value in values), re.I) if values else None
    thumbnails = {name for name in archive.namelist() if _THUMBNAIL.fullmatch(name)}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for info in archive.infolist():
            name = info.filename
            if name in thumbnails:
                continue
            if name in roots:
                body = etree.tostring(roots[name], xml_declaration=True, encoding="UTF-8", standalone=True)
                scanned = "\n".join(_hidden(roots[name]))
            elif name in _EMPTIED:
                body = (_DECLARATION + _EMPTIED[name]).encode()
                scanned = ""
            else:
                body = archive.read(name)
                if name == "_rels/.rels" and thumbnails:
                    rels = _xml(body)
                    for relation in list(rels):
                        if relation.get("Target", "").lstrip("/") in thumbnails:
                            rels.remove(relation)
                    body = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
                scanned = (html.unescape(body.decode("utf-8", "ignore"))
                           if name.endswith((".xml", ".rels", ".vml")) else "")
            found = leak and leak.search(unquote(scanned))
            if found:
                raise Refused(f"«{found.group()}» resta in {name}, fuori dal testo del documento.")
            package.writestr(info, body)       # keeps the name, date and compression
    return buffer.getvalue()


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>')
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>')
_DOCUMENT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:body>{}<w:sectPr/></w:body></w:document>')
# Characters XML 1.0 does not allow, such as the form feed a PDF conversion leaves.
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


def _paragraph(line: str) -> str:
    """One line as a paragraph, its tabs as Word tabs.

    >>> _paragraph("a\\tb & c")
    '<w:p><w:r><w:t xml:space="preserve">a</w:t><w:tab/><w:t xml:space="preserve">b &amp; c</w:t></w:r></w:p>'
    """
    if not line:
        return "<w:p/>"
    runs = "<w:tab/>".join(f'<w:t xml:space="preserve">{escape(part)}</w:t>' if part else ""
                           for part in line.split("\t"))
    return f"<w:p><w:r>{runs}</w:r></w:p>"


def docx_bytes(text: str) -> bytes:
    """A plain .docx of ``text``, one paragraph per line, with no formatting or metadata."""
    body = "".join(_paragraph(_NOT_XML.sub("", line)) for line in text.splitlines())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", _DOCUMENT.format(body))
    return buffer.getvalue()
