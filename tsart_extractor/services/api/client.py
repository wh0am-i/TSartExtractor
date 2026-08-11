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
import subprocess
import sys
import time
from typing import Iterator, List, Optional, Tuple
from xml.etree import ElementTree as ET

import requests
from requests_pkcs12 import Pkcs12Adapter

from tsart_extractor.core import app_logging as logger
from tsart_extractor.core import settings as config
from tsart_extractor.services.api.envelope import (
    _extract_next_nsu, _find_xml_payloads, _write_debug_json,
)


def _extract_cnpj_from_text(text: str) -> str:
    for match in re.finditer(r"\d{2}\D?\d{3}\D?\d{3}\D?\d{4}\D?\d{2}", text or ""):
        cnpj = re.sub(r"\D", "", match.group(0))
        if len(cnpj) == 14:
            return cnpj
    return ""


def extract_cnpj_from_pfx(pfx_path: str, password: str) -> str:
    try:
        from cryptography.hazmat.primitives.serialization import pkcs12

        pfx_data = Path(pfx_path).read_bytes()

        _, certificate, _ = pkcs12.load_key_and_certificates(
            pfx_data,
            password.encode() if password else None,
        )

        if certificate is None:
            return ""

        for attr in certificate.subject:
            value = str(attr.value)

            # ignora CNPJs da cadeia ICP-Brasil
            if value == "ICP-Brasil":
                continue

            cnpj = _extract_cnpj_from_text(value)
            if cnpj:
                return cnpj

    except Exception as e:
        logger.log_error("Erro ao extrair CNPJ do certificado", e)

    return ""
    

class NFSeAPIError(Exception):
    pass


class _WindowsStoreResponse:
    def __init__(self, url: str, payload: dict):
        self.url = url
        self.status_code = int(payload.get("status_code") or 0)
        self.reason = payload.get("reason") or ""
        self.text = payload.get("text") or ""
        content_b64 = payload.get("content_b64") or ""
        try:
            self.content = base64.b64decode(content_b64) if content_b64 else b""
        except Exception:
            self.content = b""
        self.headers = payload.get("headers") or {}

    def json(self):
        return json.loads(self.text)


class _WindowsStoreCertSession:
    """
    Sessao HTTP para certificado instalado no Windows Store.

    O requests/requests_pkcs12 usa apenas arquivo PKCS#12. Para certificado A3,
    a chave privada fica no token e precisa ser usada pelo SChannel/CAPI do
    Windows. Este backend chama .NET HttpClient via PowerShell e seleciona o
    certificado pelo thumbprint no repositório CurrentUser\\My.
    """

    def __init__(
        self,
        thumbprint: str,
        store_location: str = "CurrentUser",
        store_name: str = "My",
    ):
        if sys.platform != "win32":
            raise NFSeAPIError(
                "Certificados do Windows Store/A3 só podem ser usados no Windows."
            )
        self.thumbprint = re.sub(r"\s+", "", thumbprint or "").upper()
        self.store_location = (
            store_location if store_location in ("CurrentUser", "LocalMachine") else "CurrentUser"
        )
        self.store_name = store_name if store_name in ("My", "WebHosting") else "My"
        if not self.thumbprint:
            raise NFSeAPIError("Thumbprint do certificado não informado.")

    @staticmethod
    def _ps_string(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    def get(self, url: str, **kwargs) -> _WindowsStoreResponse:
        timeout = int(kwargs.get("timeout") or config.HTTP_TIMEOUT)
        script = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$thumbprint = {self._ps_string(self.thumbprint)}
$storeLocation = {self._ps_string(self.store_location)}
$storeName = {self._ps_string(self.store_name)}
$url = {self._ps_string(url)}
$timeoutSec = {timeout}

try {{
    Add-Type -AssemblyName System.Net.Http
    $location = [Enum]::Parse(
        [System.Security.Cryptography.X509Certificates.StoreLocation],
        $storeLocation
    )
    $store = [System.Security.Cryptography.X509Certificates.X509Store]::new(
        $storeName, $location
    )
    $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
    $cert = $store.Certificates.Find(
        [System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
        $thumbprint, $false
    )[0]
    if (-not $cert) {{ throw 'Certificado não localizado no Windows Store.' }}
    $handler = [System.Net.Http.HttpClientHandler]::new()
    [void]$handler.ClientCertificates.Add($cert)
    $handler.SslProtocols = [System.Security.Authentication.SslProtocols]::Tls12

    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds($timeoutSec)

    $resp = $client.GetAsync($url).GetAwaiter().GetResult()
    $bytes = $resp.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
    $headers = @{{}}
    foreach ($h in $resp.Headers) {{ $headers[$h.Key] = ($h.Value -join ', ') }}
    foreach ($h in $resp.Content.Headers) {{ $headers[$h.Key] = ($h.Value -join ', ') }}

    [PSCustomObject]@{{
        ok = $true
        status_code = [int]$resp.StatusCode
        reason = $resp.ReasonPhrase
        text = [System.Text.Encoding]::UTF8.GetString($bytes)
        content_b64 = [Convert]::ToBase64String($bytes)
        headers = $headers
    }} | ConvertTo-Json -Compress -Depth 5
}} catch {{
    [PSCustomObject]@{{
        ok = $false
        error = $_.Exception.Message
    }} | ConvertTo-Json -Compress -Depth 3
    exit 2
}} finally {{
    if ($client) {{ $client.Dispose() }}
    if ($handler) {{ $handler.Dispose() }}
    if ($store) {{ $store.Close() }}
}}
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        try:
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    encoded,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout + 20,
            )
        except subprocess.TimeoutExpired as e:
            raise requests.exceptions.Timeout(
                f"Tempo esgotado ao acessar a API com certificado do Windows Store: {url}"
            ) from e
        except OSError as e:
            raise requests.exceptions.RequestException(
                "PowerShell não encontrado. Certificado A3/Windows Store requer Windows PowerShell."
            ) from e

        raw = (proc.stdout or "").strip()
        try:
            payload = json.loads(raw)
        except ValueError as e:
            raise requests.exceptions.RequestException(
                f"Falha ao interpretar resposta do PowerShell: {raw[:300] or proc.stderr[:300]}"
            ) from e

        if not payload.get("ok"):
            raise requests.exceptions.RequestException(
                payload.get("error") or proc.stderr.strip() or "Falha no transporte Windows Store."
            )

        return _WindowsStoreResponse(url, payload)


class NFSeClient:
    def __init__(
        self,
        pfx_path: str = "",
        pfx_password: str = "",
        thumbprint: str = "",
        store_location: str = "CurrentUser",
        store_name: str = "My",
        client_cnpj: str = "",
        auto_extract_pfx_cnpj: bool = False,
    ):
        self._pfx_path = pfx_path or ""
        self._pfx_password = pfx_password or ""
        self._thumbprint = thumbprint or ""
        self.client_cnpj = re.sub(r"\D", "", client_cnpj or "")

        if self._pfx_path:
            self.session = requests.Session()
            adapter = Pkcs12Adapter(
                pkcs12_filename=self._pfx_path, pkcs12_password=self._pfx_password
            )
            self.session.mount(config.ADN_BASE_URL, adapter)
            if auto_extract_pfx_cnpj:
                try:
                    self.client_cnpj = (
                        self.client_cnpj
                        or extract_cnpj_from_pfx(self._pfx_path, self._pfx_password)
                    )
                except Exception:
                    pass
        elif self._thumbprint:
            self.session = _WindowsStoreCertSession(
                self._thumbprint, store_location, store_name
            )
        else:
            raise NFSeAPIError("Informe um arquivo .pfx/.p12 ou um certificado do Windows Store.")

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
            _write_debug_json(config.DEBUG_DIR / "ultima_requisicao.json", resp_json, resp.text)

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
                    config.DEBUG_DIR / "ultima_requisicao_com_documentos.json",
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

