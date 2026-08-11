from datetime import datetime, timedelta
from tkinter import BOTH, BOTTOM, E, END, EW, HORIZONTAL, LEFT, RIGHT, StringVar, VERTICAL, W, X, Y, Scrollbar, Text
from tkinter import ttk


class LayoutMixin:
    def _build_layout(self):
        self._configure_styles()
        self.root.configure(bg=self.COLORS["canvas"])

        workspace = ttk.Frame(self.root, style="Canvas.TFrame", padding=(20, 16, 20, 10))
        workspace.pack(fill=BOTH, expand=True)

        # Cartão de acesso e identificação da empresa.
        top = ttk.Frame(workspace, style="Card.TFrame", padding=16)
        top.pack(fill=X, pady=(0, 10))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Acesso", style="Section.TLabel").grid(
            row=0, column=0, columnspan=7, sticky=W, pady=(0, 10)
        )

        ttk.Label(top, text="Certificado", style="Field.TLabel").grid(row=1, column=0, sticky=W)
        self.cert_var = StringVar()
        self.cert_combo = ttk.Combobox(top, textvariable=self.cert_var, width=52, state="readonly")
        self.cert_combo.grid(row=1, column=1, padx=(8, 8), sticky=EW)
        self.cert_combo.bind("<<ComboboxSelected>>", self._on_cert_selected)
        ttk.Button(top, text="Procurar…", command=self._browse_cert, style="Secondary.TButton").grid(
            row=1, column=2, padx=(0, 6))
        ttk.Button(top, text="Atualizar", command=self._reload_certs, style="Quiet.TButton").grid(
            row=1, column=3, padx=(0, 16))

        ttk.Label(top, text="Senha", style="Field.TLabel").grid(row=1, column=4, sticky=W)
        self.senha_var = StringVar()
        self.senha_entry = ttk.Entry(top, textvariable=self.senha_var, show="•", width=18)
        self.senha_entry.grid(row=1, column=5, padx=(8, 8))
        self.senha_entry.bind("<Return>", lambda _e: self._conectar())
        self.conectar_btn = ttk.Button(
            top, text="Conectar", command=self._conectar, style="Accent.TButton"
        )
        self.conectar_btn.grid(row=1, column=6)
        self.status_conexao = ttk.Label(
            top, text="●  Não conectado", style="Offline.TLabel", padding=(10, 5)
        )
        self.status_conexao.grid(row=1, column=7, padx=(8, 0), sticky=W)

        ttk.Separator(top).grid(row=2, column=0, columnspan=8, sticky=EW, pady=12)
        ttk.Label(top, text="Empresa", style="Field.TLabel").grid(row=3, column=0, sticky=W)
        self.empresa_var = StringVar()
        ttk.Entry(top, textvariable=self.empresa_var).grid(
            row=3, column=1, columnspan=2, padx=(8, 8), sticky=EW)
        ttk.Label(top, text="CNPJ", style="Field.TLabel").grid(row=3, column=3, sticky=E, padx=(8, 0))
        self.cnpj_var = StringVar()
        ttk.Entry(top, textvariable=self.cnpj_var, width=20).grid(row=3, column=4, padx=(8, 16))
        ttk.Label(top, text="Movimento", style="Field.TLabel").grid(row=3, column=5, sticky=E)
        self.tipo_notas_var = StringVar(value="Emitidas")
        self.tipo_notas_combo = ttk.Combobox(
            top, textvariable=self.tipo_notas_var, values=("Emitidas", "Recebidas"),
            width=13, state="readonly",
        )
        self.tipo_notas_combo.grid(row=3, column=6, padx=(8, 0), sticky=EW)
        self.tipo_notas_combo.bind("<<ComboboxSelected>>", self._on_filtro_tipo_alterado)

        # Período e ações principais ficam em uma barra própria.
        filtro = ttk.Frame(workspace, style="Card.TFrame", padding=14)
        filtro.pack(fill=X, pady=(0, 10))
        ttk.Label(filtro, text="Período", style="Section.TLabel").pack(side=LEFT, padx=(2, 16))
        ttk.Label(filtro, text="De", style="Field.TLabel").pack(side=LEFT)
        self.data_ini_var = StringVar()
        self.entry_data_ini = ttk.Entry(filtro, textvariable=self.data_ini_var, width=12)
        self.entry_data_ini.pack(side=LEFT, padx=(6, 10))
        self.entry_data_ini.bind(
            "<KeyRelease>",
            lambda e: self._formatar_data(self.data_ini_var, self.entry_data_ini)
        )

        ttk.Label(filtro, text="Até", style="Field.TLabel").pack(side=LEFT)
        self.data_fim_var = StringVar()
        self.entry_data_fim = ttk.Entry(filtro, textvariable=self.data_fim_var, width=12)
        self.entry_data_fim.pack(side=LEFT, padx=(6, 8))
        self.entry_data_fim.bind(
            "<KeyRelease>",
            lambda e: self._formatar_data(self.data_fim_var, self.entry_data_fim)
        )

        # Qualquer edição manual no período, depois de já ter sincronizado,
        # invalida a sincronização e bloqueia o "Baixar lote" de novo.
        self.data_ini_var.trace_add("write", self._on_periodo_alterado)
        self.data_fim_var.trace_add("write", self._on_periodo_alterado)

        ttk.Button(
            filtro, text="Mês passado", command=self._definir_periodo_mes_passado,
            style="Quiet.TButton",
        ).pack(side=LEFT, padx=(0, 10))
        self.buscar_btn = ttk.Button(
            filtro, text="Sincronizar notas", command=self._buscar_nfse,
            state="disabled", style="Accent.TButton",
        )
        self.buscar_btn.pack(side=LEFT)
        ttk.Button(filtro, text="Limpar", command=self._limpar_tela, style="Quiet.TButton").pack(
            side=LEFT, padx=(6, 0))
        ttk.Button(filtro, text="Resetar NSU", command=self._zerar_nsu, style="Danger.TButton").pack(
            side=RIGHT)

        # Corpo redimensionável: a tabela recebe o espaço principal.
        body = ttk.Panedwindow(workspace, orient=HORIZONTAL)
        body.pack(fill=BOTH, expand=True)
        table_card = ttk.Frame(body, style="Card.TFrame", padding=14)
        side = ttk.Frame(body, style="Card.TFrame", padding=16, width=370)
        body.add(table_card, weight=4)
        body.add(side, weight=2)

        table_head = ttk.Frame(table_card, style="Card.TFrame")
        table_head.pack(fill=X, pady=(0, 10))
        ttk.Label(table_head, text="Notas fiscais", style="Section.TLabel").pack(side=LEFT)
        self.resumo_label = ttk.Label(table_head, text="0 notas  •  R$ 0,00", style="Muted.TLabel")
        self.resumo_label.pack(side=LEFT, padx=(12, 0))
        self.busca_lista_var = StringVar()
        self.busca_lista_var.trace_add("write", lambda *_: self._render_table())
        ttk.Entry(table_head, textvariable=self.busca_lista_var, width=28).pack(side=RIGHT)
        ttk.Label(table_head, text="Buscar", style="Field.TLabel").pack(side=RIGHT, padx=(0, 8))

        table_frame = ttk.Frame(table_card, style="Card.TFrame")
        table_frame.pack(fill=BOTH, expand=True)

        cols = ("data", "chave", "tomador", "documento", "situacao", "valor")
        self.tree = ttk.Treeview(
            table_frame, columns=cols, show="headings", selectmode="browse", style="Office.Treeview")
        headers = {
            "data": "Data",
            "chave": "ID Documento",
            "tomador": "Nome Tomador",
            "documento": "CPF/CNPJ Tomador",
            "situacao": "Situação",
            "valor": "Valor (R$)",
        }
        widths = {
            "data": 96, "chave": 210, "tomador": 230,
            "documento": 135, "situacao": 95, "valor": 105,
        }
        for c in cols:
            self.tree.heading(c, text=headers[c], command=lambda c=c: self._sort_by(c))
            anchor = E if c == "valor" else W
            self.tree.column(c, width=widths[c], anchor=anchor, stretch=c in ("chave", "tomador"))

        vsb = Scrollbar(table_frame, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        vsb.pack(side=LEFT, fill=Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_row)
        self.tree.bind("<Double-1>", lambda _e: self._abrir_pdf_selecionado())
        self.tree.tag_configure("even", background=self.COLORS["surface"])
        self.tree.tag_configure("odd", background=self.COLORS["surface_alt"])
        self.tree.tag_configure("cancelada", foreground=self.COLORS["danger"])

        ttk.Label(side, text="Detalhes da nota", style="Section.TLabel").pack(anchor=W)
        self.detalhe_label = ttk.Label(
            side, text="Selecione uma nota para consultar os detalhes.",
            style="Detail.TLabel", justify=LEFT, wraplength=340,
        )
        self.detalhe_label.pack(anchor=W, fill=X, pady=(10, 12))

        btns = ttk.Frame(side, style="Card.TFrame")
        btns.pack(anchor=W, pady=(0, 14))
        self.abrir_pdf_btn = ttk.Button(
            btns, text="Abrir PDF", command=self._abrir_pdf_selecionado,
            state="disabled", style="Secondary.TButton",
        )
        self.abrir_pdf_btn.pack(side=LEFT, padx=(0, 6))
        self.abrir_xml_btn = ttk.Button(
            btns, text="Abrir XML", command=self._abrir_xml_selecionado,
            state="disabled", style="Quiet.TButton",
        )
        self.abrir_xml_btn.pack(side=LEFT, padx=(0, 6))
        self.copiar_chave_btn = ttk.Button(
            btns, text="Copiar ID", command=self._copiar_chave_selecionada,
            state="disabled", style="Quiet.TButton",
        )
        self.copiar_chave_btn.pack(side=LEFT)

        ttk.Label(side, text="Prévia do XML", style="Field.TLabel").pack(anchor=W, pady=(0, 6))
        xml_frame = ttk.Frame(side, style="Card.TFrame")
        xml_frame.pack(fill=BOTH, expand=True)
        self.xml_text = Text(
            xml_frame, wrap="word", height=20, bg="#F7F8F6", fg=self.COLORS["text"],
            relief="flat", borderwidth=0, padx=10, pady=10, font=("Consolas", 9),
            insertbackground=self.COLORS["text"],
        )
        xsb_y = Scrollbar(xml_frame, orient=VERTICAL, command=self.xml_text.yview)
        self.xml_text.configure(yscrollcommand=xsb_y.set)
        xsb_y.pack(side=RIGHT, fill=Y)
        self.xml_text.pack(fill=BOTH, expand=True)
        self.xml_text.config(state="disabled")

        # Ações de saída e progresso permanecem visíveis, sem competir com o filtro.
        actions = ttk.Frame(workspace, style="Canvas.TFrame", padding=(0, 10, 0, 0))
        # Mantém as ações antes do painel expansível na ordem do pack. Assim
        # elas ficam sempre visíveis, inclusive com escala de tela de 150%.
        actions.pack(fill=X, before=body)
        self.baixar_lote_btn_xml = ttk.Button(
            actions, text="Exportar XML (ZIP)", command=self._baixar_lote_xml,
            state="disabled", style="Secondary.TButton",
        )
        self.baixar_lote_btn_xml.pack(side=LEFT)
        self.baixar_lote_btn_pdf = ttk.Button(
            actions, text="Exportar PDF (ZIP)", command=self._baixar_lote_pdf,
            state="disabled", style="Secondary.TButton",
        )
        self.baixar_lote_btn_pdf.pack(side=LEFT, padx=6)
        self.relatorio_btn = ttk.Button(
            actions, text="Gerar relatório", command=self._gerar_relatorio,
            style="Secondary.TButton",
        )
        self.relatorio_btn.pack(side=LEFT)
        self.status_bar = ttk.Label(
            self.root, text="Selecione um certificado para começar.",
            style="Status.TLabel", anchor=W, padding=(20, 9),
        )
        self.status_bar.pack(side=BOTTOM, fill=X)

        self.root.bind("<Control-Return>", lambda _e: self._buscar_nfse())
        self.root.bind("<Control-l>", lambda _e: self._limpar_tela())
        self.root.bind("<F5>", lambda _e: self._reload_certs())

    def _configure_styles(self):
        """Centraliza a linguagem visual sem adicionar dependências externas."""
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        c = self.COLORS
        # option_add recebe uma lista Tcl, portanto famílias com espaço precisam
        # estar entre chaves. Sem isso, "UI" é interpretado como o tamanho.
        self.root.option_add("*Font", "{Segoe UI} 10")
        self.root.option_add("*TCombobox*Listbox.font", "{Segoe UI} 10")
        style.configure("Canvas.TFrame", background=c["canvas"])
        style.configure("Card.TFrame", background=c["surface"])
        style.configure("Offline.TLabel", background="#3A2C2B", foreground="#F0B7B2", font=("Segoe UI Semibold", 9))
        style.configure("Online.TLabel", background="#223D33", foreground="#A8D5BE", font=("Segoe UI Semibold", 9))
        style.configure("Connecting.TLabel", background="#493B24", foreground="#E8CB94", font=("Segoe UI Semibold", 9))
        style.configure("Section.TLabel", background=c["surface"], foreground=c["text"], font=("Segoe UI Semibold", 11))
        style.configure("Field.TLabel", background=c["surface"], foreground=c["muted"], font=("Segoe UI", 9))
        style.configure("Muted.TLabel", background=c["surface"], foreground=c["muted"])
        style.configure("Detail.TLabel", background=c["surface"], foreground=c["text"], lineheight=4)
        style.configure("Status.TLabel", background="#E7EAE5", foreground=c["muted"])
        style.configure("TEntry", fieldbackground="#FBFCFA", bordercolor=c["border"], padding=7)
        style.configure("TCombobox", fieldbackground="#FBFCFA", bordercolor=c["border"], padding=6)
        style.configure("Accent.TButton", background=c["accent"], foreground="#FFFFFF", padding=(14, 8), borderwidth=0, font=("Segoe UI Semibold", 9))
        style.map("Accent.TButton", background=[("active", c["accent_hover"]), ("disabled", "#AAB5B0")])
        style.configure("Secondary.TButton", background="#E5ECE8", foreground=c["accent"], padding=(12, 7), borderwidth=0)
        style.map(
            "Secondary.TButton",
            background=[("active", "#D7E2DD"), ("disabled", "#E8ECE9")],
            foreground=[("disabled", "#87918C")],
        )
        style.configure("Quiet.TButton", background=c["surface"], foreground=c["muted"], padding=(10, 7), borderwidth=0)
        style.map("Quiet.TButton", background=[("active", "#EFF1EE")], foreground=[("active", c["text"])])
        style.configure("Danger.TButton", background=c["surface"], foreground=c["danger"], padding=(10, 7), borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#FAECEA")])
        style.configure("Office.Treeview", background=c["surface"], fieldbackground=c["surface"], foreground=c["text"], rowheight=31, borderwidth=0)
        style.configure("Office.Treeview.Heading", background="#ECEFEB", foreground=c["muted"], relief="flat", padding=(8, 8), font=("Segoe UI Semibold", 9))
        style.map("Office.Treeview", background=[("selected", c["selection"])], foreground=[("selected", c["text"])])
        style.map("Office.Treeview.Heading", background=[("active", "#E2E6E1")])
        style.configure(
            "Green.Horizontal.TProgressbar",
            background=c["success"],
            troughcolor="#E2E8E4",
            bordercolor="#E2E8E4",
            lightcolor=c["success"],
            darkcolor=c["success"],
            thickness=10,
        )

    def _definir_periodo_mes_passado(self):
        hoje = datetime.now()
        ultimo_dia = hoje.replace(day=1) - timedelta(days=1)
        primeiro_dia = ultimo_dia.replace(day=1)
        self.data_ini_var.set(primeiro_dia.strftime("%d/%m/%Y"))
        self.data_fim_var.set(ultimo_dia.strftime("%d/%m/%Y"))
        self.status_bar.config(text="Período definido para o mês anterior completo.")
