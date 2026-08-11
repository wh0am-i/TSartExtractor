"""Extração tolerante dos dados de NFS-e e eventos XML."""

import re
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree as ET

from tsart_extractor.core import app_logging as logger
from tsart_extractor.core import settings as config
from tsart_extractor.services.api.envelope import _normalize_date_to_iso


def _local(tag: str) -> str:
    """Remove o namespace de uma tag do ElementTree."""
    return tag.split("}")[-1].lower()


def _find_all_by_localname(root: ET.Element, name: str) -> List[ET.Element]:
    name = name.lower()

    return [el for el in root.iter() if _local(el.tag) == name]


def _first_text(root: ET.Element, names: List[str]) -> Optional[str]:
    for name in names:
        found = _find_all_by_localname(root, name)
        if found and found[0].text:
            return found[0].text.strip()
    return None


def _parent_map(root: ET.Element) -> dict:
    """Mapa filho -> pai, para permitir subir na árvore a partir de um elemento."""
    m = {}
    for parent in root.iter():
        for child in parent:
            m[child] = parent
    return m


def _ancestor_localnames(el: ET.Element, parent_map: dict) -> List[str]:
    """Nomes locais (minúsculos) do próprio elemento até a raiz, nessa ordem."""
    names = []
    node = el
    while node is not None:
        names.append(_local(node.tag))
        node = parent_map.get(node)
    return names


# Se um elemento candidato a prestador/tomador tiver, entre seus ancestrais,
# algum nome contendo uma destas pistas, ele NÃO pertence à NFS-e "principal"
# que está sendo processada - e sim a um documento referenciado/embutido
# (nota substituída, evento anexado, etc). Usar dados desses blocos é a causa
# mais provável de uma nota aparecer com prestador/tomador trocados sem
# nenhum padrão visível (só acontece nas notas que têm esse tipo de
# referência embutida).
_BLOCKED_ANCESTOR_HINTS = ("subst", "evento", "cancel")


def _is_blocked(el: ET.Element, parent_map: dict) -> bool:
    ancestors = _ancestor_localnames(el, parent_map)[1:]  # exclui o próprio elemento
    return any(hint in a for a in ancestors for hint in _BLOCKED_ANCESTOR_HINTS)


def _find_prestador_e_tomador(root: ET.Element):
    """
    Localiza os elementos de Prestador e Tomador seguindo a hierarquia oficial
    do padrão nacional da NFS-e (NT do Ambiente de Dados Nacional):

        NFSe/infNFSe/emit                -> Prestador, dado validado pelo
                                             próprio Ambiente Nacional a partir
                                             do certificado que emitiu a nota
                                             (fonte preferencial do CNPJ).
        NFSe/infNFSe/DPS/infDPS/prest    -> Prestador conforme declarado na DPS
                                             (usado como reforço/plano B).
        NFSe/infNFSe/DPS/infDPS/toma     -> Tomador.

    Ao contrário da versão anterior (que varria o XML inteiro e ficava com o
    ÚLTIMO elemento de mesmo nome encontrado, não importa onde), esta versão:
      - pega sempre o PRIMEIRO elemento válido na ordem do documento;
      - ignora qualquer elemento que esteja dentro de um bloco de referência
        (nota substituída, evento, cancelamento etc.), que não representa o
        prestador/tomador da nota que está sendo processada;
      - só aceita um elemento "toma" fora do DPS/infDPS como último recurso,
        caso o layout não seja exatamente o esperado.
    """
    parent_map = _parent_map(root)

    emit_candidates: List[ET.Element] = []
    prest_candidates: List[ET.Element] = []
    toma_candidates_oficiais: List[ET.Element] = []
    toma_candidates_fallback: List[ET.Element] = []

    for el in root.iter():
        tag_l = _local(el.tag)
        if tag_l not in ("emit", "prest", "prestador", "toma", "tomad", "tomador"):
            continue
        if _is_blocked(el, parent_map):
            continue

        ancestors = _ancestor_localnames(el, parent_map)
        dentro_da_dps = "infdps" in ancestors or "dps" in ancestors

        if tag_l == "emit":
            emit_candidates.append(el)
        elif tag_l in ("prest", "prestador"):
            prest_candidates.append(el)
        elif tag_l in ("toma", "tomad", "tomador"):
            if dentro_da_dps:
                toma_candidates_oficiais.append(el)
            else:
                toma_candidates_fallback.append(el)

    emit_el = emit_candidates[0] if emit_candidates else None
    prest_el = prest_candidates[0] if prest_candidates else None
    toma_el = (
        toma_candidates_oficiais[0]
        if toma_candidates_oficiais
        else (toma_candidates_fallback[0] if toma_candidates_fallback else None)
    )
    return emit_el, prest_el, toma_el


def parse_nfse_xml(xml_bytes: bytes) -> Optional[dict]:
    """
    Extrai os campos que aparecem no relatório consolidado a partir do XML
    da NFS-e. Retorna None se não conseguir reconhecer o documento como uma
    NFS-e (por exemplo, se for um Evento - tratado separadamente).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    # Detecção ampliada de Evento (antes só reconhecia a tag exata "evento" ou
    # "eventonfse" - qualquer variação, tipo "PedRegEvento"/"PedidoRegistroEvento",
    # passava batido e acabava sendo processada como se fosse uma NFS-e normal).
    is_evento = any("evento" in _local(el.tag) for el in root.iter())
    if is_evento:
        return None

    chave = _first_text(root, ["chnfse", "chaveacesso", "chave"])
    if not chave:
        # às vezes a chave vem como atributo Id em infNFSe, ex: Id="NFS...50digitos"
        for el in root.iter():
            if _local(el.tag) == "infnfse":
                id_attr = el.attrib.get("Id") or el.attrib.get("id")
                if id_attr:
                    chave = re.sub(r"[^0-9]", "", id_attr)
                break

    # Localiza especificamente as sub-árvores de Emit/Prest (Prestador) e Toma (Tomador)
    emit_el, prest_el, toma_el = _find_prestador_e_tomador(root)

    # Se achar o tomador, busca especificamente dentro dele
    if toma_el is not None:
        tomador_doc = _first_text(toma_el, ["cnpj", "cpf"])
        tomador_nome = _first_text(toma_el, ["xnome", "nomerazao", "razaosocial"]) or "Consumidor Final"
    else:
        tomador_doc = ""
        tomador_nome = "Consumidor Final"

    # CNPJ do prestador: prioriza o "emit" (NFSe/infNFSe/emit), que é o dado
    # validado pelo Ambiente Nacional a partir do certificado que efetivamente
    # emitiu a nota. Usa o "prest" da DPS como plano B.
    # IMPORTANTE: nunca mais cai num fallback de "pegar qualquer CNPJ solto no
    # documento" - essa era a causa da nota vir às vezes com o CNPJ do
    # tomador no lugar do prestador. Se não achar com confiança, fica vazio;
    # o filtro de "só notas emitidas" (em main.py) então descarta a nota em
    # vez de classificá-la errado.
    if emit_el is not None:
        prestador_cnpj = _first_text(emit_el, ["cnpj"])
    elif prest_el is not None:
        prestador_cnpj = _first_text(prest_el, ["cnpj"])
    else:
        prestador_cnpj = ""

    valor_txt = _first_text(root, ["vliq", "vliquido", "vservprest", "vlservico"])
    data_txt = _first_text(root, ["dhemi", "demi", "dataemissao"])

    try:
        valor = float(valor_txt) if valor_txt else 0.0
    except ValueError:
        valor = 0.0

    data_iso = _normalize_date_to_iso(data_txt)

    if not chave:
        return None

    # Rede de segurança: se depois de tudo isso o CNPJ do "prestador" e o
    # documento do "tomador" derem exatamente iguais, é sinal de que esta
    # nota tem uma estrutura fora do que foi previsto acima (algum layout
    # municipal/variação que ainda não apareceu nos testes). Em vez de
    # confiar cegamente no resultado, grava o XML bruto em uma pasta separada
    # para você poder me mandar esse arquivo específico e eu ajustar o
    # parser para esse caso.
    doc_prestador = re.sub(r"\D", "", prestador_cnpj or "")
    doc_tomador = re.sub(r"\D", "", tomador_doc or "")
    if doc_prestador and doc_tomador and doc_prestador == doc_tomador:
        try:
            debug_dir = config.DEBUG_DIR / "xml_suspeitas"
            debug_dir.mkdir(exist_ok=True)
            (debug_dir / f"{chave}.xml").write_bytes(xml_bytes)
            logger.log_error(
                "Nota suspeita (prestador == tomador)",
                f"chave={chave} - XML salvo em {debug_dir / f'{chave}.xml'}",
            )
        except Exception:
            pass

    return {
        "chave": chave,
        "cnpj_prestador": prestador_cnpj or "",
        "tomador_documento": tomador_doc or "",
        "tomador_nome": tomador_nome or "",
        "data_emissao": data_iso or "",
        "valor_liquido": valor,
    }


def parse_evento_cancelamento(xml_bytes: bytes) -> Optional[str]:
    """Se o XML for um Evento de Cancelamento, retorna a chave da NFS-e afetada."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    tipo = _first_text(root, ["tpevento", "tipoevento"]) or ""
    texto_completo = ET.tostring(root, encoding="unicode").lower()
    if "cancel" not in tipo.lower() and "cancel" not in texto_completo:
        return None

    chave = _first_text(root, ["chnfse", "chaveacesso"])
    return chave
