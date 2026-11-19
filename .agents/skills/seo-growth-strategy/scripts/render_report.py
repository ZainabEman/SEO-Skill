"""Render the same validated research data as editable Word and Excel."""
import argparse
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
import subprocess

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from run_state import ROOT, read, write
from validate_report import SHEETS, validate

INK = "142B47"
ACCENT = "007973"
PALE = "EFF6F8"
TABLE_SECTIONS = {4: ["Technical Issues"], 8: ["Direct Competitors", "SERP Competitors"],
                  9: ["Keywords", "Keyword Clusters"], 10: ["Keyword Gaps"], 11: ["Backlinks"],
                  12: ["Content Opportunities"], 19: ["Prioritized Actions"], 23: ["Cost Ledger"]}

def field(paragraph, name):
    element = OxmlElement("w:fldSimple")
    element.set(qn("w:instr"), name)
    paragraph._p.append(element)

def link(paragraph, label, url):
    reference = paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    element = OxmlElement("w:hyperlink")
    element.set(qn("r:id"), reference)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    style = OxmlElement("w:color")
    style.set(qn("w:val"), ACCENT)
    props.append(style)
    run.append(props)
    node = OxmlElement("w:t")
    node.text = label
    run.append(node)
    element.append(run)
    paragraph._p.append(element)

def template(path):
    doc = Document()
    section = doc.sections[0]
    section.page_height, section.page_width = Inches(11.69), Inches(8.27)
    section.top_margin = Inches(0.85)
    section.bottom_margin = Inches(0.8)
    section.left_margin = section.right_margin = Inches(0.82)
    for name, size, color in (("Normal", 10.5, INK), ("Title", 34, INK), ("Heading 1", 19, INK),
                              ("Heading 2", 13, ACCENT), ("Subtitle", 16, ACCENT)):
        font = doc.styles[name].font
        font.name, font.size, font.color.rgb = "Calibri", Pt(size), RGBColor.from_string(color)
    doc.save(path)

def word_table(doc, table, limit=12):
    columns = table["columns"]
    selected = [i for i, name in enumerate(columns) if name not in {"Volumen", "KD", "CPC", "Beleg"}]
    result = doc.add_table(rows=1, cols=len(selected))
    result.style = "Light Shading Accent 1"
    result.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    for i, cell in zip(selected, result.rows[0].cells):
        cell.text = columns[i]
    for row in table["rows"][:limit]:
        target = result.add_row()
        target._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for i, cell in zip(selected, target.cells):
            cell.text = "" if row[i] is None else str(row[i])

def render(report, sources, dest, pdf=False):
    validate(report, sources)
    dest.mkdir(parents=True, exist_ok=True)
    doc = Document(ROOT / "assets/reference.docx")
    section = doc.sections[0]
    section.header.paragraphs[0].add_run(f"{report['client']}  /  SEO").bold = True
    footer = section.footer.paragraphs[0]
    footer.add_run(f"{report['market']}  ·  {report['date']}                                      ")
    field(footer, "PAGE")
    de = report["language"].split("-")[0] == "de"
    doc.add_paragraph("Organisches Wachstum" if de else "Organic growth strategy", "Title")
    doc.add_paragraph(report["client"], "Subtitle")
    link(doc.add_paragraph(), report["website"], report["website"])
    doc.add_paragraph(f"{report['market']}  /  {report['date']}")
    doc.add_page_break()
    sources_table = report["tables"]["Sources"]
    urls = {row[0]: row[1] for row in sources_table["rows"]}
    for number, block in enumerate(report["sections"], 1):
        doc.add_heading(f"{number:02d}  {block['title']}", 1)
        for claim in block["claims"]:
            p = doc.add_paragraph()
            p.add_run(f"[{claim['tag']}] ").bold = True
            p.add_run(claim["text"])
            if claim.get("findings"):
                p.add_run("  " + ", ".join(claim["findings"]))
            for source_id in dict.fromkeys(ref["source"] for ref in claim.get("evidence", [])):
                p.add_run("  ")
                link(p, source_id, urls[source_id])
        for name in TABLE_SECTIONS.get(number, []):
            table = report["tables"][name]
            if table["rows"]:
                doc.add_heading(table.get("title", name), 2)
                word_table(doc, table)
    doc.save(dest / "report.docx")

    workbook = Workbook()
    workbook.remove(workbook.active)
    for name in SHEETS:
        table = report["tables"][name]
        sheet = workbook.create_sheet(name)
        sheet.append(table["columns"])
        for row in table["rows"]:
            sheet.append(row)
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if cell.row == 1:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor=INK)
                elif cell.row % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor=PALE)
                if name == "Sources" and cell.column == 2 and cell.row > 1:
                    cell.hyperlink = cell.value
                    cell.font = Font(color=ACCENT, underline="single")
        sheet.freeze_panes = "B2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False
        for col in sheet.columns:
            extent = max(len(str(cell.value or "")) for cell in col)
            sheet.column_dimensions[get_column_letter(col[0].column)].width = min(63, max(13, extent + 3))
    workbook.save(dest / "data.xlsx")
    reopened = Document(dest / "report.docx")
    excel = load_workbook(dest / "data.xlsx")
    assert sum(p.style.name == "Heading 1" for p in reopened.paragraphs) == 23
    assert excel.sheetnames == SHEETS
    for name in SHEETS:
        assert list(excel[name].values) == [tuple(report["tables"][name]["columns"]),
                                            *map(tuple, report["tables"][name]["rows"])]
    excel.close()
    write(dest / "validation.json", {"docx_reopened": True, "xlsx_reopened": True,
                                      "sections": 23, "sheets": len(SHEETS), "pdf": False})
    if pdf:
        engine = which("libreoffice") or which("soffice")
        if not engine:
            raise RuntimeError("PDF requested but LibreOffice is unavailable. DOCX and XLSX are ready.")
        (dest / "report.pdf").unlink(missing_ok=True)
        with TemporaryDirectory() as profile:
            subprocess.run([engine, "-env:UserInstallation=" + Path(profile).as_uri(),
                            "--headless", "--convert-to", "pdf", "--outdir", str(dest), str(dest / "report.docx")],
                           check=True, timeout=120)
        if not (dest / "report.pdf").read_bytes().startswith(b"%PDF-"):
            raise RuntimeError("Invalid PDF output")
        checks = read(dest / "validation.json")
        checks["pdf"] = True
        write(dest / "validation.json", checks)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pdf", action="store_true")
    args = parser.parse_args()
    render(read(args.run / "report.json"), read(args.run / "sources.json"), args.output, args.pdf)
