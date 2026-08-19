import threading
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

        if not self.sincronizado:
            messagebox.showwarning(
                "Baixar lote",
                "Sincronize as notas do período (botão '🔄 Sincronizar notas') "
                "antes de baixar o lote.")
            return
        if not self.notas_atuais:
            messagebox.showinfo(
                "Baixar lote", "Nenhuma nota no período sincronizado.")
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
            import zipfile
            ok, falhas = 0, 0
            cancelado = False
            destino = Path(caminho)
            temporario = destino.with_suffix(destino.suffix + ".part")
            try:
                with zipfile.ZipFile(temporario, "w", zipfile.ZIP_DEFLATED) as zf:
                    for i, nota in enumerate(notas_snapshot, start=1):
                        if self._export_cancelar and self._export_cancelar.is_set():
                            cancelado = True
                            break
                        try:
                            local_path = nota.xml_local_path if tipo == "xml" else nota.pdf_local_path
                            if tipo == "xml":
                                path = Path(local_path) if local_path else config.XML_DIR / f"{nota.chave}.xml"
                                if not path.exists():
                                    if not client:
                                        raise RuntimeError("XML não disponível localmente")
                                    path.write_bytes(client.get_xml(nota.chave))
                                storage.set_paths(nota.chave, xml_path=str(path))
                            else:
                                # Sempre gera o DANFSe do zero a partir do XML atual —
                                # nunca reaproveita um PDF já existente em disco, mesmo
                                # que pareça válido (evita colocar no ZIP um PDF
                                # desatualizado de uma exportação anterior).
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

                            zf.write(path, arcname=f"{nota.chave}.{tipo}")
                            ok += 1
                        except Exception as e:
                            logger.log_error(f"Compactar nota {nota.chave} ({tipo})", e)
                            falhas += 1
                        self.gui_queue.put(("zip_progresso", tipo, i, total, ok, falhas))
                if cancelado or ok == 0:
                    temporario.unlink(missing_ok=True)
                    erro = None if cancelado else f"Nenhum arquivo {rotulo} pôde ser preparado."
                else:
                    temporario.replace(destino)
                    erro = None
                self.gui_queue.put(("zip_fim", tipo, ok, falhas, caminho, erro, cancelado))
            except Exception as e:
                temporario.unlink(missing_ok=True)
                logger.log_error(f"Baixar lote {rotulo} (ZIP)", e)
                self.gui_queue.put(("zip_fim", tipo, ok, falhas, caminho, str(e), False))

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
            gerar_relatorio(
                notas=self.notas_atuais,
                empresa_nome=empresa_nome,
                empresa_cnpj=empresa_cnpj,
                output_path=Path(path),
                tipo_notas=self.tipo_notas_var.get(),
                periodo_inicio=self._parse_data_br(self.data_ini_var.get())
                    if self.data_ini_var.get() else "",
                periodo_fim=self._parse_data_br(self.data_fim_var.get())
                    if self.data_fim_var.get() else "",
            )
            self.status_bar.config(text=f"Relatório gerado: {path}")
            messagebox.showinfo("Relatório gerado", "Relatório criado com sucesso.")
        except Exception as e:
            messagebox.showerror("Erro ao gerar relatório", str(e))