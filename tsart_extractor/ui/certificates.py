import re
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

from tsart_extractor.core import app_logging as logger
from tsart_extractor.services import certificates as cert_finder
from tsart_extractor.services.certificates import CertEntry
from tsart_extractor.services.nfse_api import NFSeClient


class CertificateMixin:
    def _invalidate_certificate_connection(self):
        """Nunca reutiliza um cliente autenticado depois da troca de certificado."""
        self.client = None
        self.sincronizado = False
        self.periodo_sincronizado = None
        self._last_sync_signature = None
        self._last_sync_completed_at = None
        self.buscar_btn.config(state="disabled")
        self.baixar_lote_btn_xml.config(state="disabled")
        self.baixar_lote_btn_pdf.config(state="disabled")
        self.status_conexao.config(
            text="●  Reconecte o certificado", style="Offline.TLabel"
        )

    def _reload_certs(self):
        """Inicia busca assíncrona de certificados (Store + arquivos)."""
        prev = self.cert_var.get()
        self.cert_combo["values"] = ["⏳  Carregando certificados..."]
        if not prev or "Carregando" in prev:
            self.cert_var.set("⏳  Carregando certificados...")

        def worker():
            try:
                entries = cert_finder.find_certificates()
                self.gui_queue.put(("certs_carregados", entries, prev))
            except Exception as exc:
                logger.log_error("Busca de certificados", exc)
                self.gui_queue.put(("certs_erro", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_cert_selected(self, event=None):
        self._invalidate_certificate_connection()
        # Obtém o índice do item selecionado no Combobox
        idx = self.cert_combo.current()
        if idx >= 0 and idx < len(self._cert_entries):
            e = self._cert_entries[idx]
            self._apply_cert_entry_info(e)
            self.status_bar.config(
                text=f"Certificado selecionado para o CNPJ {e.cnpj or 'não identificado'}. "
                     "Clique em Conectar para autenticar novamente."
            )

    def _apply_cert_entry_info(self, entry: CertEntry):
        """Preenche senha interna e CNPJ quando estes dados vierem do certificado."""
        if entry.is_store_cert and entry.pfx_password and entry.exportable:
            self.senha_var.set(entry.pfx_password)
        if entry.cnpj:
            self.cnpj_var.set(entry.cnpj)
            self.status_bar.config(text=f"CNPJ obtido do certificado selecionado: {entry.cnpj}.")

    def _browse_cert(self):
        path = filedialog.askopenfilename(
            title="Selecione o certificado",
            filetypes=[("Certificado PKCS#12", "*.pfx *.p12"), ("Todos os arquivos", "*.*")],
        )
        if not path:
            return
        # Adiciona à lista se ainda não estiver lá
        if not any(e.pfx_path == path for e in self._cert_entries):
            p = Path(path)
            entry = CertEntry(
                display_name=f"📄 {p.name}  ({p.parent})",
                pfx_path=path,
                pfx_password="",
                is_store_cert=False,
                exportable=True,
            )
            self._cert_entries.append(entry)
            self.cert_combo["values"] = [e.display_name for e in self._cert_entries]
        # Seleciona o arquivo navegado
        for e in self._cert_entries:
            if e.pfx_path == path:
                self.cert_var.set(e.display_name)
                self.senha_var.set("")  # usuário digita a senha do arquivo
                self._invalidate_certificate_connection()
                break

    def _resolve_cert(self):
        """
        Retorna os dados do arquivo/store e a localização do repositório.
        Certificados A1 em arquivo usam pfx_path; certificados do Windows Store
        nao exportaveis (A3/token) usam thumbprint.
        """
        sel = self.cert_var.get().strip()
        
        # 1. Tenta encontrar o certificado pelo texto exato da interface
        # Isso contorna o bug do Tkinter onde combobox.current() retorna -1 no carregamento automático
        for e in self._cert_entries:
            if e.display_name == sel:
                if e.is_store_cert and not e.pfx_path and e.thumbprint:
                    return "", "", e.thumbprint, e.cnpj, True, e.store_location, e.store_name
                
                # Usa a senha interna do store (se auto-exportado) ou a digitada (para arquivos manuais)
                senha = e.pfx_password if e.is_store_cert else self.senha_var.get()
                return (
                    e.pfx_path, senha, e.thumbprint, e.cnpj, e.is_store_cert,
                    e.store_location, e.store_name,
                )

        # 2. Fallback: Se não achou na lista, assume que é um caminho de arquivo digitado manualmente
        return sel, self.senha_var.get(), "", "", False, "CurrentUser", "My"

    def _conectar(self):
        selected_entry = next(
            (entry for entry in self._cert_entries if entry.display_name == self.cert_var.get()),
            None,
        )
        if selected_entry and selected_entry.is_expired:
            messagebox.showwarning(
                "Certificado vencido",
                "O certificado foi encontrado, mas está fora da validade. "
                "Selecione um certificado válido para continuar.",
                parent=self.root,
            )
            return
        if selected_entry and selected_entry.is_store_cert and not selected_entry.has_private_key:
            messagebox.showwarning(
                "Certificado sem chave privada",
                "O certificado foi encontrado, mas o Windows não possui a chave privada "
                "correspondente. Reimporte o arquivo PFX completo ou conecte o token/cartão.",
                parent=self.root,
            )
            return

        (
            pfx_path, pfx_password, thumbprint, cert_cnpj, is_store_cert,
            store_location, store_name,
        ) = self._resolve_cert()
        if not pfx_path and not thumbprint:
            messagebox.showwarning("Certificado", "Escolha um certificado do Windows ou um arquivo .pfx/.p12.")
            return
        if pfx_path and not Path(pfx_path).exists():
            messagebox.showerror("Certificado", f"Arquivo não encontrado:\n{pfx_path}")
            return

        self.status_conexao.config(text="●  Conectando…", style="Connecting.TLabel")
        self.conectar_btn.config(state="disabled")
        self.cert_combo.config(state="disabled")
        self.buscar_btn.config(state="disabled")
        selected_display_name = self.cert_var.get()

        def worker():
            try:
                client = NFSeClient(
                    pfx_path=pfx_path,
                    pfx_password=pfx_password,
                    thumbprint=thumbprint,
                    store_location=store_location,
                    store_name=store_name,
                    client_cnpj=cert_cnpj,
                    auto_extract_pfx_cnpj=is_store_cert,
                )
                ok, msg = client.test_connection()
                if not ok:
                    logger.log_error("Conexão - Validação Falhou", msg)
                self.gui_queue.put(
                    ("conexao_resultado", ok, msg, client, selected_display_name)
                )
            except Exception as e:
                logger.log_error("Conexão - Exceção no worker", e)
                self.gui_queue.put(
                    ("conexao_resultado", False, str(e), None, selected_display_name)
                )

        threading.Thread(target=worker, daemon=True).start()
