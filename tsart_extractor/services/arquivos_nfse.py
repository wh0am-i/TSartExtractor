"""Nomes e armazenamento dos arquivos locais da NFS-e."""

import re
import tempfile
import unicodedata
from pathlib import Path

from tsart_extractor.core import settings as config
from tsart_extractor.services.api.parser import identificar_nfse


def _nome_seguro(texto: str, limite: int) -> str:
    texto = unicodedata.normalize("NFC", texto)
    texto = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", texto)
    texto = " ".join(texto.split())
    return texto.encode("utf-8")[:limite].decode("utf-8", errors="ignore").strip(" .")


def guardar_xml(chave: str, xml_bytes: bytes) -> Path:
    pasta = _pasta_xml(chave)
    numero, empresa = identificar_nfse(xml_bytes)
    numero = _nome_seguro(numero, 24) or "sem número"
    empresa = _nome_seguro(empresa, 96) or "empresa não informada"
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"NFSe {numero} {empresa}.xml"

    with tempfile.NamedTemporaryFile(dir=pasta, suffix=".part", delete=False) as arquivo:
        temporario = Path(arquivo.name)
    try:
        temporario.write_bytes(xml_bytes)
        temporario.replace(destino)
    finally:
        temporario.unlink(missing_ok=True)
    return destino


def obter_xml(chave: str, caminho_local: str | None, client) -> Path:
    pasta = _pasta_xml(chave)
    if caminho_local:
        path = Path(caminho_local)
        if path.is_file():
            if path.parent == pasta:
                return path
            return guardar_xml(chave, path.read_bytes())

    legado = config.XML_DIR / f"{chave}.xml"
    if legado.is_file():
        return guardar_xml(chave, legado.read_bytes())
    if not client:
        raise RuntimeError("XML não disponível localmente")
    return guardar_xml(chave, client.get_xml(chave))


def _pasta_xml(chave: str) -> Path:
    if not re.fullmatch(r"[0-9A-Za-z_-]{1,80}", chave):
        raise ValueError("Chave de acesso inválida")
    return config.XML_DIR / chave
