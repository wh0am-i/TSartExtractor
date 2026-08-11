"""
Cliente da API oficial do ADN (Ambiente de Dados Nacional) da NFS-e.

Autenticação: mTLS com certificado digital ICP-Brasil (e-CNPJ/e-CPF, A1 em
arquivo .pfx/.p12). Não usa usuário/senha nem captcha - é a mesma API usada
por sistemas de ERP/contábeis para integração.

====================================================================
AJUSTAR AQUI SE NECESSÁRIO
====================================================================
Os NOMES exatos dos campos JSON retornados por GET /contribuintes/DFe/{nsu}
não são publicados fora do Swagger interativo (não consegui acessá-lo do
meu ambiente para confirmar 100%). Por isso o parser abaixo:

  1. Tenta achar o "próximo NSU" testando várias chaves comuns
     (veja _extract_next_nsu).
  2. Acha o conteúdo de cada documento (XML compactado) varrendo o JSON
     em busca de qualquer string que, decodificada de base64 e
     descompactada com gzip, vire um XML válido (veja _find_xml_payloads).

Isso funciona mesmo sem saber o nome exato do campo. Mas se depois de rodar
você perceber que os documentos não estão vindo, abra o Swagger:
    https://www.nfse.gov.br/swagger/contribuintesissqn/#/DFe
faça uma chamada de teste (ele mesmo permite testar autenticado) e veja o
JSON de resposta real. Ajuste então a função `_extract_next_nsu` e
`_find_xml_payloads` para usar o nome de campo certo diretamente - fica
mais rápido e mais confiável que a varredura genérica.
====================================================================
"""
import base64
import gzip
import io
import json
from pathlib import Path
import re
import time
from typing import Iterator, List, Optional, Tuple
from xml.etree import ElementTree as ET

import requests
from requests_pkcs12 import Pkcs12Adapter

from tsart_extractor.core import app_logging as logger
from tsart_extractor.core import settings as config

def extract_cnpj_from_pfx(pfx_path: str, password: str) -> str:
    try:
        from cryptography.hazmat.primitives.serialization import pkcs12
        pfx_data = Path(pfx_path).read_bytes()
        private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(
            pfx_data,
            password.encode('utf-8') if password else None
        )
        if certificate:
            for attr in certificate.subject:
                val = str(attr.value)
                match = re.search(r"\d{14}", val)
                if match:
                    return match.group(0)
    except Exception as e:
        logger.log_error("Erro ao extrair CNPJ do certificado", e)
    return ""


class NFSeAPIError(Exception):
    pass


class NFSeClient:
    def __init__(self, pfx_path: str, pfx_password: str):
        self.session = requests.Session()
        adapter = Pkcs12Adapter(
            pkcs12_filename=pfx_path, pkcs12_password=pfx_password
        )
        self.session.mount(config.ADN_BASE_URL, adapter)
        self._pfx_path = pfx_path
        self._pfx_password = pfx_password
        self.client_cnpj = ""
        try:
            self.client_cnpj = extract_cnpj_from_pfx(pfx_path, pfx_password)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------
    def _get_with_retry(self, url: str, **kwargs) -> requests.Response:
        """
        GET com retry automático em caso de 429 (Too Many Requests) ou 503
        (Service Unavailable). Usa backoff exponencial entre tentativas.
        """
        last_resp = None
        for attempt in range(config.MAX_RETRIES):
            try:
                resp = self.session.get(url, **kwargs)
            except requests.exceptions.RequestException as e:
                if attempt < config.MAX_RETRIES - 1:
                    time.sleep(config.RETRY_BACKOFF * (2 ** attempt))
                    continue
                logger.log_error("Request HTTP API", e)
                raise
            if resp.status_code in (429, 503):
                wait = config.RETRY_BACKOFF * (2 ** attempt)
                last_resp = resp
                if attempt < config.MAX_RETRIES - 1:
                    time.sleep(wait)
                    continue
            return resp
        return last_resp or self.session.get(url, **kwargs)

    # ------------------------------------------------------------------
    # Teste de conexão / validação do certificado
    # ------------------------------------------------------------------
    def test_connection(self) -> Tuple[bool, str]:
        try:
            resp = self._get_with_retry(
                config.DFE_ENDPOINT.format(nsu=0), timeout=config.HTTP_TIMEOUT
            )
            if resp.status_code in (200, 204, 404):
                return True, "Conectado com sucesso."
            if resp.status_code in (401, 403):
                msg = f"Certificado rejeitado (HTTP {resp.status_code}). Verifique se ele está cadastrado no e-CNPJ/e-CPF correto."
                logger.log_error("Conexão - Certificado Rejeitado", msg)
                return False, msg
            msg = f"Resposta inesperada: HTTP {resp.status_code} - {resp.text[:300]}"
            logger.log_error("Conexão - Resposta Inesperada", msg)
            return False, msg
        except requests.exceptions.SSLError as e:
            logger.log_error("Erro de TLS/Certificado na Conexão", e)
            return False, f"Erro de TLS/certificado: {e}"
        except requests.exceptions.RequestException as e:
            logger.log_error("Erro de Rede na Conexão", e)
            return False, f"Erro de rede: {e}"

    # ------------------------------------------------------------------
    # Sincronização em lote via DFe/{NSU}
    # ------------------------------------------------------------------
    def iter_dfe_batches(
        self,
        last_nsu: int = 0,
        cancelar_event=None,
    ) -> Iterator[Tuple[int, List[bytes]]]:
        """
        Gera lotes de documentos (até 50 por chamada, conforme a NT oficial)
        a partir do último NSU informado. Para cada lote, retorna
        (novo_last_nsu, lista_de_xmls_brutos_em_bytes).
        Encerra quando a API não retorna mais nenhum documento novo (HTTP 204 ou 404)
        ou quando cancelar_event é disparado.

        Um delay de config.REQUEST_DELAY_SECONDS é inserido entre lotes
        consecutivos para evitar erros 429 do servidor.
        """
        nsu = last_nsu
        first_call = True
        while True:
            # Verifica cancelável
            if cancelar_event and cancelar_event.is_set():
                break

            # Delay entre requisições (pula na primeira chamada)
            if not first_call:
                time.sleep(config.REQUEST_DELAY_SECONDS)
            first_call = False

            resp = self._get_with_retry(
                config.DFE_ENDPOINT.format(nsu=nsu), timeout=config.HTTP_TIMEOUT
            )

            # Grava log da ÚLTIMA resposta bruta recebida da API (útil para ver
            # o formato exato de uma chamada de "fim de paginação"/erro, mas
            # como o loop sempre termina numa página vazia, este arquivo *por
            # definição* vai sempre mostrar uma resposta vazia - não é bug,
            # é o esperado. Para depurar o conteúdo real dos documentos, veja
            # "ultima_requisicao_com_documentos.json" gravado logo abaixo.
            resp_json = None
            try:
                resp_json = resp.json()
            except Exception:
                resp_json = None
            _write_debug_json(Path(__file__).parent / "ultima_requisicao.json", resp_json, resp.text)

            if resp.status_code in (204, 404):
                logger.log_info("DFe", f"nsu={nsu} -> HTTP {resp.status_code} (fim de paginação)")
                break
            if resp.status_code != 200:
                raise NFSeAPIError(
                    f"Falha ao consultar DFe/{nsu}: HTTP {resp.status_code} - {resp.text[:300]}"
                )
            try:
                payload = resp.json()
            except ValueError:
                raise NFSeAPIError(f"Resposta não é JSON válido em DFe/{nsu}: {resp.text[:300]}")

            xmls = _find_xml_payloads(payload)
            next_nsu = _extract_next_nsu(payload, fallback=nsu + len(xmls))

            # Sinaliza no JSON de erro/status devolvido pelo governo (quando
            # existir), por ex. "NENHUM_DOCUMENTO_LOCALIZADO", mesmo com HTTP 200 -
            # isso é o caso mais comum de "buscou e não achou nada, sem erro".
            status_proc = None
            if isinstance(payload, dict):
                for k, v in payload.items():
                    if isinstance(k, str) and k.lower() in ("statusprocessamento", "status"):
                        status_proc = v
                        break

            logger.log_info(
                "DFe",
                f"nsu={nsu} -> HTTP {resp.status_code} | documentos_encontrados={len(xmls)} "
                f"| proximo_nsu={next_nsu} | status_processamento={status_proc!r}",
            )

            if xmls:
                # Só grava por cima quando o lote realmente traz documentos -
                # este é o arquivo a olhar para depurar o schema real do JSON.
                _write_debug_json(
                    Path(__file__).parent / "ultima_requisicao_com_documentos.json",
                    payload, None,
                )

            if not xmls or next_nsu <= nsu:
                break

            yield next_nsu, xmls
            nsu = next_nsu

    def get_xml(self, chave: str) -> bytes:
        resp = self._get_with_retry(
            config.NFSE_XML_ENDPOINT.format(chave=chave), timeout=config.HTTP_TIMEOUT
        )
        if resp.status_code != 200:
            raise NFSeAPIError(f"Falha ao baixar XML de {chave}: HTTP {resp.status_code}")
        return resp.content

    def get_pdf(self, chave: str) -> bytes:
        resp = self._get_with_retry(
            config.DANFSE_PDF_ENDPOINT.format(chave=chave), timeout=config.HTTP_TIMEOUT
        )
        if resp.status_code != 200:
            raise NFSeAPIError(f"Falha ao baixar PDF (DANFSe) de {chave}: HTTP {resp.status_code}")
        return resp.content


# --------------------------------------------------------------------------
# Parsing defensivo do envelope JSON da rota DFe
# --------------------------------------------------------------------------

_NSU_KEY_CANDIDATES = ("ultnsu", "ultimonsu", "proximonsu", "nsu", "maxnsu")

def _write_debug_json(path: Path, payload, raw_text=None):
    """
    Grava a última resposta da API para depuração.
    """
    try:
        data = payload
        if data is None:
            data = {"raw_response": raw_text}

        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        # Nunca deixa o log quebrar a execução principal
        pass

def _extract_next_nsu(payload, fallback: int) -> int:
    """Procura recursivamente por uma chave que pareça indicar o próximo NSU."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k.lower() in _NSU_KEY_CANDIDATES:
                    try:
                        found.append(int(v))
                    except (TypeError, ValueError):
                        pass
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return max(found) if found else fallback


def _find_xml_payloads(payload) -> List[bytes]:
    """
    Nova versão Otimizada (Extração baseada no schema real do ADN).
    A API retorna os XMLs base64 dentro do campo 'ArquivoXml' do array 'LoteDFe'.
    """
    results = []

    def try_decode(s: str) -> Optional[bytes]:
        if not s or len(s) < 20:
            return None
        try:
            raw = base64.b64decode(s, validate=True)
            # tenta gzip primeiro (padrão usado na distribuição de DF-e)
            try:
                data = gzip.decompress(raw)
            except Exception:
                data = raw  # pode já vir sem compressão
            
            # Validação simples
            if data[:1] in (b"<",) or b"<NFSe" in data[:200] or b"<Evento" in data[:200] or b"<?xml" in data[:200]:
                return data
            return None
        except Exception:
            return None

    # Tenta ler no caminho exato documentado no teste de API
    if isinstance(payload, dict):
        lote = payload.get("LoteDFe", [])
        if isinstance(lote, list):
            for doc in lote:
                # O schema real da API joga a string em "ArquivoXml"
                base64_str = doc.get("ArquivoXml")
                if base64_str:
                    xml_bytes = try_decode(base64_str)
                    if xml_bytes:
                        results.append(xml_bytes)
                else:
                    # Em caso de fallback onde ArquivoXml não exista
                    pass

    return results

# --------------------------------------------------------------------------
# Extração de campos do XML (namespace-agnóstica)
# --------------------------------------------------------------------------

def _local(tag: str) -> str:
    """Remove namespace de uma tag do ElementTree, ex: '{ns}xNome' -> 'xnome'."""
    return tag.rsplit("}", 1)[-1].lower()


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

    data_iso = None
    if data_txt:
        data_iso = data_txt[:10]  # cobre 'YYYY-MM-DD' e 'YYYY-MM-DDThh:mm:ss'

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
            debug_dir = Path(__file__).parent / "xml_suspeitas"
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
