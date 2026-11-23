import queue
import re
from time import monotonic
from tkinter import messagebox

from tsart_extractor.core import app_logging as logger


class EventMixin:
    def _poll_queue(self):
        try:
            while True:
                item = self.gui_queue.get_nowait()
                kind = item[0]

                # ── Certificados carregados ─────────────────────────────
                if kind == "certs_carregados":
                    _, entries, prev_sel = item
                    self._cert_entries = entries
                    display_names = [e.display_name for e in entries]
                    self.cert_combo["values"] = display_names
                    selected_entry = None
                    if entries:
                        # Tenta restaurar seleção anterior
                        if prev_sel and prev_sel in display_names:
                            self.cert_var.set(prev_sel)
                            selected_entry = entries[display_names.index(prev_sel)]
                        elif not self.cert_var.get() or "Carregando" in self.cert_var.get():
                            self.cert_var.set(entries[0].display_name)
                            selected_entry = entries[0]
                        if selected_entry:
                            self._apply_cert_entry_info(selected_entry)
                    else:
                        self.cert_var.set("")

                    store_ok  = sum(1 for e in entries if e.is_store_cert and e.exportable)
                    sem_chave = sum(1 for e in entries if e.is_store_cert and not e.has_private_key)
                    vencidos = sum(1 for e in entries if e.is_store_cert and e.is_expired)
                    store_a3  = sum(
                        1 for e in entries
                        if e.is_store_cert and e.has_private_key and not e.exportable
                    )
                    arquivos  = sum(1 for e in entries if not e.is_store_cert)
                    partes = []
                    if store_ok:
                        partes.append(f"{store_ok} do Windows Store")
                    if store_a3:
                        partes.append(f"{store_a3} com chave protegida")
                    if sem_chave:
                        partes.append(f"{sem_chave} sem chave privada")
                    if vencidos:
                        partes.append(f"{vencidos} vencido(s)")
                    if arquivos:
                        partes.append(f"{arquivos} arquivo(s)")
                    resumo = "  |  ".join(partes) if partes else "nenhum encontrado"
                    self.status_bar.config(
                        text=f"{len(entries)} certificado(s): {resumo}.  "
                             "Selecione, informe a senha (se necessário) e clique em Conectar.")

                elif kind == "certs_erro":
                    _, erro = item
                    self._cert_entries = []
                    self.cert_combo["values"] = []
                    self.cert_var.set("")
                    self.status_bar.config(text=f"Não foi possível buscar certificados: {erro}")
                    messagebox.showerror(
                        "Busca de certificados",
                        f"Não foi possível consultar os certificados instalados.\n\n{erro}",
                        parent=self.root,
                    )

                # ── Resultado da conexão ────────────────────────────────
                elif kind == "conexao_resultado":
                    _, ok, msg, client, selected_display_name = item
                    self.conectar_btn.config(state="normal")
                    self.cert_combo.config(state="readonly")
                    if selected_display_name != self.cert_var.get():
                        self.client = None
                        self.buscar_btn.config(state="disabled")
                        self.status_conexao.config(
                            text="●  Reconecte o certificado", style="Offline.TLabel"
                        )
                        self.status_bar.config(
                            text="O certificado foi alterado durante a conexão. Clique em Conectar novamente."
                        )
                        continue
                    if ok:
                        self.client = client
                        self.status_conexao.config(text="●  Conectado", style="Online.TLabel")
                        self.buscar_btn.config(state="normal")
                        cnpj_cert = re.sub(r"\D", "", getattr(client, "client_cnpj", "") or "")
                        cnpj_atual = re.sub(r"\D", "", self.cnpj_var.get())
                        if cnpj_cert:
                            self.cnpj_var.set(cnpj_cert)
                            cnpj_msg = f"CNPJ obtido do certificado: {cnpj_cert}."
                        elif len(cnpj_atual) != 14:
                            cnpj_msg = "Não consegui obter o CNPJ do certificado."
                            messagebox.showwarning(
                                "CNPJ não identificado",
                                "Não consegui obter o CNPJ do certificado digital selecionado.\n\n"
                                "Digite o CNPJ da empresa manualmente no campo 'CNPJ' antes de sincronizar.",
                            )
                        else:
                            cnpj_msg = "CNPJ não veio do certificado; usando o CNPJ digitado manualmente."
                        self.status_bar.config(
                            text=f"Conectado ✓  —  {cnpj_msg} Defina o período e clique em '🔄 Sincronizar notas'.")
                    else:
                        self.client = None
                        self.status_conexao.config(text="●  Falha na conexão", style="Offline.TLabel")
                        self.buscar_btn.config(state="disabled")
                        messagebox.showerror("Conexão", msg)

                # ── Progresso da busca (modal) ──────────────────────────
                elif kind == "busca_progresso":
                    _, lotes, total, periodo = item
                    if self._modal_status:
                        self._modal_status.config(
                            text=(
                                f"Lote {lotes}  |  "
                                f"{total} nota(s) sincronizadas  |  "
                                f"{periodo} no período selecionado"
                            )
                        )

                # ── Fim da busca (metadados + XML) ──────────────────────
                elif kind == "busca_fim":
                    _, total, periodo, erro, cancelado = item
                    if erro:
                        self._close_progress_dialog()
                        self.buscar_btn.config(state="normal" if self.client else "disabled")
                        self.status_bar.config(text=f"Erro na busca: {erro}")
                        messagebox.showerror("Erro na busca de NFSe", erro)
                        self._refresh_table()
                    elif cancelado:
                        self._close_progress_dialog()
                        self.buscar_btn.config(state="normal" if self.client else "disabled")
                        self.status_bar.config(
                            text=f"Busca cancelada pelo usuário. {total} nota(s) sincronizadas. "
                                 "Sincronize novamente (sem cancelar) para liberar o baixar lote.")
                        self._refresh_table()
                    else:
                        self.status_bar.config(
                            text=f"Metadados sincronizados: {total} nota(s), "
                                 f"{periodo} no período. Validando XMLs locais...")
                        self._refresh_table()
                        # Segunda etapa, automática: garante os XMLs em disco
                        # para todas as notas do período antes de liberar o
                        # botão de baixar lote.
                        self._iniciar_download_arquivos_pos_sync()

                # ── 2ª etapa da sincronização: validar XMLs pendentes ──
                elif kind == "sync_arquivos_progresso":
                    _, i, total_arquivos, ok, falhas = item
                    self._update_progress_dialog(
                        i,
                        total_arquivos,
                        f"Validando XMLs: {i}/{total_arquivos}  •  falhas: {falhas}",
                    )

                elif kind == "sync_arquivos_fim":
                    _, ok, falhas, cancelado = item
                    self._close_progress_dialog()
                    self.buscar_btn.config(state="normal" if self.client else "disabled")
                    if cancelado:
                        self.status_bar.config(text="Sincronização cancelada pelo usuário.")
                        self.sincronizado = False
                        continue
                    self.sincronizado = True
                    self.periodo_sincronizado = self._sync_periodo_atual
                    self._last_sync_signature = self._sync_signature_atual
                    self._last_sync_completed_at = monotonic()
                    self._refresh_table()
                    estado = "normal" if self.notas_atuais else "disabled"
                    self.baixar_lote_btn_xml.config(state=estado)
                    self.baixar_lote_btn_pdf.config(state=estado)
                    if falhas:
                        self.status_bar.config(
                            text=f"Sincronização concluída: {ok} XML(s) pronto(s) e "
                                 f"{falhas} indisponível(is).")
                    else:
                        self.status_bar.config(
                            text=f"Sincronização concluída: {ok} nota(s) prontas. "
                                 "Botões 'Baixar lote XML' e 'Baixar lote PDF' liberados.")

                # ── Baixar lote (XML ou PDF, um ZIP por vez) ────────────
                elif kind == "zip_progresso":
                    _, tipo, i, total_zip, ok, falhas = item
                    rotulo = "XML" if tipo == "xml" else "PDF"
                    self._update_progress_dialog(
                        i,
                        total_zip,
                        f"Preparando {rotulo}: {i}/{total_zip}  •  falhas: {falhas}",
                    )

                elif kind == "zip_fim":
                    _, tipo, ok, falhas, caminho, erro, cancelado, aviso_logo = item
                    self._close_progress_dialog()
                    rotulo = "XML" if tipo == "xml" else "PDF"
                    btn = self.baixar_lote_btn_xml if tipo == "xml" else self.baixar_lote_btn_pdf
                    btn.config(state="normal" if self.sincronizado else "disabled")
                    if cancelado:
                        self.status_bar.config(text=f"Exportação de {rotulo} cancelada.")
                    elif erro:
                        self.status_bar.config(text=f"Erro ao gerar ZIP {rotulo}.")
                        mensagem = f"Erro ao gerar arquivo ZIP ({rotulo}):\n{erro}"
                        if aviso_logo:
                            mensagem += f"\n\n{aviso_logo}"
                        messagebox.showerror(
                            "Baixar lote", mensagem)
                    else:
                        self.status_bar.config(text=f"ZIP {rotulo} gerado com {ok} nota(s).")
                        mostrar = messagebox.showwarning if aviso_logo else messagebox.showinfo
                        titulo = "Lote gerado sem logo" if aviso_logo else "Lote gerado"
                        mostrar(
                            titulo,
                            f"Arquivo ZIP ({rotulo}) criado com sucesso em:\n{caminho}\n\n"
                            f"{ok} nota(s) incluída(s)"
                            + (f", {falhas} sem arquivo {rotulo} local (ignorada(s))." if falhas else ".")
                            + (f"\n\n{aviso_logo}" if aviso_logo else "")
                        )

                # ── Exportar tudo (XML + PDF + relatório) ───────────────
                elif kind == "export_all_progresso":
                    _, i, total_export, detalhe = item
                    self._update_progress_dialog(i, total_export, detalhe)

                elif kind == "export_all_fim":
                    _, resultados, caminhos, erro_relatorio, cancelado, aviso_logo = item
                    self._close_progress_dialog()
                    estado_lote = "normal" if self.sincronizado and self.notas_atuais else "disabled"
                    self.baixar_lote_btn_xml.config(state=estado_lote)
                    self.baixar_lote_btn_pdf.config(state=estado_lote)
                    self.relatorio_btn.config(state="normal")
                    self.exportar_tudo.config(state="normal")

                    if cancelado:
                        self.status_bar.config(text="Exportação completa cancelada.")
                        continue

                    partes_erro = []
                    for tipo in ("xml", "pdf"):
                        ok, falhas, erro = resultados.get(tipo, (0, 0, "Não executado."))
                        rotulo = "XML" if tipo == "xml" else "PDF"
                        if erro:
                            partes_erro.append(f"{rotulo}: {erro}")
                    if erro_relatorio:
                        partes_erro.append(f"Relatório: {erro_relatorio}")

                    if partes_erro:
                        mensagem = "Alguns arquivos não puderam ser gerados:\n\n" + "\n".join(partes_erro)
                        if aviso_logo:
                            mensagem += f"\n\n{aviso_logo}"
                        self.status_bar.config(text="Exportação completa finalizada com erro(s).")
                        messagebox.showerror("Exportar tudo", mensagem)
                    else:
                        xml_ok, xml_falhas, _ = resultados.get("xml", (0, 0, None))
                        pdf_ok, pdf_falhas, _ = resultados.get("pdf", (0, 0, None))
                        self.status_bar.config(
                            text=f"Exportação completa gerada: {xml_ok} XML(s), {pdf_ok} PDF(s) e relatório."
                        )
                        falhas_msg = ""
                        if xml_falhas or pdf_falhas:
                            falhas_msg = (
                                f"\n\nFalhas ignoradas: {xml_falhas} XML(s), {pdf_falhas} PDF(s)."
                            )
                        mostrar = messagebox.showwarning if aviso_logo else messagebox.showinfo
                        titulo = "Exportação completa com PDFs sem logo" if aviso_logo else "Exportação completa"
                        aviso_msg = f"\n\n{aviso_logo}" if aviso_logo else ""
                        mostrar(
                            titulo,
                            "Arquivos criados com sucesso em:\n"
                            f"{caminhos['xml']}\n"
                            f"{caminhos['pdf']}\n"
                            f"{caminhos['relatorio']}"
                            f"{falhas_msg}{aviso_msg}",
                        )

        except queue.Empty:
            pass
        except Exception as exc:
            # Uma falha de apresentação não pode interromper definitivamente
            # o polling das threads de sincronização/download.
            logger.log_error("Fila de eventos da interface", exc)
            self.status_bar.config(text=f"Falha ao atualizar a interface: {exc}")
        self.root.after(150, self._poll_queue)
