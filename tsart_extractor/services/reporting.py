"""
Gera o PDF "Relatório Consolidado de Notas Fiscais (Extração XML)" no mesmo
formato do exemplo fornecido: cabeçalho com empresa/CNPJ, tabela com
ID Documento / CPF-CNPJ Tomador / Nome Tomador / Data / Situação / Valor,
e rodapé com Total Líquido (apenas emitidas) e Total de registros.
"""

from datetime import datetime
from pathlib import Path
from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from tsart_extractor.persistence.repository import Nota


def _fmt_moeda(v: float) -> str:
    s = f"{v:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def _fmt_data(data_iso: str) -> str:
    if not data_iso:
        return ""
    try:
        return datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return data_iso


def gerar_relatorio(
    notas: List[Nota],
    empresa_nome: str,
    empresa_cnpj: str,
    output_path: Path,
    tipo_notas: str = "Emitidas",
    periodo_inicio: str = "",
    periodo_fim: str = "",
):
    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle(
        "cell", parent=styles["Normal"], fontSize=7, leading=9
    )
    title_style = ParagraphStyle(
        "title", parent=styles["Normal"], fontSize=13, leading=16, spaceAfter=4
    )
    subtitle_style = ParagraphStyle(
        "subtitle", parent=styles["Normal"], fontSize=9, leading=12
    )

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
    )

    elements = []
    elements.append(Paragraph("Relatório Consolidado de Notas Fiscais (Extração XML)", title_style))
    elements.append(Paragraph(f"Empresa: {empresa_nome} | CNPJ: {empresa_cnpj}", subtitle_style))
    elements.append(Paragraph(f"Tipo de notas: {tipo_notas}", subtitle_style))
    if periodo_inicio or periodo_fim:
        elements.append(
            Paragraph(
                f"Período: {_fmt_data(periodo_inicio) or '...'} a {_fmt_data(periodo_fim) or '...'}",
                subtitle_style,
            )
        )
    elements.append(Spacer(1, 0.4 * cm))

    header = ["ID Documento", "CPF/CNPJ Tomador", "Nome Tomador", "Data", "Situação", "Valor"]
    data = [header]

    total_liquido = 0.0

    for n in notas:
        nome = n.tomador_nome or "Extraído do PDF"
        situacao = n.situacao or "Emitida"
        
        # Apenas notas não canceladas entram na soma do total líquido
        if situacao.lower() == "emitida":
            total_liquido += n.valor_liquido
            
        data.append([
            Paragraph(n.chave, cell_style),
            Paragraph(n.tomador_documento or "", cell_style),
            Paragraph(nome, cell_style),
            Paragraph(_fmt_data(n.data_emissao), cell_style),
            Paragraph(situacao, cell_style),
            Paragraph(_fmt_moeda(n.valor_liquido), cell_style),
        ])

    col_widths = [6.0 * cm, 3.2 * cm, 6.5 * cm, 2.2 * cm, 2.2 * cm, 2.5 * cm]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bdc3c7")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f7")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 0.5 * cm))

    elements.append(Paragraph(f"Total Líquido ({tipo_notas}): R$ {_fmt_moeda(total_liquido)}", subtitle_style))
    elements.append(Paragraph(f"Total de registros: {len(notas)}", subtitle_style))

    doc.build(elements)
    return output_path
