"""Geração local do documento auxiliar da NFS-e a partir do XML autorizado."""

from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


GREEN = colors.HexColor("#315C4C")
LIGHT_GREEN = colors.HexColor("#E5ECE8")
BORDER = colors.HexColor("#DDE1DA")
MUTED = colors.HexColor("#66706A")


def _local(tag: str) -> str:
    return tag.split("}")[-1].lower()


def _child(parent: ET.Element | None, *names: str) -> ET.Element | None:
    if parent is None:
        return None
    wanted = {name.lower() for name in names}
    return next((item for item in parent.iter() if _local(item.tag) in wanted), None)


def _text(parent: ET.Element | None, *names: str, default: str = "") -> str:
    item = _child(parent, *names)
    return (item.text or "").strip() if item is not None else default


def _money(value: str) -> str:
    try:
        number = float(value or 0)
    except ValueError:
        number = 0.0
    return f"R$ {number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _date(value: str) -> str:
    raw = (value or "").strip()
    if len(raw) >= 10 and raw[4] == "-":
        date = f"{raw[8:10]}/{raw[5:7]}/{raw[:4]}"
        return f"{date} {raw[11:19]}" if len(raw) >= 19 else date
    return raw


def _document(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    if len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    return value


def _address(person: ET.Element | None) -> str:
    parts = [
        _text(person, "xLgr"),
        _text(person, "nro"),
        _text(person, "xCpl"),
        _text(person, "xBairro"),
        _text(person, "UF"),
        _text(person, "CEP"),
    ]
    return " · ".join(part for part in parts if part)


def _qr(value: str) -> Drawing:
    widget = QrCodeWidget(value)
    x1, y1, x2, y2 = widget.getBounds()
    size = 2.7 * cm
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(widget)
    return drawing


def gerar_danfse(xml_path: Path, output_path: Path) -> Path:
    """Cria um PDF individual sem depender da API de PDF descontinuada."""
    root = ET.fromstring(xml_path.read_bytes())
    info = _child(root, "infNFSe")
    dps = _child(info, "infDPS")
    emit = _child(info, "emit")
    toma = _child(dps, "toma", "tomador")
    serv = _child(dps, "serv")

    id_value = (info.attrib.get("Id") or info.attrib.get("id") or "") if info is not None else ""
    chave = _text(info, "chNFSe", "chaveAcesso", "chave") or "".join(
        ch for ch in id_value if ch.isdigit()
    )
    if not chave:
        raise ValueError("O XML não contém a chave de acesso da NFS-e.")

    prest_doc = _text(emit, "CNPJ", "CPF")
    toma_doc = _text(toma, "CNPJ", "CPF")
    descricao = _text(serv, "xDescServ", default="Serviço não detalhado no XML.")
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("normal-office", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#202522"))
    small = ParagraphStyle("small-office", parent=normal, fontSize=7.5, leading=9, textColor=MUTED)
    label = ParagraphStyle("label-office", parent=small, fontName="Helvetica-Bold", textColor=GREEN)
    title = ParagraphStyle("title-office", parent=normal, fontName="Helvetica-Bold", fontSize=16, leading=19, textColor=GREEN)

    def p(value: str, style=normal):
        return Paragraph(escape(value or "—"), style)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".part")
    doc = SimpleDocTemplate(
        str(temporary_path), pagesize=A4, leftMargin=1.25 * cm, rightMargin=1.25 * cm,
        topMargin=1.15 * cm, bottomMargin=1.15 * cm,
        title=f"DANFSe {chave}", author="TSartExtractor",
    )

    header = Table(
        [[Paragraph("DANFSe v2.0", title), _qr(f"https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave={chave}")],
         [p("Documento Auxiliar da Nota Fiscal de Serviço eletrônica", small), ""]],
        colWidths=[15.7 * cm, 2.7 * cm], rowHeights=[2.15 * cm, 0.55 * cm],
    )
    header.setStyle(TableStyle([
        ("SPAN", (1, 0), (1, 1)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.7, GREEN), ("LINEBEFORE", (1, 0), (1, -1), 0.5, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))

    number = _text(info, "nNFSe", "nDFSe")
    issue = _date(_text(dps, "dhEmi") or _text(info, "dhProc"))
    competence = _date(_text(dps, "dCompet"))
    summary = Table([
        [p("NÚMERO", label), p("EMISSÃO", label), p("COMPETÊNCIA", label)],
        [p(number), p(issue), p(competence)],
    ], colWidths=[4.8 * cm, 7.0 * cm, 6.6 * cm])
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT_GREEN), ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    def party(title_text: str, person: ET.Element | None, document: str):
        content = [
            [p(title_text, label)],
            [p(_text(person, "xNome", default="Nome não informado"))],
            [p(_document(document) + (("  ·  IM " + _text(person, "IM")) if _text(person, "IM") else ""), small)],
            [p(_address(person) or "Endereço não informado", small)],
        ]
        table = Table(content, colWidths=[18.4 * cm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), LIGHT_GREEN), ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return table

    values = _child(info, "valores")
    service_values = _child(dps, "valores")
    value_service = _text(service_values, "vServ") or _text(values, "vLiq", "vServPrest")
    value_net = _text(values, "vLiq") or value_service
    tax = _text(values, "vISSQN")
    value_table = Table([
        [p("VALOR DO SERVIÇO", label), p("ISSQN", label), p("VALOR LÍQUIDO", label)],
        [p(_money(value_service)), p(_money(tax)), p(_money(value_net))],
    ], colWidths=[6.1 * cm, 6.1 * cm, 6.2 * cm])
    value_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT_GREEN), ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER), ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))

    story = [header, Spacer(1, 0.28 * cm), summary, Spacer(1, 0.28 * cm)]
    story += [party("PRESTADOR DO SERVIÇO", emit, prest_doc), Spacer(1, 0.22 * cm)]
    story += [party("TOMADOR DO SERVIÇO", toma, toma_doc), Spacer(1, 0.28 * cm)]
    story += [p("SERVIÇO PRESTADO", label), Spacer(1, 0.08 * cm), p(descricao), Spacer(1, 0.28 * cm)]
    story += [value_table, Spacer(1, 0.35 * cm)]
    story += [p("CHAVE DE ACESSO", label), p(chave, small), Spacer(1, 0.2 * cm)]
    story += [p("Documento auxiliar gerado a partir do XML autorizado. O XML é o documento fiscal de referência.", small)]
    try:
        doc.build(story)
        if temporary_path.stat().st_size < 500 or temporary_path.read_bytes()[:4] != b"%PDF":
            raise ValueError("O PDF gerado não é válido.")
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return output_path
