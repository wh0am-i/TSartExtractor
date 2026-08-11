import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tkinter import END, messagebox

from tsart_extractor.persistence import repository as storage


class NotesMixin:
    def _limpar_tela(self):
        self.data_ini_var.set("")
        self.data_fim_var.set("")
        self.busca_lista_var.set("")
        self.notas_atuais = []
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.detalhe_label.config(text="Selecione uma nota para consultar os detalhes.")
        self._set_xml_preview("")
        self.abrir_xml_btn.config(state="disabled")
        self.abrir_pdf_btn.config(state="disabled")
        self.copiar_chave_btn.config(state="disabled")
        self.resumo_label.config(text="0 notas  •  R$ 0,00")
        self.sincronizado = False
        self.periodo_sincronizado = None
        self.baixar_lote_btn_xml.config(state="disabled")
        self.baixar_lote_btn_pdf.config(state="disabled")
        self.status_bar.config(
            text="Tela limpa. Defina o período e clique em '🔄 Sincronizar notas'.")

    def _zerar_nsu(self):
        cnpj = re.sub(r"\D", "", self.cnpj_var.get())
        if len(cnpj) != 14:
            messagebox.showwarning(
                "CNPJ obrigatório",
                "Informe o CNPJ da empresa cujo histórico deve ser reiniciado.",
            )
            return
        if messagebox.askyesno(
            "Resetar NSU",
            f"Deseja resetar o NSU de sincronização do CNPJ {cnpj} para 0?\n\n"
            "Isso forçará a próxima busca a consultar e baixar novamente todos os "
            "documentos do servidor do governo desde o início, gravando-os no log para análise."
        ):
            storage.set_last_nsu(0, cnpj)
            self.sincronizado = False
            self.periodo_sincronizado = None
            self._last_sync_signature = None
            self._last_sync_completed_at = None
            self.baixar_lote_btn_xml.config(state="disabled")
            self.baixar_lote_btn_pdf.config(state="disabled")
            messagebox.showinfo(
                "NSU Resetado",
                f"NSU do CNPJ {cnpj} resetado para 0 com sucesso.",
            )

    def _formatar_data(self, var, entry):
        # posição atual do cursor
        pos = entry.index("insert")

        texto = "".join(c for c in var.get() if c.isdigit())
        texto = texto[:8]

        novo = texto
        if len(texto) > 3:
            novo = f"{texto[:2]}/{texto[2:4]}/{texto[4:]}"
        elif len(texto) > 1:
            novo = f"{texto[:2]}/{texto[2:]}"

        # verifica se uma barra foi inserida automaticamente
        adicionou_barra = len(novo) > len(var.get())

        if var.get() != novo:
            var.set(novo)

            # se inseriu uma barra, avança o cursor
            if adicionou_barra:
                pos += 1

            entry.icursor(min(pos, len(novo)))

    def _tipo_filtro_atual(self) -> str:
        return "recebidas" if self.tipo_notas_var.get().strip().lower().startswith("receb") else "emitidas"

    def _parse_data_br(self, s: str):
        s = s.strip()
        if not s:
            return None
        if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", s):
            messagebox.showerror("Data inválida", f"Use o formato dd/mm/aaaa: '{s}'")
            return None
        try:
            return datetime.strptime(s, "%d/%m/%Y").strftime("%Y-%m-%d")
        except ValueError:
            messagebox.showerror("Data inválida", f"A data '{s}' não existe.")
            return None

    def _refresh_table(self):
        ini = self._parse_data_br(self.data_ini_var.get()) if self.data_ini_var.get() else None
        fim = self._parse_data_br(self.data_fim_var.get()) if self.data_fim_var.get() else None
        
        cnpj = re.sub(r"\D", "", self.cnpj_var.get())

        self.notas_atuais = storage.listar_notas(
            ini,
            fim,
            cnpj=cnpj,
            tipo=self._tipo_filtro_atual(),
        )
        self._render_table()
        total = sum(float(n.valor_liquido or 0) for n in self.notas_atuais)
        canceladas = sum(1 for n in self.notas_atuais if n.situacao.lower() == "cancelada")
        resumo = f"{len(self.notas_atuais)} nota(s)  •  {self._fmt_moeda(total)}"
        if canceladas:
            resumo += f"  •  {canceladas} cancelada(s)"
        self.resumo_label.config(text=resumo)
        self.status_bar.config(text=f"{len(self.notas_atuais)} nota(s) no período selecionado.")

    def _render_table(self):
        """Aplica a busca rápida sem alterar o conjunto usado nas exportações."""
        if not hasattr(self, "tree"):
            return
        for row in self.tree.get_children():
            self.tree.delete(row)
        termo = self.busca_lista_var.get().strip().casefold() if hasattr(self, "busca_lista_var") else ""
        notas_exibidas = []
        for nota in self.notas_atuais:
            haystack = " ".join((
                nota.chave or "", nota.tomador_nome or "", nota.tomador_documento or "",
                nota.situacao or "", self._fmt_data_exibicao(nota.data_emissao),
            )).casefold()
            if termo and termo not in haystack:
                continue
            notas_exibidas.append(nota)
        for index, nota in enumerate(notas_exibidas):
            data_fmt = self._fmt_data_exibicao(nota.data_emissao)
            valor_fmt = self._fmt_moeda(nota.valor_liquido, prefixo=False)
            tags = ["even" if index % 2 == 0 else "odd"]
            if nota.situacao.lower() == "cancelada":
                tags.append("cancelada")
            self.tree.insert(
                "", END, iid=nota.chave,
                values=(data_fmt, nota.chave, nota.tomador_nome,
                        nota.tomador_documento, nota.situacao, valor_fmt),
                tags=tuple(tags),
            )
        if termo:
            self.status_bar.config(
                text=f"Exibindo {len(notas_exibidas)} de {len(self.notas_atuais)} nota(s). A exportação mantém todo o período."
            )

    @staticmethod
    def _fmt_moeda(valor: float, prefixo: bool = True) -> str:
        numero = f"{float(valor or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {numero}" if prefixo else numero

    @staticmethod
    def _fmt_data_exibicao(iso: str) -> str:
        if not iso:
            return ""
        try:
            return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return iso

    def _sort_by(self, col):
        reverse = getattr(self, f"_sort_rev_{col}", False)
        def sort_key(iid):
            value = self.tree.set(iid, col)
            if col == "valor":
                try:
                    return float(value.replace(".", "").replace(",", "."))
                except ValueError:
                    return 0.0
            if col == "data":
                try:
                    return datetime.strptime(value, "%d/%m/%Y")
                except ValueError:
                    return datetime.min
            return value.casefold()
        items = [(sort_key(k), k) for k in self.tree.get_children("")]
        items.sort(key=lambda item: item[0], reverse=reverse)
        for index, (_, k) in enumerate(items):
            self.tree.move(k, "", index)
        setattr(self, f"_sort_rev_{col}", not reverse)

    def _nota_selecionada(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return storage.buscar_nota(sel[0])

    def _on_select_row(self, event=None):
        nota = self._nota_selecionada()
        if not nota:
            return
        detalhe = (
            f"ID do documento\n{nota.chave}\n\n"
            f"Tomador\n{nota.tomador_nome or 'Não informado'}\n{nota.tomador_documento or 'Documento não informado'}\n\n"
            f"Emissão   {self._fmt_data_exibicao(nota.data_emissao)}\n"
            f"Situação  {nota.situacao}\n"
            f"Valor     {self._fmt_moeda(nota.valor_liquido)}"
        )
        self.detalhe_label.config(text=detalhe)
        xml_disponivel = bool(nota.xml_local_path and Path(nota.xml_local_path).exists())
        pdf_disponivel = bool(nota.pdf_local_path and Path(nota.pdf_local_path).exists())
        self.abrir_xml_btn.config(state="normal" if xml_disponivel else "disabled")
        self.abrir_pdf_btn.config(state="normal" if pdf_disponivel else "disabled")
        self.copiar_chave_btn.config(state="normal")
        if nota.xml_local_path and Path(nota.xml_local_path).exists():
            try:
                content = Path(nota.xml_local_path).read_text(encoding="utf-8", errors="replace")
                self._set_xml_preview(content)
            except Exception as e:
                self._set_xml_preview(f"Não foi possível ler o XML: {e}")
        else:
            self._set_xml_preview("XML ainda não disponível. Sincronize o período para baixar os arquivos.")

    def _set_xml_preview(self, content: str):
        self.xml_text.config(state="normal")
        self.xml_text.delete("1.0", END)
        if content:
            self.xml_text.insert("1.0", content)
        self.xml_text.config(state="disabled")

    def _copiar_chave_selecionada(self):
        nota = self._nota_selecionada()
        if not nota:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(nota.chave)
        self.status_bar.config(text="ID do documento copiado para a área de transferência.")

    def _abrir_arquivo_sistema(self, path: str):
        try:
            if sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", path])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                import os
                os.startfile(path)  # type: ignore
        except Exception as e:
            messagebox.showerror("Erro ao abrir", str(e))

    def _abrir_xml_selecionado(self):
        nota = self._nota_selecionada()
        if nota and nota.xml_local_path and Path(nota.xml_local_path).exists():
            self._abrir_arquivo_sistema(nota.xml_local_path)
        else:
            messagebox.showinfo("XML", "XML ainda não baixado para esta nota.")

    def _abrir_pdf_selecionado(self):
        nota = self._nota_selecionada()
        if nota and nota.pdf_local_path and Path(nota.pdf_local_path).exists():
            self._abrir_arquivo_sistema(nota.pdf_local_path)
        else:
            messagebox.showinfo("PDF", "PDF ainda não baixado para esta nota.")
