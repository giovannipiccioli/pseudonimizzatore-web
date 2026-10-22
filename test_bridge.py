"""bridge.py under CPython: the same file the page runs under Pyodide.

    python -m pytest --doctest-modules        # needs regex and lxml; conftest.py pins the engine
"""
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from pseudonimizzatore_legale import ResidualFinding, analyze, anonymize, render
from bridge import (Refused, docx_bytes, docx_pseudonymized, docx_text, input_findings,
                    output_to_input, partial_keeps, review)

TEXT = ("Il sig. Mario Rossi, C.F. RSSMRA80A01H501U, ricorre contro Luigi Bianchi, "
        "residente in Via Roma 1, Bari. Giudice: dott. Carlo Verdi.")
# Ends with a name the engine does not redact, which verification reports.
SIGNED = TEXT + "\nIl sig. Rossi insiste. Firmato Tizio Caio."


def run(edits=None, text=TEXT):
    return json.loads(review(text, json.dumps(edits or {})))


def tags(result):
    return {entity["entity_id"]: entity["aliases"] for entity in result["entities"]
            if entity["action"] == "redact"}


def span(result, alias, nth=0):
    """The input span of the nth decision on ``alias``."""
    found = [d for d in result["decisions"] if result["text"][d["start"]:d["end"]] == alias]
    return [found[nth]["start"], found[nth]["end"]]


def test_no_edits_matches_the_engine():
    result = run()
    output, report = anonymize(TEXT)
    assert result["output"] == output
    assert result["mapping"] == report.mapping
    assert result["status"] == report.status


def test_decision_offsets_point_into_the_shown_text():
    result = run()
    shown = {result["text"][d["start"]:d["end"]]: d for d in result["decisions"]}
    assert shown["Mario Rossi"]["tag"] == "Nominativo_1"
    assert shown["Carlo Verdi"]["action"] == "keep"


def test_exclude_keeps_the_other_tags_with_prior_tags():
    first = run()
    second = run({"exclude": ["Mario Rossi"], "prior_tags": list(first["mapping"].items())})
    assert "Mario Rossi" in second["output"]
    assert tags(second)["Nominativo_2"] == ["Luigi Bianchi"]
    assert "Nominativo_1" not in tags(second)


def test_people_and_terms_add_redactions():
    result = run({"people": [["Carlo Verdi", "Nominativo"]], "terms": [["Bari", "Dato"]]})
    assert "Carlo Verdi" not in result["output"]
    assert "Bari" not in result["output"]
    assert "Dato_1" in result["output"]


def test_keep_spans_placeholders_and_merge_cross_the_bridge():
    first = run(text=SIGNED)
    result = run({"keep_spans": [span(first, "Rossi")],
                  "placeholders": [["Mario Rossi", "Attore_1"]],
                  "merge": [["Luigi Bianchi", "Mario Rossi"]]}, text=SIGNED)
    assert "Il sig. Rossi insiste" in result["output"]
    assert result["output"].startswith("Il sig. Attore_1, C.F. CF_1, ricorre contro Attore_1,")
    assert tags(result)["Attore_1"] == ["Luigi Bianchi", "Mario Rossi"]


def test_undoing_a_rename_restores_the_number_with_first_tags_as_prior():
    # The engine reads prior_tags as a dict, so it holds one tag per value: the page
    # keeps the first, and the rename's Attore_1 must not replace Nominativo_1.
    first = run()
    renamed = run({"placeholders": [["Mario Rossi", "Attore_1"]],
                   "prior_tags": list(first["mapping"].items())})
    assert renamed["mapping"]["Mario Rossi"] == "Attore_1"
    undone = run({"prior_tags": list({**renamed["mapping"], **first["mapping"]}.items())})
    assert undone["mapping"] == first["mapping"]


def test_unknown_edit_field_is_refused():
    with pytest.raises(ValueError, match="unknown edit fields"):
        review(TEXT, json.dumps({"exclud": ["Mario Rossi"]}))


# --- output offsets back to input offsets ---------------------------------------------

def redact(start, end, tag):
    return SimpleNamespace(start=start, end=end, action="redact", replacement=tag)


def test_output_to_input_through_several_redactions_and_a_keep():
    text = "Rossi e Bianchi, giudice Verdi, e Neri."
    decisions = [redact(0, 5, "Nominativo_1"), redact(8, 15, "N_2"),
                 SimpleNamespace(start=25, end=30, action="keep", replacement=None)]
    output = "Nominativo_1 e N_2, giudice Verdi, e Neri."
    for word in ("giudice", "Verdi", "Neri", " e "):
        out = output.index(word)
        start, end = output_to_input(decisions, out, out + len(word))
        assert text[start:end] == word
    # A span reaching into a tag covers what the tag replaced.
    assert output_to_input(decisions, 0, 14) == (0, 7)
    assert output_to_input(decisions, 13, 17) == (6, 15)


def test_findings_are_mapped_into_the_shown_text():
    analysis = analyze(SIGNED)
    output, report = render(analysis)
    result = run(text=SIGNED)
    assert [f["text"] for f in result["findings"]] == [r.text for r in report.residuals]
    assert "Firmato Tizio Caio" in [f["text"] for f in result["findings"]]
    for finding in result["findings"]:
        assert result["text"][finding["start"]:finding["end"]] == finding["text"]


def finding(start, end, text):
    return ResidualFinding(start, end, text, "PERSON", "verification:name-shape", "")


def test_overlapping_findings_are_merged():
    text = "Il teste Mario Rossi conferma."
    merged = input_findings(text, [], [finding(9, 20, "Mario Rossi"), finding(15, 20, "Rossi")])
    assert [(f["start"], f["end"], f["text"]) for f in merged] == [(9, 20, "Mario Rossi")]
    merged = input_findings(text, [], [finding(12, 20, "io Rossi"), finding(9, 14, "Mario")])
    assert [(f["start"], f["end"], f["text"]) for f in merged] == [(9, 20, "Mario Rossi")]


def test_a_finding_about_the_whole_document_has_no_span():
    coverage = ResidualFinding(0, 0, "", "DOCUMENT", "verification:coverage", "long document")
    [result] = input_findings("x", [redact(0, 1, "Dato_1")], [coverage])
    assert (result["start"], result["end"], result["text"]) == (0, 0, "")


# --- partial keeps -------------------------------------------------------------------

def test_keeping_one_occurrence_of_a_redacted_name_is_a_partial_keep():
    first = run(text=SIGNED)
    result = run({"keep_spans": [span(first, "Rossi")]}, text=SIGNED)
    assert [(p["text"], p["tags"]) for p in result["partial_keeps"]] == [("Rossi", ["Nominativo_1"])]
    assert result["text"][slice(*span(first, "Rossi"))] == "Rossi"


def test_keeping_the_only_occurrence_is_not_a_partial_keep():
    first = run()
    result = run({"keep_spans": [span(first, "RSSMRA80A01H501U")]})
    assert "RSSMRA80A01H501U" in result["output"]
    assert result["partial_keeps"] == []


def test_partial_keeps_ignore_the_engine_keeps():
    judge = SimpleNamespace(start=0, end=11, text="Carlo Verdi", kind="JUDGE", action="keep",
                            replacement=None)
    party = SimpleNamespace(start=20, end=31, text="Mario Verdi", kind="PERSON",
                            action="redact", replacement="Nominativo_1")
    assert partial_keeps([judge, party]) == []


# --- .docx in --------------------------------------------------------------------------

NAMESPACES = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
              'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"')


def part(root, inner):
    return f'<?xml version="1.0" encoding="UTF-8"?><w:{root} {NAMESPACES}>{inner}</w:{root}>'


def p(*texts):
    return "<w:p>" + "".join(f'<w:r><w:t xml:space="preserve">{t}</w:t></w:r>' for t in texts) + "</w:p>"


def docx(body, files=None, **parts):
    """A .docx with ``body`` in word/document.xml, each of ``parts`` as word/<name>.xml,
    and ``files`` as they are named."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", part("document", f"<w:body>{body}</w:body>"))
        for name, xml in parts.items():
            archive.writestr(f"word/{name}.xml", xml)
        for name, content in (files or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_docx_text_reads_every_part_in_order():
    data = docx(
        p("Il sig. Mario Rossi ricorre.") + p("Seconda riga."),
        header1=part("hdr", p("Studio Legale Neri")),
        header2=part("hdr", p("Studio Legale Neri")),     # each part is rewritten in place
        footer1=part("ftr", p("Via Roma 1, Bari")),
        footnotes=part("footnotes",
                       '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
                       f'<w:footnote w:id="1">{p("Nota di Anna Verdi")}</w:footnote>'),
        endnotes=part("endnotes", f'<w:endnote w:id="1">{p("Chiusa")}</w:endnote>'),
        comments=part("comments", f'<w:comment w:id="0" w:author="Revisore">{p("Controllare Bianchi")}</w:comment>'),
    )
    assert docx_text(data) == (
        "[Intestazione]\nStudio Legale Neri\nStudio Legale Neri\n\n"
        "Il sig. Mario Rossi ricorre.\nSeconda riga.\n\n"
        "[Piè di pagina]\nVia Roma 1, Bari\n\n"
        "[Note a piè di pagina]\nNota di Anna Verdi\n\n"
        "[Note di chiusura]\nChiusa\n\n"
        "[Commenti]\nControllare Bianchi")


def test_docx_text_keeps_tabs_and_breaks_and_reads_a_text_box_once():
    body = ('<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr>'
            '<w:r><w:t>a</w:t><w:tab/><w:t>b</w:t><w:br/><w:t>c</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Prima</w:t>'
            f'<mc:AlternateContent><mc:Choice><w:txbxContent>{p("Nel riquadro")}</w:txbxContent></mc:Choice>'
            f'<mc:Fallback><w:txbxContent>{p("Nel riquadro")}</w:txbxContent></mc:Fallback>'
            '</mc:AlternateContent></w:r></w:p>')
    assert docx_text(docx(body)) == "a\tb\nc\nPrima\nNel riquadro"


@pytest.mark.parametrize("revision", [
    '<w:ins w:id="1" w:author="X"><w:r><w:t>nuovo</w:t></w:r></w:ins>',
    '<w:del w:id="1" w:author="X"><w:r><w:delText>vecchio</w:delText></w:r></w:del>',
])
def test_docx_text_refuses_tracked_changes(revision):
    with pytest.raises(Refused, match="accetta o rifiuta"):
        docx_text(docx(f"<w:p>{revision}</w:p>"))


def test_docx_text_refuses_tracked_changes_in_a_comment():
    data = docx(p("Testo"), comments=part(
        "comments", '<w:comment w:id="0"><w:p><w:ins w:id="1"><w:r><w:t>x</w:t></w:r></w:ins></w:p></w:comment>'))
    with pytest.raises(Refused, match="revisioni"):
        docx_text(data)


def strict_docx():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml",
                         '<w:document xmlns:w="http://purl.oclc.org/ooxml/wordprocessingml/main"/>')
    return buffer.getvalue()


def zip_without_document():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("content.xml", "<x/>")
    return buffer.getvalue()


@pytest.mark.parametrize("data", [b"\xd0\xcf\x11\xe0 an old .doc", zip_without_document(),
                                  strict_docx()], ids=["doc", "odt-like", "strict"])
def test_docx_text_refuses_what_is_not_a_docx(data):
    with pytest.raises(Refused, match="non è un documento Word"):
        docx_text(data)


def test_docx_text_accepts_a_memoryview():
    assert docx_text(memoryview(docx(p("Ciao")))) == "Ciao"


# --- .docx out -------------------------------------------------------------------------

def test_docx_bytes_round_trips_through_docx_text():
    text = "Nominativo_1 & <Dato_1>\n\n   rientro\tcon tab\nàèìòù «virgolette»"
    assert docx_text(docx_bytes(text)) == text


def test_docx_bytes_is_a_minimal_package():
    archive = zipfile.ZipFile(io.BytesIO(docx_bytes("riga")))
    assert archive.namelist() == ["[Content_Types].xml", "_rels/.rels", "word/document.xml"]


def test_docx_bytes_drops_characters_xml_cannot_hold():
    assert docx_text(docx_bytes("a\x01b\x08c\nd\x0ce")) == "abc\nd\ne"


# --- .docx out, formatting kept ----------------------------------------------------------

def pseudonymized(data):
    """What the page does with a .docx: analyse its text, then write the tags back."""
    result = json.loads(review(docx_text(data), "{}", False))
    redactions = [[d["start"], d["end"], d["tag"]] for d in result["decisions"] if d["action"] == "redact"]
    return result, docx_pseudonymized(data, json.dumps(redactions))


def read(data, name):
    return zipfile.ZipFile(io.BytesIO(data)).read(name).decode()


SPLIT = ('<w:p><w:r><w:t xml:space="preserve">Il sig. </w:t></w:r>'
         '<w:r><w:rPr><w:b/></w:rPr><w:t>Ma</w:t></w:r>'
         '<w:r><w:t xml:space="preserve">rio Rossi insiste.</w:t></w:r></w:p>')


def test_formatted_export_has_the_text_of_the_plain_one():
    data = docx(
        p("Il sig. Mario Rossi, C.F. RSSMRA80A01H501U, ricorre.") + SPLIT,
        header1=part("hdr", p("Avv. Anna Neri")),
        footnotes=part("footnotes", f'<w:footnote w:id="1">{p("Sentito il sig. Luigi Bianchi.")}</w:footnote>'),
        comments=part("comments", f'<w:comment w:id="0">{p("Il C.F. RSSMRA80A01H501U va verificato.")}</w:comment>'),
    )
    result, formatted = pseudonymized(data)
    assert "Mario Rossi" not in result["output"] and "Luigi Bianchi" not in result["output"]
    assert docx_text(formatted) == result["output"]


def test_a_tag_takes_the_formatting_of_the_run_where_its_text_starts():
    _, formatted = pseudonymized(docx(p("Il sig. Mario Rossi ricorre.") + SPLIT))
    assert ('<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">Nominativo_1</w:t></w:r>'
            '<w:r><w:t xml:space="preserve"> insiste.</w:t></w:r>') in read(formatted, "word/document.xml")


def test_fallback_copies_go_and_leave_no_name_behind():
    box = ('<w:p><w:r><mc:AlternateContent>'
           f'<mc:Choice><w:txbxContent>{p("Il sig. Mario Rossi ricorre.")}</w:txbxContent></mc:Choice>'
           f'<mc:Fallback><w:txbxContent>{p("Il sig. Mario Rossi ricorre.")}</w:txbxContent></mc:Fallback>'
           '</mc:AlternateContent></w:r></w:p>')
    _, formatted = pseudonymized(docx(box))
    document = read(formatted, "word/document.xml")
    assert "Fallback" not in document and "Mario Rossi" not in document and "Nominativo_1" in document


def test_who_wrote_and_edited_the_file_is_cleared():
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="t" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="t" Target="docProps/thumbnail.jpeg"/></Relationships>')
    data = docx(p("Il sig. Mario Rossi ricorre."), files={
        "_rels/.rels": rels,
        "docProps/thumbnail.jpeg": b"\xff\xd8 a picture of page one",
        "docProps/core.xml": '<cp:coreProperties xmlns:cp="c" xmlns:dc="d"><dc:creator>Paola Conti</dc:creator></cp:coreProperties>',
        "word/people.xml": '<w15:people xmlns:w15="w"><w15:person w15:author="Paola Conti"/></w15:people>',
    }, comments=part("comments", f'<w:comment w:id="0" w:author="Paola Conti" w:initials="PC">{p("Ok")}</w:comment>'))
    _, formatted = pseudonymized(data)
    archive = zipfile.ZipFile(io.BytesIO(formatted))
    assert "docProps/thumbnail.jpeg" not in archive.namelist()
    assert "thumbnail" not in read(formatted, "_rels/.rels") and "word/document.xml" in read(formatted, "_rels/.rels")
    for name in archive.namelist():
        assert "Paola Conti" not in read(formatted, name), name
    assert 'w:author=""' in read(formatted, "word/comments.xml")


def test_a_templates_building_blocks_are_emptied():
    # Found in documents made from a Word template: its glossary repeated a body name.
    glossary = part("glossaryDocument", f'<w:docParts><w:docPart>{p("Il sig. Mario Rossi")}</w:docPart></w:docParts>')
    _, formatted = pseudonymized(docx(p("Il sig. Mario Rossi ricorre."), files={"word/glossary/document.xml": glossary}))
    assert "Mario Rossi" not in read(formatted, "word/glossary/document.xml")


def test_a_redacted_text_left_outside_the_text_is_refused():
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId9" Type="hyperlink" TargetMode="External" '
            'Target="mailto:mario.rossi@pec.it"/></Relationships>')
    data = docx(p("Il sig. Mario Rossi, PEC mario.rossi@pec.it, ricorre."),
                files={"word/_rels/document.xml.rels": rels})
    with pytest.raises(Refused, match=r"mario\.rossi@pec\.it.*document\.xml\.rels"):
        pseudonymized(data)


def test_a_redacted_word_inside_a_longer_one_is_not_a_leak():
    styles = part("styles", '<w:style w:styleId="Normale"><w:rPr><w:rFonts w:ascii="Times New Roman"/></w:rPr></w:style>')
    result, formatted = pseudonymized(docx(p("Il sig. Mario Rossi, nato a Roma il 3 marzo 1970, ricorre."),
                                           styles=styles))
    assert "Luogo_1" in result["output"] and docx_text(formatted) == result["output"]
