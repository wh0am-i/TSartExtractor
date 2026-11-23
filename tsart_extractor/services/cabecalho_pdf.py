"""Preferências locais do cabeçalho das notas em PDF."""

import base64
import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image, ImageOps

from tsart_extractor.core import app_logging as logger
from tsart_extractor.core import settings as config


LIMITE_TEXTO = 60


@dataclass(frozen=True)
class CabecalhoPDF:
    texto: str = "TSartExtractor"
    logo_png: bytes = b""


@dataclass(frozen=True)
class PreferenciasCabecalhoPDF:
    texto: str = CabecalhoPDF().texto
    caminho_logo: Path | None = None
    logo_legado: bytes = b""

    def preparar(self) -> tuple[CabecalhoPDF, str]:
        if self.caminho_logo is None:
            return CabecalhoPDF(self.texto, self.logo_legado), ""
        try:
            logo = importar_logo(self.caminho_logo)
        except FileNotFoundError:
            erro = "Arquivo da logo não encontrado"
        except (OSError, ValueError, Image.DecompressionBombError):
            erro = "Não foi possível ler o arquivo da logo"
        else:
            return CabecalhoPDF(self.texto, logo), ""
        return CabecalhoPDF(self.texto), f"{erro}.\n{self.caminho_logo}"


def importar_logo(caminho: Path) -> bytes:
    with Image.open(caminho, formats=("PNG", "JPEG")) as imagem:
        imagem = ImageOps.exif_transpose(imagem)
        imagem.thumbnail((720, 480))
        saida = BytesIO()
        imagem.convert("RGBA").save(saida, format="PNG")
    return saida.getvalue()


def carregar_cabecalho() -> PreferenciasCabecalhoPDF:
    caminho = config.APP_DIR / "cabecalho_pdf.json"
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        texto = dados["texto"]
        if not isinstance(texto, str) or len(texto) > LIMITE_TEXTO:
            raise ValueError("Texto do cabeçalho inválido")
        caminho_logo = dados.get("caminho_logo")
        if caminho_logo:
            return PreferenciasCabecalhoPDF(texto, Path(caminho_logo))
    except FileNotFoundError:
        return PreferenciasCabecalhoPDF()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.log_error("Ler cabeçalho do PDF", exc)
        return PreferenciasCabecalhoPDF()

    # Versões anteriores guardavam a imagem, sem registrar seu caminho original.
    logo = b""
    try:
        logo_legado = base64.b64decode(dados.get("logo_png", ""), validate=True)
        if logo_legado:
            with Image.open(BytesIO(logo_legado), formats=("PNG",)) as imagem:
                imagem.load()
            logo = logo_legado
    except (OSError, ValueError, TypeError, Image.DecompressionBombError) as exc:
        logger.log_error("Ler logo salva do PDF", exc)
    return PreferenciasCabecalhoPDF(texto, logo_legado=logo)


def salvar_cabecalho(cabecalho: PreferenciasCabecalhoPDF) -> None:
    dados = {
        "texto": cabecalho.texto,
        "caminho_logo": str(cabecalho.caminho_logo) if cabecalho.caminho_logo else None,
    }
    if cabecalho.logo_legado and cabecalho.caminho_logo is None:
        dados["logo_png"] = base64.b64encode(cabecalho.logo_legado).decode("ascii")
    destino = config.APP_DIR / "cabecalho_pdf.json"
    with NamedTemporaryFile(dir=destino.parent, suffix=".tmp", delete=False) as arquivo:
        temporario = Path(arquivo.name)
    try:
        temporario.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        temporario.replace(destino)
    finally:
        temporario.unlink(missing_ok=True)
