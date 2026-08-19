"""
Geração local do DANFSe (Documento Auxiliar da NFS-e) a partir do XML
autorizado, seguindo o leiaute "DANFSe v2.0" definido pela Nota Técnica
SE/CGNFS-e nº 008/2026 (versão 1.02, 14/07/2026).

Por que gerar localmente:
A API oficial de geração do DANFSe (https://adn.nfse.gov.br/danfse/docs/index.html)
foi suspensa nacionalmente em 03/08/2026 para todo emissor terceiro (ERPs,
sistemas fiscais, integrações via API). A partir dessa data, a própria NT
008/2026 passou a ser a base para que cada sistema gere o documento por
conta própria a partir do XML da NFS-e (o único documento com validade
fiscal continua sendo o XML — o DANFSe é só auxiliar).

Este módulo NÃO tenta reproduzir posição/pixel a pixel do Anexo I da NT
(coordenadas em cm por campo), mas segue fielmente:
  - a lista de blocos obrigatórios e a ordem definida na seção 2.1;
  - os caminhos XML de onde cada campo vem (seção 2.4.5, coluna "Caminho
    no XML"), usando busca por nome local de tag (sem depender de prefixo
    de namespace);
  - as regras de supressão de blocos vazios (Tomador/Destinatário/
    Intermediário/ISSQN não aplicável — seção 2.3);
  - a linha obrigatória de "Totais Aproximados dos Tributos" (Lei
    12.741/2012) dentro de Informações Complementares (nota 10);
  - o aviso "NFS-e SEM VALIDADE JURÍDICA" quando tpAmb = 2 (homologação);
  - a marca d'água diagonal "CANCELADA" / "SUBSTITUÍDA" (seção 2.5);
  - QR Code apontando para a Consulta Pública oficial com a chave de
    acesso (seção 2.4.3).
  - página única, A4, retrato (seção 2.2).

Se o Comitê Gestor da NFS-e publicar uma nova versão da NT 008, ajuste os
mapeamentos de campo abaixo (a maioria está isolada nas funções
_bloco_*), sem precisar reescrever a geração do PDF inteira.
"""

from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)


GREEN = colors.HexColor("#315C4C")
LIGHT_GREEN = colors.HexColor("#E5ECE8")
BORDER = colors.HexColor("#DDE1DA")
MUTED = colors.HexColor("#66706A")
RED = colors.HexColor("#B3261E")
WATERMARK_GRAY = colors.Color(0, 0, 0, alpha=0.35)  # cinza ~K35, conforme item 2.5.1/2.5.2

CONSULTA_PUBLICA_URL = "https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave="


# --------------------------------------------------------------------------
# Helpers de leitura do XML (namespace-agnóstico)
# --------------------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.split("}")[-1].lower()


def _child(parent: "ET.Element | None", *names: str) -> "ET.Element | None":
    if parent is None:
        return None
    wanted = {name.lower() for name in names}
    return next((item for item in parent.iter() if _local(item.tag) in wanted), None)


def _text(parent: "ET.Element | None", *names: str, default: str = "") -> str:
    item = _child(parent, *names)
    return (item.text or "").strip() if item is not None else default


def _num(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _money(value: str) -> str:
    number = _num(value)
    return f"R$ {number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _percent(value: str) -> str:
    if not value:
        return "-"
    return f"{_num(value):,.2f}%".replace(",", "X").replace(".", ",").replace("X", ".")


def _date(value: str) -> str:
    raw = (value or "").strip()
    if len(raw) >= 10 and raw[4] == "-":
        formatted = f"{raw[8:10]}/{raw[5:7]}/{raw[:4]}"
        return f"{formatted} {raw[11:19]}" if len(raw) >= 19 else formatted
    return raw or "-"


def _document(value: str) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    if len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    return value or "-"


def _address(person: "ET.Element | None") -> str:
    parts = [
        _text(person, "xLgr"), _text(person, "nro"), _text(person, "xCpl"),
        _text(person, "xBairro"),
    ]
    linha = ", ".join(part for part in parts if part)
    return linha or ""


def _municipio_uf(person: "ET.Element | None") -> str:
    cidade = _text(person, "xCidade") or _text(person, "cMun")
    # "UF" cobre endereço nacional (endNac); "xEstProvReg" é o campo
    # equivalente para endereço no exterior (endExt) — sem esse fallback,
    # o estado/província de um tomador estrangeiro ficava em branco.
    uf = _text(person, "UF") or _text(person, "xEstProvReg")
    if cidade and uf:
        return f"{cidade} / {uf}"
    return cidade or uf or ""


def _pais(person: "ET.Element | None") -> str:
    """
    País do endereço da pessoa. Endereço nacional (endNac) não carrega
    campo de país no layout — nesse caso é sempre Brasil. Endereço no
    exterior (endExt) traz o código ISO 3166-1 alfa-2 (ex.: "AR") no
    campo cPais; resolvemos pela tabela _PAISES_ISO (padrão internacional
    fixo) e, só se o código não constar da tabela, mostramos o código
    bruto em vez de adivinhar o nome do país.
    """
    xpais = _text(person, "xPais")  # alguns emissores incluem texto livre; se vier, prevalece
    if xpais:
        return xpais
    cpais = _text(person, "cPais")
    if not cpais:
        return "Brasil"
    return _PAISES_ISO.get(cpais.strip().upper(), f"Código {cpais} (ISO 3166-1)")


def _has_any(parent: "ET.Element | None", *names: str) -> bool:
    return parent is not None and any(_text(parent, n) for n in names)


def _qr(chave: str) -> Drawing:
    """QR Code para a Consulta Pública oficial (seção 2.4.3), min. 1,52cm x 1,52cm."""
    widget = QrCodeWidget(CONSULTA_PUBLICA_URL + chave)
    x1, y1, x2, y2 = widget.getBounds()
    size = 2.4 * cm
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(widget)
    return drawing


# --------------------------------------------------------------------------
# Tabelas de código -> descrição (seção 2.1 / notas da NT)
# --------------------------------------------------------------------------

_TP_EMIT = {"1": "Prestador", "2": "Tomador", "3": "Intermediário"}
_TP_AMB = {"1": "Produção", "2": "Produção Restrita (Homologação)"}
# Domínio oficial do campo opSimpNac (NT SE/CGNFS-e): o valor 3 é
# ME/EPP optante pelo Simples — NÃO é MEI. MEI é o valor 2. O dicionário
# anterior tinha 1 e 2 trocados e chamava o código 3 (ME/EPP) de MEI, o
# que fazia prestadores optantes-mas-não-MEI aparecerem como MEI no PDF.
_OP_SIMPLES = {
    "1": "Não Optante",
    "2": "Optante - Microempreendedor Individual (MEI)",
    "3": "Optante - Microempresa ou Empresa de Pequeno Porte (ME/EPP)",
    "4": "Optante Pendente",  # incluído pela NT 009/2026
}
_TP_RET_ISSQN = {"1": "Não Retido", "2": "Retido pelo Tomador", "3": "Retido pelo Intermediário"}
_TP_RET_PISCOFINS = {
    "1": "PIS/COFINS/CSLL Retido",
    "2": "PIS/COFINS/CSLL Não Retido",
}
_TRIB_ISSQN = {
    "1": "Operação Tributável",
    "2": "Operação Isenta",
    "3": "Operação Imune",
    "4": "Exigibilidade Suspensa",
}

# Tabela de Países ISO 3166-1 alfa-2 (código de 2 letras), usada no campo
# cPais do bloco endExt. É um padrão internacional fixo (não uma tabela
# proprietária do BACEN), então decodificar o código para o nome do país
# é seguro — não é uma suposição sobre dados fiscais, é a definição do
# próprio padrão ISO.
_PAISES_ISO = {
    "AR": "Argentina", "BO": "Bolívia", "BR": "Brasil", "CL": "Chile",
    "CO": "Colômbia", "EC": "Equador", "GY": "Guiana", "PY": "Paraguai",
    "PE": "Peru", "SR": "Suriname", "UY": "Uruguai", "VE": "Venezuela",
    "US": "Estados Unidos", "CA": "Canadá", "MX": "México",
    "PT": "Portugal", "ES": "Espanha", "FR": "França", "DE": "Alemanha",
    "IT": "Itália", "GB": "Reino Unido", "IE": "Irlanda", "NL": "Países Baixos",
    "BE": "Bélgica", "CH": "Suíça", "AT": "Áustria", "SE": "Suécia",
    "NO": "Noruega", "DK": "Dinamarca", "FI": "Finlândia", "PL": "Polônia",
    "RU": "Rússia", "UA": "Ucrânia", "CN": "China", "JP": "Japão",
    "KR": "Coreia do Sul", "IN": "Índia", "AU": "Austrália",
    "NZ": "Nova Zelândia", "ZA": "África do Sul", "AE": "Emirados Árabes Unidos",
    "IL": "Israel", "PA": "Panamá", "CR": "Costa Rica", "CU": "Cuba",
    "DO": "República Dominicana", "JM": "Jamaica", "GT": "Guatemala",
    "HN": "Honduras", "SV": "El Salvador", "NI": "Nicarágua",
    "CY": "Chipre", "LU": "Luxemburgo", "AD": "Andorra", "MC": "Mônaco",
    "GR": "Grécia", "TR": "Turquia", "AO": "Angola", "MZ": "Moçambique",
    "CV": "Cabo Verde", "GW": "Guiné-Bissau", "ST": "São Tomé e Príncipe",
    "TL": "Timor-Leste", "SG": "Singapura", "HK": "Hong Kong",
    "TW": "Taiwan", "MO": "Macau",
}


def _cod_desc(tabela: dict, codigo: str, bruto_se_ausente: bool = True) -> str:
    if not codigo:
        return "-"
    return tabela.get(codigo, codigo if bruto_se_ausente else "-")


# --------------------------------------------------------------------------
# Geração do PDF
# --------------------------------------------------------------------------

def gerar_danfse(
    xml_path: Path,
    output_path: Path,
    cancelada: bool = False,
    substituida: bool = False,
) -> Path:
    """
    Gera o DANFSe em PDF a partir do XML autorizado da NFS-e, seguindo o
    leiaute DANFSe v2.0 (NT 008/2026 v1.02) — sem depender da API de PDF
    do governo, descontinuada em 03/08/2026.

    cancelada / substituida: controla a marca d'água diagonal exigida
    pela seção 2.5 quando a nota estiver nesses estados.
    """
    root = ET.fromstring(xml_path.read_bytes())
    info = _child(root, "infNFSe")
    dps = _child(info, "infDPS")
    prest = _child(dps, "prest")
    toma = _child(dps, "toma", "tomador")
    dest = _child(dps, "dest")  # Destinatário (bloco IBSCBS/dest no leiaute)
    interm = _child(dps, "interm")
    serv = _child(dps, "serv")
    valores = _child(dps, "valores")
    ibscbs = _child(info, "IBSCBS")
    ibscbs_valores = _child(ibscbs, "valores") if ibscbs is not None else None
    ibscbs_trib = _child(ibscbs_valores, "trib") if ibscbs_valores is not None else None
    gibscbs = _child(ibscbs_trib, "gIBSCBS") if ibscbs_trib is not None else None
    totcibs = _child(ibscbs, "totCIBS") if ibscbs is not None else None

    id_value = (info.attrib.get("Id") or info.attrib.get("id") or "") if info is not None else ""
    chave = _text(info, "chNFSe", "chaveAcesso", "id") or "".join(ch for ch in id_value if ch.isdigit())
    if not chave:
        raise ValueError("O XML não contém a chave de acesso da NFS-e (50 dígitos).")

    tp_amb = _text(dps, "tpAmb")
    homologacao = tp_amb == "2"

    styles = getSampleStyleSheet()
    txt = ParagraphStyle("txt", parent=styles["Normal"], fontName="Helvetica", fontSize=7, leading=9, textColor=colors.black)
    txt_small = ParagraphStyle("txt-small", parent=txt, fontSize=6, leading=8, textColor=MUTED)
    lbl = ParagraphStyle("lbl", parent=txt, fontName="Helvetica-Bold", fontSize=6, leading=8, textColor=GREEN)
    lbl_block = ParagraphStyle("lbl-block", parent=lbl, fontSize=7)
    title_style = ParagraphStyle("title", parent=txt, fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=GREEN, alignment=1)
    subtitle_style = ParagraphStyle("subtitle", parent=txt, fontSize=8, leading=10, alignment=1)
    homolog_style = ParagraphStyle("homolog", parent=txt, fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=RED, alignment=1)

    def p(value: str, style=txt):
        return Paragraph(escape(str(value) if value not in (None, "") else "-"), style)

    def campo(label_text: str, value: str, style=txt):
        """Célula com título (label) em cima e valor embaixo, como manda 2.4.2/2.4.4."""
        return Table(
            [[Paragraph(escape(label_text), lbl)], [p(value, style)]],
            colWidths=[None],
        )

    FULL_WIDTH = 18.9 * cm

    def block_title(text: str) -> Table:
        t = Table([[Paragraph(escape(text), lbl_block)]], colWidths=[FULL_WIDTH])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREEN),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        return t

    def field_row(cols):
        """cols: lista de (largura_cm|None, label, valor). None = distribui igual."""
        n = len(cols)
        widths = [c[0] * cm if c[0] else FULL_WIDTH / n for c in cols]
        cells = [campo(c[1], c[2]) for c in cols]
        t = Table([cells], colWidths=widths)
        t.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER), ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))
        return t

    story = []

    # ---------------------------------------------------------------
    # Cabeçalho (2.4.3): logo | "DANFSe v2.0" + título | município/ambiente + QR
    # ---------------------------------------------------------------
    municipio_emit = _text(info, "xLocEmi") or _municipio_uf(prest)
    ambiente_ger = _text(info, "ambGer") or "Sistema Nacional NFS-e"
    header_center = [Paragraph("DANFSe v2.0", title_style),
                      Paragraph("Documento Auxiliar da NFS-e", subtitle_style)]
    if homologacao:
        header_center.append(Paragraph("NFS-e SEM VALIDADE JURÍDICA", homolog_style))

    header_right = Table(
        [[p(f"Município: {municipio_emit}" if municipio_emit else "-", txt_small)],
         [p(f"Ambiente gerador: {ambiente_ger}", txt_small)],
         [p(f"Tipo de ambiente: {_cod_desc(_TP_AMB, tp_amb)}", txt_small)]],
        colWidths=[5.0 * cm],
    )

    header = Table(
        [["TSartExtractor", header_center, header_right, _qr(chave)]],
        colWidths=[3.2 * cm, 8.2 * cm, 5.0 * cm, 2.5 * cm],
        rowHeights=[2.0 * cm],
    )
    header.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.7, GREEN), ("LINEBEFORE", (1, 0), (1, -1), 0.4, BORDER),
        ("LINEBEFORE", (2, 0), (2, -1), 0.4, BORDER), ("LINEBEFORE", (3, 0), (3, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (0, 0), "CENTER"),
        ("FONTNAME", (0, 0), (0, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (0, 0), 8),
        ("TEXTCOLOR", (0, 0), (0, 0), GREEN),
    ]))
    story.append(header)
    story.append(Paragraph(
        "A autenticidade desta NFS-e pode ser verificada pela leitura deste código QR "
        "ou pela consulta da chave de acesso no portal nacional da NFS-e.", txt_small))
    story.append(Spacer(1, 0.15 * cm))

    # ---------------------------------------------------------------
    # Dados de Identificação da NFS-e (2.1.2)
    # ---------------------------------------------------------------
    story.append(block_title("DADOS DA NFS-e"))
    story.append(field_row([(None, "CHAVE DE ACESSO DA NFS-e", chave)]))
    story.append(field_row([
        (6.3, "NÚMERO DA NFS-e", _text(info, "nNFSe")),
        (6.3, "COMPETÊNCIA DA NFS-e", _date(_text(dps, "dCompet"))[:10]),
        (6.3, "DATA E HORA DA EMISSÃO DA NFS-e", _date(_text(info, "dhProc"))),
    ]))
    story.append(field_row([
        (4.7, "NÚMERO DA DPS", _text(dps, "nDPS")),
        (4.7, "SÉRIE DA DPS", _text(dps, "serie")),
        (4.7, "DATA E HORA DA EMISSÃO DA DPS", _date(_text(dps, "dhEmi"))),
        (4.7, "EMITENTE DA NFS-e", _cod_desc(_TP_EMIT, _text(dps, "tpEmit"))),
    ]))
    story.append(field_row([
        (6.3, "SITUAÇÃO DA NFS-e", _text(info, "cStat") or "Normal"),
        (6.3, "FINALIDADE", _text(dps, "finNFSe") or "NFS-e regular"),
        (6.3, "", ""),
    ]))
    story.append(Spacer(1, 0.12 * cm))

    # ---------------------------------------------------------------
    # Prestador / Fornecedor (2.1.3)
    # ---------------------------------------------------------------
    def bloco_pessoa(titulo: str, pessoa, ausente_texto: str, mostrar_im: bool = True):
        if pessoa is None or not (_text(pessoa, "CNPJ") or _text(pessoa, "CPF") or _text(pessoa, "NIF") or _text(pessoa, "xNome")):
            story.append(block_title(titulo))
            story.append(field_row([(None, "", ausente_texto)]))
            story.append(Spacer(1, 0.12 * cm))
            return
        doc = _text(pessoa, "CNPJ") or _text(pessoa, "CPF") or _text(pessoa, "NIF")
        story.append(block_title(titulo))
        row1 = [(6.3, "CNPJ / CPF / NIF", _document(doc))]
        if mostrar_im:
            row1.append((6.3, "INDICADOR MUNICIPAL (INSCRIÇÃO)", _text(pessoa, "IM") or "-"))
        row1.append((6.3, "TELEFONE", _text(pessoa, "fone") or "-"))
        story.append(field_row(row1))
        story.append(field_row([
            (6.3, "NOME / NOME EMPRESARIAL", _text(pessoa, "xNome")),
            (6.3, "MUNICÍPIO / SIGLA UF", _municipio_uf(pessoa) or "-"),
            (6.3, "PAÍS", _pais(pessoa)),
        ]))
        story.append(field_row([
            (9.4, "ENDEREÇO", _address(pessoa) or "-"),
            (9.4, "E-MAIL", _text(pessoa, "email") or "-"),
        ]))
        story.append(Spacer(1, 0.12 * cm))

    bloco_pessoa("PRESTADOR / FORNECEDOR", prest, "PRESTADOR NÃO IDENTIFICADO NA NFS-e")
    story.append(field_row([
        (9.4, "SIMPLES NACIONAL NA DATA DE COMPETÊNCIA",
         _cod_desc(_OP_SIMPLES, _text(_child(prest, "regTrib"), "opSimpNac"))),
        (9.4, "REGIME DE APURAÇÃO TRIBUTÁRIA PELO SN",
         _text(_child(prest, "regTrib"), "regApTribSN") or "-"),
    ]))
    story.append(Spacer(1, 0.12 * cm))

    # ---------------------------------------------------------------
    # Tomador/Adquirente (2.1.4) — supressível (nota 2)
    # ---------------------------------------------------------------
    bloco_pessoa("TOMADOR / ADQUIRENTE", toma, "TOMADOR/ADQUIRENTE DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e")

    # ---------------------------------------------------------------
    # Destinatário da Operação (2.1.5) — supressível (nota 2 e 3)
    # ---------------------------------------------------------------
    toma_doc = _text(toma, "CNPJ") or _text(toma, "CPF") or _text(toma, "NIF")
    dest_doc = _text(dest, "CNPJ") or _text(dest, "CPF") or _text(dest, "NIF") if dest is not None else ""
    if dest is not None and dest_doc and dest_doc == toma_doc:
        story.append(block_title("DESTINATÁRIO DA OPERAÇÃO"))
        story.append(field_row([(None, "", "O DESTINATÁRIO É O PRÓPRIO TOMADOR/ADQUIRENTE DA OPERAÇÃO")]))
        story.append(Spacer(1, 0.12 * cm))
    else:
        bloco_pessoa("DESTINATÁRIO DA OPERAÇÃO", dest, "DESTINATÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e", mostrar_im=False)

    # ---------------------------------------------------------------
    # Intermediário da Operação (2.1.6) — supressível (nota 2)
    # ---------------------------------------------------------------
    bloco_pessoa("INTERMEDIÁRIO DA OPERAÇÃO", interm, "INTERMEDIÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e")

    # ---------------------------------------------------------------
    # Serviço Prestado (2.1.7)
    # ---------------------------------------------------------------
    cserv = _child(serv, "cServ")
    story.append(block_title("SERVIÇO PRESTADO"))
    trib_nac = _text(cserv, "cTribNac")
    trib_mun = _text(cserv, "cTribMun")
    story.append(field_row([
        (6.3, "CÓDIGO DE TRIBUTAÇÃO NACIONAL / MUNICIPAL", f"{trib_nac} / {trib_mun}" if trib_mun else (trib_nac or "-")),
        (6.3, "CÓDIGO DA NBS", _text(cserv, "cNBS") or "-"),
        (6.3, "LOCAL DA PRESTAÇÃO / UF / PAÍS", _text(info, "xLocPrestacao") or _municipio_uf(prest) or "-"),
    ]))
    descricao_trib = _text(cserv, "xTribMun") or _text(cserv, "xTribNac")
    if descricao_trib:
        story.append(field_row([(None, "", descricao_trib)]))
    story.append(field_row([(None, "DESCRIÇÃO DO SERVIÇO", _text(cserv, "xDescServ", default="Serviço não detalhado no XML."))]))
    story.append(Spacer(1, 0.12 * cm))

    # ---------------------------------------------------------------
    # Tributação Municipal / ISSQN (2.1.8) — supressível (nota 4/5)
    # ---------------------------------------------------------------
    trib_mun_el = _child(valores, "tribMun") if valores is not None else None
    tribISSQN_cod = _text(trib_mun_el, "tribISSQN") if trib_mun_el is not None else ""
    if not tribISSQN_cod and not _has_any(valores, "vISSQN", "vBC"):
        story.append(block_title("TRIBUTAÇÃO MUNICIPAL (ISSQN)"))
        story.append(field_row([(None, "", "TRIBUTAÇÃO MUNICIPAL (ISSQN) - OPERAÇÃO NÃO SUJEITA AO ISSQN")]))
        story.append(Spacer(1, 0.12 * cm))
    else:
        story.append(block_title("TRIBUTAÇÃO MUNICIPAL (ISSQN)"))
        story.append(field_row([
            (9.4, "TIPO DE TRIBUTAÇÃO DO ISSQN", _cod_desc(_TRIB_ISSQN, tribISSQN_cod)),
            (9.4, "MUNICÍPIO / UF / PAÍS DE INCIDÊNCIA", _text(trib_mun_el, "xLocIncid") or _municipio_uf(prest) or "-"),
        ]))
        story.append(field_row([
            (6.3, "BC ISSQN", _money(_text(valores, "vBC"))),
            (6.3, "ALÍQUOTA APLICADA", _percent(_text(valores, "pAliqAplic"))),
            (6.3, "RETENÇÃO DO ISSQN", _cod_desc(_TP_RET_ISSQN, _text(trib_mun_el, "tpRetISSQN"))),
        ]))
        story.append(field_row([(None, "ISSQN APURADO", _money(_text(valores, "vISSQN")))]))
        story.append(Spacer(1, 0.12 * cm))

    # ---------------------------------------------------------------
    # Tributação Federal exceto CBS (2.1.9) — impressa até competência 2026 (nota 6)
    # ---------------------------------------------------------------
    trib_fed_el = _child(valores, "tribFed") if valores is not None else None
    piscofins_el = _child(trib_fed_el, "piscofins") if trib_fed_el is not None else None
    tp_ret_pc = _text(piscofins_el, "tpRetPisCofins")
    ret_pc_retido = tp_ret_pc == "1"
    v_pis = "0.00" if ret_pc_retido else _text(piscofins_el, "vPis")
    v_cofins = "0.00" if ret_pc_retido else _text(piscofins_el, "vCofins")
    v_csll = _text(trib_fed_el, "vRetCSLL")
    contrib_sociais = str(_num(v_csll) + (_num(_text(piscofins_el, "vPis")) + _num(_text(piscofins_el, "vCofins")) if ret_pc_retido else 0))

    if _has_any(trib_fed_el, "vRetIRRF", "vRetCP", "vRetCSLL") or _has_any(piscofins_el, "vPis", "vCofins"):
        story.append(block_title("TRIBUTAÇÃO FEDERAL (EXCETO CBS)"))
        story.append(field_row([
            (4.7, "IRRF", _money(_text(trib_fed_el, "vRetIRRF"))),
            (4.7, "CONTRIBUIÇÃO PREVIDENCIÁRIA - RETIDA", _money(_text(trib_fed_el, "vRetCP"))),
            (4.7, "CONTRIBUIÇÕES SOCIAIS - RETIDAS", _money(contrib_sociais)),
            (4.7, "DESCRIÇÃO CONTRIB. SOCIAIS - RETIDAS", _cod_desc(_TP_RET_PISCOFINS, tp_ret_pc)),
        ]))
        story.append(field_row([
            (9.4, "PIS - DÉBITO APURAÇÃO PRÓPRIA", _money(v_pis)),
            (9.4, "COFINS - DÉBITO APURAÇÃO PRÓPRIA", _money(v_cofins)),
        ]))
        story.append(Spacer(1, 0.12 * cm))

    # ---------------------------------------------------------------
    # Tributação IBS / CBS (2.1.10) — bloco novo da Reforma Tributária
    # ---------------------------------------------------------------
    ibscbs_uf = _child(ibscbs_valores, "uf") if ibscbs_valores is not None else None
    ibscbs_mun = _child(ibscbs_valores, "mun") if ibscbs_valores is not None else None
    ibscbs_fed = _child(ibscbs_valores, "fed") if ibscbs_valores is not None else None
    gibs = _child(totcibs, "gIBS") if totcibs is not None else None
    gcbs = _child(totcibs, "gCBS") if totcibs is not None else None
    gibs_mun_tot = _child(gibs, "gIBSMunTot") if gibs is not None else None
    gibs_uf_tot = _child(gibs, "gIBSUFTot") if gibs is not None else None

    story.append(block_title("TRIBUTAÇÃO IBS / CBS"))
    story.append(field_row([
        (6.3, "CST / cClassTrib", f"{_text(gibscbs, 'CST') or '-'} / {_text(gibscbs, 'cClassTrib') or '-'}"),
        (6.3, "INDICADOR DE OPERAÇÃO / MUNICÍPIO DE INCIDÊNCIA / UF",
         f"{_text(ibscbs, 'cIndOp') or '-'} / {_text(ibscbs, 'xLocalidadeIncid') or '-'}"),
        (6.3, "BASE DE CÁLCULO APÓS EXCLUSÕES E REDUÇÕES", _money(_text(ibscbs_valores, "vBC"))),
    ]))
    story.append(field_row([
        (3.7, "RED. ALÍQ. IBS UF", _percent(_text(ibscbs_uf, "pRedAliq"))),
        (3.7, "RED. ALÍQ. IBS MUN", _percent(_text(ibscbs_mun, "pRedAliq"))),
        (3.7, "RED. ALÍQ. CBS", _percent(_text(ibscbs_fed, "pRedAliq"))),
        (3.7, "ALÍQ. IBS UF / MUN", f"{_percent(_text(ibscbs_uf, 'pIBSUF') or _text(ibscbs_uf, 'p'))} / {_percent(_text(ibscbs_mun, 'pIBSMun') or _text(ibscbs_mun, 'p'))}"),
        (3.7, "ALÍQUOTA CBS", _percent(_text(ibscbs_fed, "pCBS") or _text(ibscbs_fed, "p"))),
    ]))
    story.append(field_row([
        (4.7, "ALÍQ. EFETIVA MUNICIPAL - IBS", _percent(_text(ibscbs_mun, "pAliqEfet"))),
        (4.7, "VALOR APURADO MUNICIPAL - IBS", _money(_text(gibs_mun_tot, "vIBSMun") if gibs_mun_tot is not None else _text(ibscbs_mun, "vIBS"))),
        (4.7, "ALÍQ. EFETIVA ESTADUAL - IBS", _percent(_text(ibscbs_uf, "pAliqEfet"))),
        (4.7, "VALOR APURADO ESTADUAL - IBS", _money(_text(gibs_uf_tot, "vIBSUF") if gibs_uf_tot is not None else _text(ibscbs_uf, "vIBS"))),
    ]))
    story.append(field_row([
        (6.3, "VALOR TOTAL APURADO - IBS", _money(_text(gibs, "vIBSTot") if gibs is not None else "0")),
        (6.3, "ALÍQUOTA EFETIVA - CBS", _percent(_text(ibscbs_fed, "pAliqEfet"))),
        (6.3, "VALOR TOTAL APURADO - CBS", _money(_text(gcbs, "vCBS") if gcbs is not None else "0")),
    ]))
    story.append(Spacer(1, 0.12 * cm))

    # ---------------------------------------------------------------
    # Valor Total da NFS-e (2.1.11)
    # ---------------------------------------------------------------
    v_serv = _text(valores, "vServPrest") or _text(_child(valores, "vServPrest"), "vServ")
    v_desc_incond = _text(_child(valores, "vDescCondIncond"), "vDescIncond") or _text(valores, "vDescIncond")
    v_desc_cond = _text(_child(valores, "vDescCondIncond"), "vDescCond") or _text(valores, "vDescCond")
    v_tot_ret = _text(valores, "vTotalRet")
    v_liq = _text(valores, "vLiq")
    v_ibs_tot = _text(gibs, "vIBSTot") if gibs is not None else "0"
    v_cbs_tot = _text(gcbs, "vCBS") if gcbs is not None else "0"
    v_tot_ibscbs = str(_num(v_ibs_tot) + _num(v_cbs_tot))
    v_tot_nf = _text(totcibs, "vTotNF") if totcibs is not None else str(_num(v_liq) + _num(v_tot_ibscbs))

    story.append(block_title("VALOR TOTAL DA NFS-e"))
    story.append(field_row([
        (3.7, "VALOR DA OPERAÇÃO / SERVIÇO", _money(v_serv)),
        (3.7, "DESCONTO INCONDICIONADO", _money(v_desc_incond)),
        (3.7, "DESCONTO CONDICIONADO", _money(v_desc_cond)),
        (3.7, "TOTAL DAS RETENÇÕES (ISSQN/FEDERAIS)", _money(v_tot_ret)),
    ]))
    total_row = Table(
        [[campo("VALOR LÍQUIDO DA NFS-e", _money(v_liq)),
          campo("TOTAL DO IBS/CBS", _money(v_tot_ibscbs)),
          campo("VALOR LÍQUIDO DA NFS-e + IBS/CBS", _money(v_tot_nf))]],
        colWidths=[6.3 * cm, 6.3 * cm, 6.3 * cm],
    )
    total_row.setStyle(TableStyle([
        ("BACKGROUND", (2, 0), (2, 0), LIGHT_GREEN),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER), ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(total_row)
    story.append(Spacer(1, 0.12 * cm))

    # ---------------------------------------------------------------
    # Informações Complementares (2.1.12) — inclui Totais Aproximados (nota 10, obrigatório)
    # ---------------------------------------------------------------
    partes_info = []
    inf_compl = _text(cserv, "xInfComp") or _text(_child(serv, "infoCompl"), "xInfComp")
    if inf_compl:
        partes_info.append(f"Inf. Cont.: {inf_compl}")
    ch_substda = _text(dps, "chSubstda")
    if ch_substda:
        partes_info.append(f"NFS-e Subst.: {ch_substda}")
    cobra = _text(serv, "cObra")
    insc_imob = _text(serv, "inscImobFisc")
    if cobra or insc_imob:
        if cobra:
            partes_info.append(f"Cod. Obra: {cobra}")
        if insc_imob:
            partes_info.append(f"Insc. Imob.: {insc_imob}")
    id_atv_evt = _text(serv, "idAtvEvt")
    if id_atv_evt:
        partes_info.append(f"Cod. Evt.: {id_atv_evt}")

    # Totais Aproximados dos Tributos (Lei 12.741/2012) — obrigatório mesmo que zerado
    tot_trib = _child(valores, "totTrib") if valores is not None else None
    v_tot_trib = _child(tot_trib, "vTotTrib") if tot_trib is not None else None
    if v_tot_trib is not None and (_has_any(v_tot_trib, "vTotTribFed", "vTotTribEst", "vTotTribMun")):
        totais_txt = (
            f"Totais Aproximados dos Tributos cfe. Lei nº 12.741/2012: "
            f"Federais: {_money(_text(v_tot_trib, 'vTotTribFed'))}; "
            f"Estaduais: {_money(_text(v_tot_trib, 'vTotTribEst'))}; "
            f"Municipais: {_money(_text(v_tot_trib, 'vTotTribMun'))}"
        )
    else:
        totais_txt = (
            "Totais Aproximados dos Tributos cfe. Lei nº 12.741/2012: "
            "Federais: R$ 0,00; Estaduais: R$ 0,00; Municipais: R$ 0,00 "
            "(fonte não informada no XML)"
        )
    partes_info.append(totais_txt)

    story.append(block_title("INFORMAÇÕES COMPLEMENTARES"))
    story.append(field_row([(None, "", " | ".join(partes_info))]))
    story.append(Spacer(1, 0.15 * cm))

    story.append(Paragraph(
        "Documento auxiliar gerado localmente a partir do XML autorizado, conforme leiaute "
        "DANFSe v2.0 da Nota Técnica SE/CGNFS-e nº 008/2026 (v1.02). O XML é o documento fiscal "
        "de referência; em caso de divergência, prevalece o XML.", txt_small))

    # ---------------------------------------------------------------
    # Construção do PDF + marca d'água (2.5.1 / 2.5.2)
    # ---------------------------------------------------------------
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".part")

    def _watermark(cnv: "pdfcanvas.Canvas", _doc):
        texto = "CANCELADA" if cancelada else ("SUBSTITUÍDA" if substituida else None)
        if not texto:
            return
        cnv.saveState()
        cnv.translate(A4[0] / 2, A4[1] / 2)
        cnv.rotate(45)
        cnv.setFont("Helvetica-Bold", 60)
        cnv.setFillColor(WATERMARK_GRAY)
        cnv.drawCentredString(0, 0, texto)
        cnv.restoreState()

    frame = Frame(1.05 * cm, 1.05 * cm, 18.9 * cm, 27.9 * cm, id="corpo",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc = BaseDocTemplate(
        str(temporary_path), pagesize=A4,
        title=f"DANFSe {chave}", author="TSartExtractor",
    )
    doc.addPageTemplates([PageTemplate(id="pagina", frames=[frame], onPage=_watermark)])

    try:
        doc.build(story)
        if temporary_path.stat().st_size < 500 or temporary_path.read_bytes()[:4] != b"%PDF":
            raise ValueError("O PDF gerado não é válido.")
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return output_path