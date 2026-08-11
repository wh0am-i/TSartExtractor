import csv
import re
import threading
from datetime import datetime
from time import monotonic
from tkinter import messagebox

from tsart_extractor.core import app_logging as logger
from tsart_extractor.core import settings as config
from tsart_extractor.persistence import repository as storage
from tsart_extractor.services.nfse_api import NFSeAPIError, parse_evento_cancelamento, parse_nfse_xml


class SynchronizationMixin:
    def _on_filtro_tipo_alterado(self, event=None):
        self._on_periodo_alterado()
        self._refresh_table()

    def _on_periodo_alterado(self, *args):
        """
        Chamado a cada alteração (digitação) nos campos de data. Se já havia
        uma sincronização válida, ela é invalidada e os botões de baixar
        lote voltam a ficar bloqueados, forçando uma nova sincronização.
        """
        if self.sincronizado:
            self.sincronizado = False
            self.periodo_sincronizado = None
            if hasattr(self, "baixar_lote_btn_xml"):
                self.baixar_lote_btn_xml.config(state="disabled")
            if hasattr(self, "baixar_lote_btn_pdf"):
                self.baixar_lote_btn_pdf.config(state="disabled")
            self.status_bar.config(
                text="Período alterado após a sincronização — "
                     "sincronize novamente antes de baixar o lote.")

    def _buscar_nfse(self):
        if not self.client:
            messagebox.showwarning("Conexão", "Conecte-se primeiro com um certificado válido.")
            return

        ini_str = self.data_ini_var.get().strip()
        fim_str = self.data_fim_var.get().strip()
        if not ini_str or not fim_str:
            messagebox.showwarning(
                "Período obrigatório",
                "Informe a data de início e a data de fim do período para buscar as NFSe.")
            return

        data_ini = self._parse_data_br(ini_str)
        data_fim = self._parse_data_br(fim_str)
        if not data_ini or not data_fim:
            return
        if data_ini > data_fim:
            messagebox.showwarning("Período inválido",
                                   "A data de início deve ser anterior ou igual à data de fim.")
            return

        # ------------------------------------------------------------------
        # CNPJ do prestador (obrigatório para separar emitidas x recebidas)
        # ------------------------------------------------------------------
        # Resolvido AQUI, antes de iniciar a thread, e nunca fica vazio:
        # se não der pra identificar com segurança, abortamos a sincronização
        # em vez de deixar passar tudo sem filtro (é isso que causava notas
        # RECEBIDAS aparecendo junto com as EMITIDAS).
        cnpj_filtro = re.sub(r"\D", "", self.cnpj_var.get())
        tipo_filtro = self._tipo_filtro_atual()

        if len(cnpj_filtro) != 14:
            messagebox.showwarning(
                "CNPJ obrigatório",
                "Informe o CNPJ da empresa no campo 'CNPJ'."
            )
            return

        cnpj_certificado = re.sub(
            r"\D", "", getattr(self.client, "client_cnpj", "") or ""
        )
        if len(cnpj_certificado) == 14 and cnpj_certificado != cnpj_filtro:
            messagebox.showwarning(
                "Certificado diferente da empresa",
                f"O certificado conectado pertence ao CNPJ {cnpj_certificado}, mas o campo "
                f"da empresa contém {cnpj_filtro}.\n\nSelecione o certificado correto e clique em "
                "Conectar antes de sincronizar.",
            )
            self._invalidate_certificate_connection()
            return

        signature = (data_ini, data_fim, cnpj_filtro, tipo_filtro)
        # Evita repetir imediatamente uma consulta idêntica depois de "Limpar".
        # O ADN limita chamadas em sequência, o que acionava o backoff HTTP e
        # fazia a segunda sincronização parecer muito mais lenta.
        if (
            self._last_sync_signature == signature
            and self._last_sync_completed_at is not None
            and monotonic() - self._last_sync_completed_at < 45
        ):
            self._sync_periodo_atual = (data_ini, data_fim)
            self.periodo_sincronizado = self._sync_periodo_atual
            self.sincronizado = True
            self._refresh_table()
            estado = "normal" if self.notas_atuais else "disabled"
            self.baixar_lote_btn_xml.config(state=estado)
            self.baixar_lote_btn_pdf.config(state=estado)
            self.status_bar.config(
                text=f"Sincronização recente reutilizada: {len(self.notas_atuais)} nota(s) no período."
            )
            return

        # Nova sincronização em andamento: invalida qualquer sincronização
        # anterior e bloqueia o botão de baixar lote até este fluxo terminar.
        self.sincronizado = False
        self.periodo_sincronizado = None
        self._sync_periodo_atual = (data_ini, data_fim)
        self._sync_signature_atual = signature
        self.baixar_lote_btn_xml.config(state="disabled")
        self.baixar_lote_btn_pdf.config(state="disabled")

        self._busca_cancelar = threading.Event()
        self._open_progress_dialog(
            "Sincronização em andamento",
            "Sincronizando notas fiscais",
            "Consultando documentos no servidor…",
            self._busca_cancelar,
        )

        cancelar = self._busca_cancelar
        client = self.client

        def worker():
            total_lotes = 0
            total_notas = 0
            total_periodo = 0
            total_excluidas = 0

            # Log de auditoria: uma linha por documento recebido do governo,
            # mostrando exatamente o CNPJ extraído do XML como "prestador" e
            # a decisão tomada. Fica ao lado do main.py/api_client.py — abra
            # com Excel/LibreOffice e filtre por decisao=INCLUIDA para
            # conferir, nota a nota, se o CNPJ bateu certo.
            debug_csv_path = config.DEBUG_DIR / "debug_filtro_emitida_recebida.csv"
            debug_novo = not debug_csv_path.exists()
            debug_file = open(debug_csv_path, "a", newline="", encoding="utf-8")
            debug_writer = csv.writer(debug_file)
            if debug_novo:
                debug_writer.writerow([
                    "timestamp", "nsu", "chave", "prestador_cnpj_xml",
                    "tomador_doc_xml", "cnpj_filtro_usado", "decisao",
                ])

            try:
                last_nsu = storage.get_last_nsu(cnpj_filtro)
                for novo_nsu, xmls in client.iter_dfe_batches(last_nsu, cancelar):
                    total_lotes += 1
                    for xml_bytes in xmls:
                        info = parse_nfse_xml(xml_bytes)
                        if info:
                            prestador_cnpj_limpo = re.sub(r"\D", "", info.get("cnpj_prestador") or "")
                            tomador_doc_limpo = re.sub(r"\D", "", info.get("tomador_documento") or "")
                            incluida = (
                                prestador_cnpj_limpo == cnpj_filtro
                                if tipo_filtro == "emitidas"
                                else tomador_doc_limpo == cnpj_filtro
                            )
                            debug_writer.writerow([
                                datetime.now().isoformat(timespec="seconds"),
                                novo_nsu,
                                info.get("chave"),
                                prestador_cnpj_limpo,
                                tomador_doc_limpo,
                                f"{cnpj_filtro} ({tipo_filtro})",
                                "INCLUIDA" if incluida else "EXCLUIDA",
                            ])
                            debug_file.flush()

                            nota = storage.Nota(
                                chave=info["chave"],
                                nsu=novo_nsu,
                                tipo_documento="NFSE",
                                cnpj_prestador=info["cnpj_prestador"],
                                tomador_documento=info["tomador_documento"],
                                tomador_nome=info["tomador_nome"],
                                data_emissao=info["data_emissao"],
                                situacao="Emitida",
                                valor_liquido=info["valor_liquido"],
                            )
                            storage.upsert_nota(nota)
                            xml_path = config.XML_DIR / f"{info['chave']}.xml"
                            xml_path.write_bytes(xml_bytes)
                            storage.set_paths(info["chave"], xml_path=str(xml_path))
                            if incluida:
                                total_notas += 1
                                data_em = storage.normalize_date_to_iso(info.get("data_emissao") or "")
                                if data_ini <= data_em <= data_fim:
                                    total_periodo += 1
                            else:
                                total_excluidas += 1
                        else:
                            chave_canc = parse_evento_cancelamento(xml_bytes)
                            if chave_canc:
                                storage.marcar_cancelada(chave_canc)
                    storage.set_last_nsu(novo_nsu, cnpj_filtro)
                    self.gui_queue.put(
                        ("busca_progresso", total_lotes, total_notas, total_periodo))

                cancelado = cancelar.is_set()
                logger.log_info(
                    "Filtro Emitida/Recebida",
                    f"Sincronização finalizada. cnpj_filtro={cnpj_filtro!r} | "
                    f"incluídas={total_notas} | excluídas={total_excluidas} | "
                    f"log detalhado em {debug_csv_path}",
                )
                self.gui_queue.put(
                    ("busca_fim", total_notas, total_periodo, None, cancelado))
            except NFSeAPIError as e:
                logger.log_error("Busca NFSe (Erro API)", e)
                self.gui_queue.put(
                    ("busca_fim", total_notas, total_periodo, str(e), False))
            except Exception as e:
                logger.log_error("Busca NFSe (Erro Inesperado)", e)
                self.gui_queue.put(
                    ("busca_fim", total_notas, total_periodo, f"Erro inesperado: {e}", False))
            finally:
                try:
                    debug_file.close()
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _iniciar_download_arquivos_pos_sync(self):
        if not self.client:
            # Sem cliente não há como buscar XMLs que faltem; ainda assim
            # considera sincronizado o que já está em disco.
            self.sincronizado = True
            self.periodo_sincronizado = self._sync_periodo_atual
            self._last_sync_signature = self._sync_signature_atual
            self._last_sync_completed_at = monotonic()
            estado = "normal" if self.notas_atuais else "disabled"
            self.baixar_lote_btn_xml.config(state=estado)
            self.baixar_lote_btn_pdf.config(state=estado)
            self.buscar_btn.config(state="normal" if self.client else "disabled")
            self._close_progress_dialog()
            return

        if not self.notas_atuais:
            # Período sem notas: nada para baixar, mas considera sincronizado
            # (não há lote a gerar mesmo).
            self.sincronizado = True
            self.periodo_sincronizado = self._sync_periodo_atual
            self._last_sync_signature = self._sync_signature_atual
            self._last_sync_completed_at = monotonic()
            self.baixar_lote_btn_xml.config(state="disabled")
            self.baixar_lote_btn_pdf.config(state="disabled")
            self.status_bar.config(
                text="Sincronização concluída: nenhuma nota no período selecionado.")
            self.buscar_btn.config(state="normal" if self.client else "disabled")
            self._close_progress_dialog()
            return

        total = len(self.notas_atuais)
        notas_snapshot = list(self.notas_atuais)
        client = self.client

        def worker():
            ok, falhas = 0, 0
            for concluídas, nota in enumerate(notas_snapshot, start=1):
                if self._busca_cancelar and self._busca_cancelar.is_set():
                    self.gui_queue.put(("sync_arquivos_fim", ok, falhas, True))
                    return
                try:
                    xml_path = config.XML_DIR / f"{nota.chave}.xml"
                    if not xml_path.exists():
                        xml_bytes = client.get_xml(nota.chave)
                        xml_path.write_bytes(xml_bytes)
                    storage.set_paths(nota.chave, xml_path=str(xml_path))
                    ok += 1
                except Exception as e:
                    logger.log_error(f"Preparar XML da nota {nota.chave}", e)
                    falhas += 1
                self.gui_queue.put(
                    ("sync_arquivos_progresso", concluídas, total, ok, falhas))

            self.gui_queue.put(("sync_arquivos_fim", ok, falhas, False))

        threading.Thread(target=worker, daemon=True).start()
