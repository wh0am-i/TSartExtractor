from io import BytesIO
from pathlib import Path
from tkinter import LEFT, RIGHT, W, X, StringVar, filedialog, messagebox, ttk

from PIL import Image, ImageTk

from tsart_extractor.services.cabecalho_pdf import (
    LIMITE_TEXTO, PreferenciasCabecalhoPDF, salvar_cabecalho,
)


class CabecalhoPDFMixin:
    def _editar_cabecalho_pdf(self):
        janela = self._new_toplevel()
        janela.title("Cabeçalho das notas em PDF")
        janela.transient(self.root)
        janela.resizable(False, False)

        painel = ttk.Frame(janela, style="Card.TFrame", padding=18)
        painel.pack(fill=X)
        texto = StringVar(master=janela, value=self.cabecalho_pdf.texto)
        preferencias = self.cabecalho_pdf

        ttk.Label(painel, text=f"Texto (até {LIMITE_TEXTO} caracteres)", style="Field.TLabel").pack(anchor=W)
        campo = ttk.Entry(painel, textvariable=texto, width=46)
        campo.pack(fill=X, pady=(4, 12))

        previa = ttk.Label(painel, style="Field.TLabel")
        previa.pack(anchor=W)
        origem = ttk.Label(painel, wraplength=340, style="Muted.TLabel")
        origem.pack(anchor=W)

        def mostrar_logo(logo_png: bytes, erro: str = ""):
            foto = None
            if logo_png:
                with Image.open(BytesIO(logo_png)) as imagem:
                    imagem.thumbnail((144, 80))
                    foto = ImageTk.PhotoImage(imagem, master=janela)
            previa.configure(image=foto or "", text="" if foto or erro else "Sem logo")
            previa.image = foto
            caminho = preferencias.caminho_logo
            origem.configure(text=erro or (str(caminho) if caminho else ""))

        def escolher_logo():
            nonlocal preferencias
            caminho = filedialog.askopenfilename(
                parent=janela, title="Escolher logo",
                filetypes=[("Imagens PNG ou JPEG", ("*.png", "*.jpg", "*.jpeg"))],
            )
            if caminho:
                escolha = PreferenciasCabecalhoPDF(caminho_logo=Path(caminho).absolute())
                cabecalho, erro = escolha.preparar()
                if erro:
                    messagebox.showerror("Logo inválida", erro, parent=janela)
                else:
                    preferencias = escolha
                    mostrar_logo(cabecalho.logo_png)

        def remover_logo():
            nonlocal preferencias
            preferencias = PreferenciasCabecalhoPDF()
            mostrar_logo(b"")

        def restaurar():
            texto.set(PreferenciasCabecalhoPDF().texto)
            remover_logo()

        def salvar():
            valor = texto.get().strip()
            if len(valor) > LIMITE_TEXTO:
                messagebox.showwarning("Texto muito longo", f"Use até {LIMITE_TEXTO} caracteres.", parent=janela)
                return
            cabecalho = PreferenciasCabecalhoPDF(valor, preferencias.caminho_logo, preferencias.logo_legado)
            try:
                salvar_cabecalho(cabecalho)
            except OSError as exc:
                messagebox.showerror("Erro ao salvar o cabeçalho", str(exc), parent=janela)
                return
            self.cabecalho_pdf = cabecalho
            self.status_bar.configure(text="Cabeçalho salvo para os próximos PDFs exportados.")
            janela.destroy()

        logos = ttk.Frame(painel, style="Card.TFrame")
        logos.pack(fill=X, pady=(6, 12))
        ttk.Button(logos, text="Escolher logo…", command=escolher_logo, style="Secondary.TButton").pack(side=LEFT)
        ttk.Button(logos, text="Remover logo", command=remover_logo, style="Quiet.TButton").pack(side=LEFT, padx=6)

        acoes = ttk.Frame(painel, style="Card.TFrame")
        acoes.pack(fill=X)
        ttk.Button(acoes, text="Restaurar padrão", command=restaurar, style="Quiet.TButton").pack(side=LEFT)
        ttk.Button(acoes, text="Salvar", command=salvar, style="Accent.TButton").pack(side=RIGHT)
        ttk.Button(acoes, text="Cancelar", command=janela.destroy, style="Quiet.TButton").pack(side=RIGHT, padx=6)

        cabecalho, erro = preferencias.preparar()
        mostrar_logo(cabecalho.logo_png, erro)
        janela.bind("<Escape>", lambda _event: janela.destroy())
        janela.wait_visibility()
        janela.grab_set()
        campo.focus_set()
