import threading
import zipfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

from tsart_extractor.core import app_logging as logger
from tsart_extractor.core import settings as config
from tsart_extractor.persistence import repository as storage
from tsart_extractor.services.danfse import gerar_danfse
from tsart_extractor.services.reporting import gerar_relatorio


class ExportMixin:
    def _baixar_lote_xml(self):
        self._baixar_lote_generico("xml")

    def _baixar_lote_pdf(self):
        self._baixar_lote_generico("pdf")

    def _baixar_lote_generico(self, tipo: str):
        """tipo: 'xml' ou 'pdf' — compacta só os arquivos daquele tipo."""
        assert tipo in ("xml", "pdf")
        btn = self.baixar_lote_btn_xml if tipo == "xml" else self.baixar_lote_btn_pdf
        rotulo = "XML" if tipo == "xml" else "PDF"

        if not self._validar_lote_exportavel("Baixar lote"):
            return

        default_name = f"nfse_lote_{tipo}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        pasta = filedialog.askdirectory(
            title=f"Escolha onde salvar o lote de {rotulo}",
            initialdir=str(config.REPORT_DIR),
        )
        if not pasta:
            return
        caminho = str(Path(pasta) / default_name)

        notas_snapshot = list(self.notas_atuais)
        total = len(notas_snapshot)
        client = self.client

        btn.config(state="disabled")
        self._export_cancelar = threading.Event()
        self._open_progress_dialog(
            f"Exportando lote de {rotulo}",
            f"Preparando arquivos {rotulo}",
            f"Iniciando exportação de {total} nota(s)…",
            self._export_cancelar,
            determinate=True,
            total=total,
        )

        def worker():
            def progresso(i, ok, falhas):
                self.gui_queue.put(("zip_progresso", tipo, i, total, ok, falhas))

            ok, falhas, erro, cancelado = self._gerar_zip_lote(
                tipo=tipo,
                destino=Path(caminho),
                notas_snapshot=notas_snapshot,
                client=client,
                cancel_event=self._export_cancelar,
                progresso=progresso,
            )
            self.gui_queue.put(("zip_fim", tipo, ok, falhas, caminho, erro, cancelado))

        threading.Thread(target=worker, daemon=True).start()

    def _gerar_relatorio(self):
        self._refresh_table()
        if not self.notas_atuais:
            messagebox.showinfo("Relatório",
                                "Nenhuma nota no filtro atual. Faça uma busca primeiro.")
            return
        empresa_nome = self.empresa_var.get().strip() or "(empresa não informada)"
        empresa_cnpj = self.cnpj_var.get().strip() or "(cnpj não informado)"

        default_name = f"relatorio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        path = filedialog.asksaveasfilename(
            initialdir=str(config.REPORT_DIR),
            initialfile=default_name,
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
        )
        if not path:
            return

        try:
            self._gerar_relatorio_em(Path(path), empresa_nome, empresa_cnpj)
            self.status_bar.config(text=f"Relatório gerado: {path}")
            messagebox.showinfo("Relatório gerado", "Relatório criado com sucesso.")
        except Exception as e:
            messagebox.showerror("Erro ao gerar relatório", str(e))

    def _exportar_tudo(self):
        if not self._validar_lote_exportavel("Exportar tudo"):
            return

        pasta = filedialog.askdirectory(
            title="Escolha onde salvar XML, PDF e relatório",
            initialdir=str(config.REPORT_DIR),
        )
        if not pasta:
            return

        destino_dir = Path(pasta)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        caminhos = {
            "xml": destino_dir / f"nfse_lote_xml_{timestamp}.zip",
            "pdf": destino_dir / f"nfse_lote_pdf_{timestamp}.zip",
            "relatorio": destino_dir / f"relatorio_{timestamp}.pdf",
        }

        notas_snapshot = list(self.notas_atuais)
        total_notas = len(notas_snapshot)
        total_passos = max(total_notas * 2 + 1, 1)
        client = self.client
        empresa_nome = self.empresa_var.get().strip() or "(empresa não informada)"
        empresa_cnpj = self.cnpj_var.get().strip() or "(cnpj não informado)"
        tipo_notas = self.tipo_notas_var.get()
        periodo_inicio = (
            self._parse_data_br(self.data_ini_var.get())
            if self.data_ini_var.get() else ""
        )
        periodo_fim = (
            self._parse_data_br(self.data_fim_var.get())
            if self.data_fim_var.get() else ""
        )

        self.baixar_lote_btn_xml.config(state="disabled")
        self.baixar_lote_btn_pdf.config(state="disabled")
        self.relatorio_btn.config(state="disabled")
        self.exportar_tudo.config(state="disabled")
        self._export_cancelar = threading.Event()
        self._open_progress_dialog(
            "Exportando tudo",
            "Gerando XML, PDF e relatório",
            f"Iniciando exportação de {total_notas} nota(s)...",
            self._export_cancelar,
            determinate=True,
            total=total_passos,
        )

        def worker():
            resultados = {}

            def progresso_xml(i, ok, falhas):
                self.gui_queue.put((
                    "export_all_progresso",
                    i,
                    total_passos,
                    f"Preparando XML: {i}/{total_notas}  •  falhas: {falhas}",
                ))

            ok, falhas, erro, cancelado = self._gerar_zip_lote(
                tipo="xml",
                destino=caminhos["xml"],
                notas_snapshot=notas_snapshot,
                client=client,
                cancel_event=self._export_cancelar,
                progresso=progresso_xml,
            )
            resultados["xml"] = (ok, falhas, erro)
            if cancelado:
                self.gui_queue.put(("export_all_fim", resultados, caminhos, None, True))
                return

            def progresso_pdf(i, ok, falhas):
                passo = total_notas + i
                self.gui_queue.put((
                    "export_all_progresso",
                    passo,
                    total_passos,
                    f"Preparando PDF: {i}/{total_notas}  •  falhas: {falhas}",
                ))

            ok, falhas, erro, cancelado = self._gerar_zip_lote(
                tipo="pdf",
                destino=caminhos["pdf"],
                notas_snapshot=notas_snapshot,
                client=client,
                cancel_event=self._export_cancelar,
                progresso=progresso_pdf,
            )
            resultados["pdf"] = (ok, falhas, erro)
            if cancelado:
                self.gui_queue.put(("export_all_fim", resultados, caminhos, None, True))
                return

            if self._export_cancelar and self._export_cancelar.is_set():
                self.gui_queue.put(("export_all_fim", resultados, caminhos, None, True))
                return

            erro_relatorio = None
            try:
                self._gerar_relatorio_em(
                    caminhos["relatorio"],
                    empresa_nome,
                    empresa_cnpj,
                    notas=notas_snapshot,
                    tipo_notas=tipo_notas,
                    periodo_inicio=periodo_inicio,
                    periodo_fim=periodo_fim,
                )
                self.gui_queue.put((
                    "export_all_progresso",
                    total_passos,
                    total_passos,
                    "Relatório consolidado gerado.",
                ))
            except Exception as e:
                erro_relatorio = str(e)
                logger.log_error("Exportar tudo (relatório)", e)
            self.gui_queue.put(("export_all_fim", resultados, caminhos, erro_relatorio, False))

        threading.Thread(target=worker, daemon=True).start()

    def _validar_lote_exportavel(self, titulo: str) -> bool:
        if not self.sincronizado:
            messagebox.showwarning(
                titulo,
                "Sincronize as notas do período (botão 'Sincronizar notas') "
                "antes de exportar o lote.",
            )
            return False
        if not self.notas_atuais:
            messagebox.showinfo(titulo, "Nenhuma nota no período sincronizado.")
            return False
        return True

    def _gerar_relatorio_em(
        self,
        path: Path,
        empresa_nome: str,
        empresa_cnpj: str,
        *,
        notas=None,
        tipo_notas: str | None = None,
        periodo_inicio: str | None = None,
        periodo_fim: str | None = None,
    ):
        return gerar_relatorio(
            notas=notas if notas is not None else self.notas_atuais,
            empresa_nome=empresa_nome,
            empresa_cnpj=empresa_cnpj,
            output_path=path,
            tipo_notas=tipo_notas if tipo_notas is not None else self.tipo_notas_var.get(),
            periodo_inicio=periodo_inicio
                if periodo_inicio is not None
                else (
                    self._parse_data_br(self.data_ini_var.get())
                    if self.data_ini_var.get() else ""
                ),
            periodo_fim=periodo_fim
                if periodo_fim is not None
                else (
                    self._parse_data_br(self.data_fim_var.get())
                    if self.data_fim_var.get() else ""
                ),
        )

    def _gerar_zip_lote(
        self,
        *,
        tipo: str,
        destino: Path,
        notas_snapshot,
        client,
        cancel_event,
        progresso,
    ):
        assert tipo in ("xml", "pdf")
        rotulo = "XML" if tipo == "xml" else "PDF"
        ok, falhas = 0, 0
        cancelado = False
        temporario = destino.with_suffix(destino.suffix + ".part")
        try:
            with zipfile.ZipFile(temporario, "w", zipfile.ZIP_DEFLATED) as zf:
                total = len(notas_snapshot)
                for i, nota in enumerate(notas_snapshot, start=1):
                    if cancel_event and cancel_event.is_set():
                        cancelado = True
                        break
                    try:
                        path = self._preparar_arquivo_lote(tipo, nota, client)
                        zf.write(path, arcname=f"{nota.chave}.{tipo}")
                        ok += 1
                    except Exception as e:
                        logger.log_error(f"Compactar nota {nota.chave} ({tipo})", e)
                        falhas += 1
                    progresso(i, ok, falhas)
            if cancelado or ok == 0:
                temporario.unlink(missing_ok=True)
                erro = None if cancelado else f"Nenhum arquivo {rotulo} pôde ser preparado."
            else:
                temporario.replace(destino)
                erro = None
            return ok, falhas, erro, cancelado
        except Exception as e:
            temporario.unlink(missing_ok=True)
            logger.log_error(f"Baixar lote {rotulo} (ZIP)", e)
            return ok, falhas, str(e), False

    def _preparar_arquivo_lote(self, tipo: str, nota, client) -> Path:
        if tipo == "xml":
            path = Path(nota.xml_local_path) if nota.xml_local_path else config.XML_DIR / f"{nota.chave}.xml"
            if not path.exists():
                if not client:
                    raise RuntimeError("XML não disponível localmente")
                path.write_bytes(client.get_xml(nota.chave))
            storage.set_paths(nota.chave, xml_path=str(path))
            return path

        # Sempre gera o DANFSe do zero a partir do XML atual, evitando usar
        # PDF antigo que possa ter sido criado por uma exportação anterior.
        path = config.PDF_DIR / f"{nota.chave}.pdf"
        xml_path = Path(nota.xml_local_path) if nota.xml_local_path else config.XML_DIR / f"{nota.chave}.xml"
        if not xml_path.exists():
            if not client:
                raise RuntimeError("XML necessário para gerar o PDF não está disponível")
            xml_path.write_bytes(client.get_xml(nota.chave))
            storage.set_paths(nota.chave, xml_path=str(xml_path))
        gerar_danfse(
            xml_path, path,
            cancelada=(nota.situacao == "Cancelada"),
        )
        storage.set_paths(nota.chave, pdf_path=str(path))
        return path
