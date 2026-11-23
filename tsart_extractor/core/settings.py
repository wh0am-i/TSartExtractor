"""
Configurações centrais do NFSe Downloader.

IMPORTANTE - LEIA ANTES DE USAR:
Este projeto usa os endpoints oficiais do ADN (Ambiente de Dados Nacional) da
NFS-e Nacional, documentados em:
  https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/apis-prod-restrita-e-producao

Os endpoints (rotas) abaixo são confirmados pela documentação oficial e por
relatos de quem já integrou. O SCHEMA exato dos campos de retorno da rota
DFe (nomes de chaves no JSON) não é público fora do Swagger interativo, então
o parser em api_client.py foi feito de forma "defensiva" (procura o conteúdo
em vez de depender de um nome de campo fixo). Se algo não bater com a resposta
real da API na sua conta, veja o bloco "AJUSTAR AQUI" em api_client.py.
"""

import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = PACKAGE_DIR.parent
ASSETS_DIR = PROJECT_DIR / "assets" / "images"
APP_DIR = Path.home() / ".nfse_downloader"
VAR_DIR = (APP_DIR if getattr(sys, "frozen", False) else PROJECT_DIR) / "var"
DEBUG_DIR = VAR_DIR / "debug"
LOG_DIR = VAR_DIR / "logs"
LOGO_PATH = ASSETS_DIR / "logo.png"

# --------------------------------------------------------------------------
# Ambiente: "producao" (dados fiscais reais) ou "restrita" (homologação/teste)
# Comece testando em "restrita" se quiser validar o fluxo sem certificado real
# de produção. Notas emitidas em restrita não têm validade fiscal.
# --------------------------------------------------------------------------
AMBIENTE = "producao"  # troque para "restrita" para testes

_HOSTS = {
    "producao": "https://adn.nfse.gov.br",
    "restrita": "https://adn.producaorestrita.nfse.gov.br",
}

ADN_BASE_URL = _HOSTS[AMBIENTE]

# Rotas (conforme documentação oficial / swagger contribuintesissqn)
DFE_ENDPOINT = ADN_BASE_URL + "/contribuintes/DFe/{nsu}"
NFSE_XML_ENDPOINT = ADN_BASE_URL + "/nfse/{chave}"
EVENTOS_ENDPOINT = ADN_BASE_URL + "/contribuintes/NFSe/{chave}/Eventos"

# --------------------------------------------------------------------------
# Diretórios locais
# --------------------------------------------------------------------------
DB_PATH = APP_DIR / "notas.db"
XML_DIR = APP_DIR / "xml"
PDF_DIR = APP_DIR / "pdf"
REPORT_DIR = APP_DIR / "relatorios"
# Pasta para .pfx temporários exportados do Windows Certificate Store
CERT_TEMP_DIR = APP_DIR / "certs_temp"

for d in (APP_DIR, XML_DIR, PDF_DIR, REPORT_DIR, CERT_TEMP_DIR, DEBUG_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Pastas onde o app vai procurar certificados .pfx/.p12 automaticamente
CERT_SEARCH_DIRS = [
    Path.home() / ".certificados",
    Path.home() / "Documentos",
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path.home() / "Área de Trabalho",
]
CERT_EXTENSIONS = (".pfx", ".p12")
CERT_SEARCH_MAX_DEPTH = 2

# Timeout de rede (segundos)
HTTP_TIMEOUT = 30

# Tamanho do lote retornado por chamada em /DFe/{nsu} (definido pelo governo, não pelo cliente)
DFE_BATCH_SIZE = 50

# --------------------------------------------------------------------------
# Controle de carga de requisições
# --------------------------------------------------------------------------
# Pausa entre lotes DFe consecutivos (segundos).
# 0.3 s × 6 lotes (300 NFSe) = ~1.8 s de overhead — aceitável.
REQUEST_DELAY_SECONDS = 0.3

# Tentativas automáticas em caso de erro HTTP 429 (Too Many Requests) ou 503
MAX_RETRIES = 3

# Multiplicador de backoff exponencial entre tentativas (segundos)
# Tentativa 1 → 2 s  |  Tentativa 2 → 4 s  |  Tentativa 3 → 8 s
RETRY_BACKOFF = 2.0
