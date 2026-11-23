import base64
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from reportlab import rl_config

from tsart_extractor.core import settings as config
from tsart_extractor.persistence.repository import Nota
from tsart_extractor.services.cabecalho_pdf import (
    PreferenciasCabecalhoPDF, carregar_cabecalho, importar_logo, salvar_cabecalho,
)
from tsart_extractor.services.danfse import gerar_danfse
from tsart_extractor.ui.exports import ExportMixin


class ExportacaoNFSeTest(unittest.TestCase):
    def test_lote_usa_logo_do_caminho_salvo_e_mantem_texto_se_arquivo_sumir(self):
        with TemporaryDirectory() as pasta:
            destino = Path(pasta)
            logo_path = destino / "logo.png"
            with Image.new("RGBA", (300, 100), (0, 120, 90, 150)) as logo:
                logo.save(logo_path)
            preferencias = PreferenciasCabecalhoPDF("Acme & Filhos <Centro>", logo_path)

            chave = "1" * 50
            legado = destino / f"{chave}.xml"
            legado.write_text(f'<NFSe><infNFSe Id="NFS{chave}"><nNFSe>123</nNFSe></infNFSe></NFSe>')
            pdf_padrao = gerar_danfse(legado, destino / "padrao.pdf").read_bytes()
            arquivo_zip = destino / "lote_pdf.zip"
            nota = Nota(chave, xml_local_path=str(legado))
            with (
                patch.object(config, "APP_DIR", destino),
                patch.object(config, "XML_DIR", destino / "xml"),
                patch.object(config, "PDF_DIR", destino / "pdf"),
                patch.object(rl_config, "pageCompression", 0),
                patch("tsart_extractor.ui.exports.storage.set_paths"),
            ):
                self.assertEqual(carregar_cabecalho(), PreferenciasCabecalhoPDF())
                salvar_cabecalho(preferencias)
                self.assertEqual(carregar_cabecalho(), preferencias)
                for tem_logo in (True, False):
                    with self.subTest(tem_logo=tem_logo):
                        cabecalho, aviso = carregar_cabecalho().preparar()
                        if tem_logo:
                            self.assertEqual(aviso, "")
                            logo_path.unlink()
                        else:
                            self.assertIn("Arquivo da logo não encontrado", aviso)
                            self.assertIn(str(logo_path), aviso)
                        resultado = ExportMixin()._gerar_zip_lote(
                            tipo="pdf", destino=arquivo_zip, notas_snapshot=[nota], client=None,
                            cancel_event=None, progresso=lambda *_: None, cabecalho=cabecalho,
                        )
                        self.assertEqual(resultado, (1, 0, None, False))

                        with zipfile.ZipFile(arquivo_zip) as lote:
                            pdf = lote.read("NFSe 123 empresa não informada.pdf")
                        for trecho in (b"Acme & Filhos", b"(Centro)", b"DANFSe v2.0"):
                            self.assertTrue(trecho in pdf, trecho)
                        self.assertEqual(b"/Subtype /Image" in pdf, tem_logo)
                        self.assertEqual(pdf.count(b"/Type /Page\n"), pdf_padrao.count(b"/Type /Page\n"))

    def test_configuracao_antiga_preserva_logo_sem_caminho(self):
        with TemporaryDirectory() as pasta, patch.object(config, "APP_DIR", Path(pasta)):
            logo_path = Path(pasta) / "logo.png"
            with Image.new("RGB", (20, 10), "green") as logo:
                logo.save(logo_path)
            logo_png = importar_logo(logo_path)
            arquivo = Path(pasta) / "cabecalho_pdf.json"
            arquivo.write_text(json.dumps({"texto": "Acme", "logo_png": base64.b64encode(logo_png).decode("ascii")}))
            preferencias = carregar_cabecalho()
            self.assertEqual(preferencias, PreferenciasCabecalhoPDF("Acme", logo_legado=logo_png))
            salvar_cabecalho(preferencias)
            self.assertEqual(carregar_cabecalho(), preferencias)

    def test_xml_legado_e_pdf_usam_numero_e_emitente_sem_colisao(self):
        with TemporaryDirectory() as pasta:
            destino = Path(pasta)
            xml_dir = destino / "xml"
            xml_dir.mkdir()
            notas = []
            for chave in ("1" * 50, "2" * 50):
                legado = xml_dir / f"{chave}.xml"
                legado.write_text(
                    f'<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse">'
                    f'<infNFSe Id="NFS{chave}">'
                    '<nNFSe>000123</nNFSe><emit><xNome>Acme Serviços / Sul</xNome></emit>'
                    '<DPS><infDPS><nDPS>456</nDPS><toma><xNome>Cliente</xNome></toma>'
                    '</infDPS></DPS></infNFSe></NFSe>',
                    encoding="utf-8",
                )
                notas.append(Nota(chave, xml_local_path=str(legado)))

            with (
                patch.object(config, "XML_DIR", xml_dir),
                patch.object(config, "PDF_DIR", destino / "pdf"),
                patch("tsart_extractor.ui.exports.storage.set_paths"),
            ):
                for tipo in ("xml", "pdf"):
                    arquivo_zip = destino / f"lote_{tipo}.zip"
                    resultado = ExportMixin()._gerar_zip_lote(
                        tipo=tipo,
                        destino=arquivo_zip,
                        notas_snapshot=notas,
                        client=None,
                        cancel_event=None,
                        progresso=lambda *_: None,
                    )
                    self.assertEqual(resultado, (2, 0, None, False))
                    nome = "NFSe 000123 Acme Serviços Sul"
                    with zipfile.ZipFile(arquivo_zip) as lote:
                        self.assertEqual(lote.namelist(), [f"{nome}.{tipo}", f"{nome} (2).{tipo}"])
                        for nota, arquivo in zip(notas, lote.namelist()):
                            local = destino / tipo / nota.chave / f"{nome}.{tipo}"
                            self.assertEqual(lote.read(arquivo), local.read_bytes())
                            if tipo == "pdf":
                                self.assertTrue(local.read_bytes().startswith(b"%PDF"))
