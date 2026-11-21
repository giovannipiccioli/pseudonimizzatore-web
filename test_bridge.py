"""bridge.py under CPython: the same file the page runs under Pyodide.

    python -m pytest --doctest-modules        # needs regex and lxml; conftest.py pins the engine
"""
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pseudonimizzatore_legale import Config, ResidualFinding, analyze, anonymize, render
from bridge import (Refused, docx_bytes, docx_pseudonymized, docx_text, input_findings,
                    output_to_input, partial_keeps, pdf_text, review)

TEXT = ("Il sig. Mario Rossi, C.F. RSSMRA80A01H501U, ricorre contro Luigi Bianchi, "
        "residente in Via Roma 1, Bari. Giudice: dott. Carlo Verdi.")
# Ends with a name the engine does not redact, which verification reports.
SIGNED = TEXT + "\nIl sig. Rossi insiste. Firmato Tizio Caio."


def run(state=None, text=TEXT, action=None):
    return json.loads(review(text, json.dumps(state), True, json.dumps(action)))


def correct(first, kind, **fields):
    return run(first["state"], first["text"], {"type": kind, **fields})


def occurrence(result, kind, alias, nth=0):
    decisions = [d for d in result["decisions"] if result["text"][d["start"]:d["end"]] == alias]
    decision = decisions[nth]
    return correct(result, kind, start=decision["start"], end=decision["end"])


@pytest.mark.parametrize("package", ["pseudonimizzatore_legale", "pypdf"])
def test_worker_loads_the_same_wheel_as_the_bridge_tests(package):
    root = Path(__file__).parent
    wheel, = root.glob(f"vendor/{package}-*.whl")
    assert f'"vendor/{wheel.name}"' in (root / "worker.js").read_text()


def test_first_pass_and_state_replay_use_the_web_judge_policy():
    config = Config(keep_roles=("giudice",))
    for text in (TEXT, SIGNED, "Nato a Roma. Tribunale di Roma.", "Nessun dato."):
        first = run(text=text)
        output, report = anonymize(text, config)
        assert first["output"] == output
        assert first["state"] == report.state
        assert run(first["state"], text) == first
    shown = {TEXT[d["start"]:d["end"]]: d for d in run()["decisions"]}
    assert shown["Carlo Verdi"]["action"] == "keep"
    assert shown["Mario Rossi"]["tag"] == "Nominativo_1"


def test_global_keep_and_undo_preserve_other_tags():
    first = run()
    person, other, _ = first["state"]["people"]
    kept = correct(first, "exclude", id=person["id"])
    assert "Mario Rossi" in kept["output"]
    assert kept["state"]["people"][0]["placeholder"] == person["placeholder"]
    assert other in kept["state"]["people"]
    assert run(first["state"])["output"] == first["output"]


def test_add_person_and_literal_with_propagation_and_rename():
    first = run(text="Verbale: zefiro. Nota: ZEFIRO. Firma: Ada Quercetani. La Quercetani conferma.")
    person = correct(first, "person", text="Ada Quercetani", id="web-1")
    added = correct(person, "term", text="zefiro", id="web-2")
    renamed = correct(added, "redact", id="web-2", tag="[Progetto]")
    assert renamed["output"].count("Nominativo_1") == 2
    assert renamed["output"].count("[Progetto]") == 2
    assert run(renamed["state"], first["text"])["output"] == renamed["output"]
    assert run(renamed["state"], "Ada Quercetani firma per zefiro.")["output"] == "Nominativo_1 firma per [Progetto]."


def test_explicit_all_matches_can_expand_a_contextual_discovery():
    first = run(text="Tribunale di Roma. Nato a Roma.")
    assert first["output"] == "Tribunale di Roma. Nato a Luogo_1."
    all_copies = correct(first, "term", text="Roma", id="web-1")
    assert all_copies["output"] == "Tribunale di Luogo_1. Nato a Luogo_1."
    assert run(all_copies["state"], first["text"])["output"] == all_copies["output"]


def test_include_only_one_occurrence_and_replace_it_with_a_keep():
    text = "📄 codice zefiro; secondo codice zefiro."
    start = text.rindex("zefiro")
    result = correct(run(text=text), "include", start=start, end=start + 6)
    assert result["output"] == "📄 codice zefiro; secondo codice Dato_1."
    assert run(result["state"], text)["output"] == result["output"]
    [choice] = result["state"]["occurrences"]
    assert choice["placeholder"] == "Dato_1" and choice["anchor"]["quote"] == "zefiro"
    kept = correct(result, "keep", start=start, end=start + 6)
    assert kept["output"] == text and not kept["unresolved"]
    assert [r["action"] for r in kept["state"]["occurrences"]] == ["keep"]
    revised = run(result["state"], "prima versione.")
    assert revised["unresolved"] == [choice]


def test_selective_undo_preserves_allocated_tags_and_skips_dependent_renames():
    first = run(text="codice alfa e codice beta.")
    a = correct(first, "term", id="web-1", text="alfa")
    b = correct(a, "term", id="web-2", text="beta")
    tag = b["state"]["data"][1]["placeholder"]
    without_a = correct(first, "term", id="web-2", text="beta", placeholder=tag)
    assert without_a["output"] == "codice alfa e codice Dato_2."
    dependent = correct(without_a, "redact", id="web-1", tag="Altro")
    assert not dependent["applied"] and dependent["state"] == without_a["state"]
    # Undoing an earlier rename can also restore ownership of the requested label.
    retry = correct(a, "term", id="web-2", text="beta", placeholder="Dato_1")
    assert retry["output"] == b["output"]


def test_merge_data_and_retarget_linked_occurrence():
    text = "Contatti: primo@example.test e secondo@example.test. Firma: recapito."
    first = run(text=text)
    a, b = first["state"]["data"]
    first["state"]["occurrences"].append({
        "action": "redact", "entity_id": a["id"], "anchor": {"quote": "recapito"}})
    merged = correct(first, "merge", id=a["id"], into=b["id"])
    assert merged["output"].count(b["placeholder"]) == 3
    assert merged["state"]["occurrences"][0]["entity_id"] == b["id"]
    assert run(merged["state"], text)["output"] == merged["output"]


def test_kept_judge_can_be_redacted_here_then_everywhere_with_the_same_label():
    first = run(text="Giudice: dott. Carlo Verdi. Il giudice Carlo Verdi firma.")
    judge, = first["state"]["people"]
    last = first["decisions"][-1]
    local = correct(first, "include", start=last["start"], end=last["end"], tag="Firma_7")
    assert local["output"] == "Giudice: dott. Carlo Verdi. Il giudice Firma_7 firma."
    assert local["decisions"][-1]["owner_id"] == judge["id"]
    both = correct(local, "redact", id=judge["id"], tag="Firma_7")
    assert both["output"].count("Firma_7") == 2
    assert both["state"]["occurrences"] == []
    assert run(both["state"], first["text"])["output"] == both["output"]


def test_local_reuse_links_one_mention_and_global_merge_keeps_other_choices():
    first = run(text=SIGNED)
    a, b, _ = first["state"]["people"]
    surname = next(d for d in first["decisions"] if SIGNED[d["start"]:d["end"]] == "Rossi")
    local = correct(first, "include", start=surname["start"], end=surname["end"], target=b["id"])
    assert len(local["state"]["people"]) == 3
    assert local["output"].count(a["placeholder"]) == 1 and local["output"].count(b["placeholder"]) == 2
    assert local["state"]["occurrences"][0]["entity_id"] == b["id"]
    assert next(d for d in local["decisions"] if d["start"] == surname["start"])["owner_id"] == a["id"]
    [finding] = local["findings"]
    dismissed = correct(local, "ignore", spans=finding["spans"])
    merged = correct(dismissed, "merge", id=a["id"], into=b["id"])
    assert merged["output"].count(b["placeholder"]) == 3
    assert [c["action"] for c in merged["state"]["occurrences"]] == ["dismiss"]


def test_finding_can_be_redacted_with_a_custom_label_at_each_scope():
    text = "Firmato Tizio Caio!\nFirma: Firmato Tizio Caio."
    first = run(text=text)
    quote = "Firmato Tizio Caio"
    local = correct(first, "include", start=0, end=len(quote), tag="Firma_4")
    assert local["output"].count("Firma_4") == 1
    for scope in ("term", "person"):
        both = correct(first, scope, id="web-1", text=quote, tag="Firma_4")
        assert both["output"].count("Firma_4") == 2


def test_repeated_identical_lines_refuse_ambiguous_choice():
    text = "codice zefiro.\ncodice zefiro."
    with pytest.raises(Refused, match="univoco"):
        correct(run(text=text), "include", start=7, end=13)


def test_merge_then_rename_preserves_aliases_and_local_exceptions():
    first = run(text=SIGNED)
    mario, luigi, _ = first["state"]["people"]
    merged = correct(first, "merge", id=luigi["id"], into=mario["id"])
    renamed = correct(merged, "redact", id=mario["id"], tag="Attore_1")
    kept = occurrence(renamed, "keep", "Rossi")
    assert "Il sig. Rossi insiste" in kept["output"]
    assert kept["output"].startswith("Il sig. Attore_1, C.F. CF_1, ricorre contro Attore_1,")
    assert kept["state"]["people"][0]["aliases"] == ["Luigi Bianchi"]
    entity = next(e for e in kept["entities"] if e["entity_id"] == mario["id"])
    assert entity["action"] == "mixed"
    assert run(kept["state"], SIGNED)["output"] == kept["output"]


def test_dismissal_is_replayed_by_the_engine_at_one_passage():
    first = run(text=SIGNED)
    [finding] = first["findings"]
    reviewed = correct(first, "ignore", spans=finding["spans"])
    assert not reviewed["findings"]
    assert reviewed["output"] == first["output"]
    [dismiss] = reviewed["state"]["occurrences"]
    assert dismiss["action"] == "dismiss" and dismiss["anchor"]["quote"] == finding["text"]
    assert not run(reviewed["state"], SIGNED)["findings"]


@pytest.mark.parametrize("treatment", ["redact", "dismiss"])
def test_bulk_finding_treatment_keeps_existing_review_choices_and_replays(treatment):
    text = SIGNED + "\nTestimone Aldo Mancini. Firma Neri Celeste."
    first = run(text=text)
    first = correct(first, "exclude", id=first["state"]["people"][0]["id"])
    assert len(first["findings"]) == 2
    groups = [f["spans"] if treatment == "dismiss" else [[f["start"], f["end"]]]
              for f in first["findings"]]
    bulk = correct(first, "bulk_findings", treatment=treatment, findings=groups)
    assert bulk["bulk"] == {"count": 2, "skipped": 0}
    assert bulk["applied"] and not bulk["findings"] and not bulk["unresolved"]
    assert bulk["state"]["people"] == first["state"]["people"]
    if treatment == "redact":
        assert "Dato_1" in bulk["output"] and "Dato_2" in bulk["output"]
        assert "Tizio Caio" not in bulk["output"] and "Neri Celeste" not in bulk["output"]
    else:
        assert bulk["output"] == first["output"]
    assert run(bulk["state"], text)["output"] == bulk["output"]
    assert not run(bulk["state"], text)["findings"]
    assert run(first["state"], text)["findings"] == first["findings"]


def test_bulk_redaction_leaves_a_finding_that_overlaps_an_existing_decision():
    first = run(text=SIGNED)
    person = next(d for d in first["decisions"] if d["kind"] == "PERSON")
    finding, = first["findings"]
    bulk = correct(first, "bulk_findings", treatment="redact", findings=[
        [[person["start"], person["end"] + 1]], [[finding["start"], finding["end"]]],
    ])
    assert bulk["bulk"] == {"count": 1, "skipped": 1}
    assert bulk["decisions"][0] == person
    assert len(bulk["state"]["occurrences"]) == 1 and not bulk["unresolved"]


def test_bulk_dismissal_leaves_an_ambiguous_finding_in_the_list():
    text = "codice zefiro.\ncodice zefiro.\nFirmato Tizio Caio."
    first = run(text=text)
    finding, = first["findings"]
    bulk = correct(first, "bulk_findings", treatment="dismiss",
                   findings=[[[7, 13]], finding["spans"]])
    assert bulk["bulk"] == {"count": 1, "skipped": 1}
    assert [c["anchor"]["quote"] for c in bulk["state"]["occurrences"]] == [finding["text"]]


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
    analysis = analyze(SIGNED, Config(keep_roles=("giudice",)))
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
    assert merged[0]["spans"] == [(9, 20), (15, 20)]
    merged = input_findings(text, [], [finding(12, 20, "io Rossi"), finding(9, 14, "Mario")])
    assert [(f["start"], f["end"], f["text"]) for f in merged] == [(9, 20, "Mario Rossi")]


def test_a_finding_about_the_whole_document_has_no_span():
    coverage = ResidualFinding(0, 0, "", "DOCUMENT", "verification:coverage", "long document")
    [result] = input_findings("x", [redact(0, 1, "Dato_1")], [coverage])
    assert (result["start"], result["end"], result["text"]) == (0, 0, "")


# --- partial keeps -------------------------------------------------------------------

def test_keeping_one_occurrence_of_a_redacted_name_is_a_partial_keep():
    first = run(text=SIGNED)
    result = occurrence(first, "keep", "Rossi")
    assert [(p["text"], p["tags"]) for p in result["partial_keeps"]] == [("Rossi", ["Nominativo_1"])]


def test_keeping_the_only_occurrence_is_not_a_partial_keep():
    first = run()
    result = occurrence(first, "keep", "RSSMRA80A01H501U")
    assert "RSSMRA80A01H501U" in result["output"]
    assert result["partial_keeps"] == []


def test_partial_keeps_ignore_the_engine_keeps():
    judge = SimpleNamespace(start=0, end=11, text="Carlo Verdi", kind="JUDGE", source="judge", action="keep",
                            replacement=None)
    party = SimpleNamespace(start=20, end=31, text="Mario Verdi", kind="PERSON",
                            action="redact", replacement="Nominativo_1")
    assert partial_keeps([judge, party]) == []


# --- PDF input -------------------------------------------------------------------------

def pdf(*page_texts, password=None):
    """Make a small PDF with a text stream on each nonblank test page."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text in page_texts:
        page = writer.add_blank_page(width=300, height=400)
        if text:
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({
                    NameObject("/F1"): DictionaryObject({
                        NameObject("/Type"): NameObject("/Font"),
                        NameObject("/Subtype"): NameObject("/Type1"),
                        NameObject("/BaseFont"): NameObject("/Helvetica"),
                        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
                    }),
                }),
            })
            contents = DecodedStreamObject()
            contents.set_data(b"BT /F1 12 Tf 10 380 Td (" + text.encode("cp1252") + b") Tj ET")
            page.replace_contents(contents)
    if password:
        writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_preserves_page_order_and_feeds_the_review_engine():
    result = json.loads(pdf_text(memoryview(pdf(
        "Il sig. Mario Rossi ricorre.", "", "L’udienza è rinviata."))))
    assert result == {
        "text": "Il sig. Mario Rossi ricorre.\n\nL’udienza è rinviata.",
        "empty_pages": [2],
    }
    reviewed = json.loads(review(result["text"]))
    assert "Nominativo_1" in reviewed["output"]
    assert "Mario Rossi" not in reviewed["output"]


@pytest.mark.parametrize(("make_pdf", "message"), [
    (lambda: pdf("", ""), "non contiene testo estraibile"),
    (lambda: pdf("Testo", password="test"), "PDF è protetto"),
    (lambda: b"%PDF-1.7\n", "Impossibile leggere il PDF"),
], ids=["no text layer", "encrypted", "unreadable"])
def test_pdf_refuses_unusable_documents(make_pdf, message):
    with pytest.raises(Refused, match=message):
        pdf_text(make_pdf())


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
    with pytest.raises(Refused, match="accettare o rifiutare"):
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


def test_formatted_docx_uses_the_reviewed_label_and_occurrence():
    data = docx(p("📄  Il sig. Mario Rossi firma.") + p("Il sig. Rossi conferma."))
    raw = docx_text(data)
    first = json.loads(review(raw, "null", False))
    person, = first["state"]["people"]
    renamed = json.loads(review(raw, json.dumps(first["state"]), False,
                                json.dumps({"type": "redact", "id": person["id"], "tag": "Parte_7"})))
    surname = next(d for d in renamed["decisions"] if raw[d["start"]:d["end"]] == "Rossi")
    kept = json.loads(review(raw, json.dumps(renamed["state"]), False, json.dumps({
        "type": "keep", "start": surname["start"], "end": surname["end"]})))
    spans = [[d["start"], d["end"], d["tag"]] for d in kept["decisions"] if d["action"] == "redact"]
    rendered = docx_text(docx_pseudonymized(data, json.dumps(spans)))
    assert rendered == kept["output"] == "📄  Il sig. Parte_7 firma.\nIl sig. Rossi conferma."


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
