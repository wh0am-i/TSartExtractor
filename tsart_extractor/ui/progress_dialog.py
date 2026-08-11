"""Janela modal única para operações demoradas da interface."""

from tkinter import BOTH, W, X
from tkinter import ttk


class ProgressDialogMixin:
    def _open_progress_dialog(
        self,
        title: str,
        heading: str,
        detail: str,
        cancel_event,
        *,
        determinate: bool = False,
        total: int = 100,
    ):
        self._close_progress_dialog()

        top = self._new_toplevel()
        top.title(title)
        top.resizable(False, False)
        top.transient(self.root)
        top.configure(bg=self.COLORS["canvas"])

        panel = ttk.Frame(top, style="Card.TFrame", padding=22)
        panel.pack(fill=BOTH, expand=True, padx=12, pady=12)
        ttk.Label(panel, text=heading, style="Section.TLabel").pack(anchor=W)

        status = ttk.Label(panel, text=detail, style="Muted.TLabel")
        status.pack(anchor=W, pady=(6, 16))

        mode = "determinate" if determinate else "indeterminate"
        progress = ttk.Progressbar(
            panel,
            mode=mode,
            maximum=max(total, 1),
            style="Green.Horizontal.TProgressbar",
        )
        progress.pack(fill=X)
        if not determinate:
            progress.start(10)

        cancel_button = ttk.Button(panel, text="Cancelar", style="Secondary.TButton")
        cancel_button.config(
            command=lambda: self._cancel_progress_dialog(cancel_button)
        )
        cancel_button.pack(fill=X, pady=(16, 0))
        top.protocol(
            "WM_DELETE_WINDOW",
            lambda: self._cancel_progress_dialog(cancel_button),
        )

        self._modal_top = top
        self._modal_progress = progress
        self._modal_status = status
        self._modal_cancel_event = cancel_event
        self._modal_determinate = determinate

        # Usa o tamanho solicitado pelos widgets. Uma altura fixa cortava o
        # botão Cancelar no Windows quando a escala de tela estava em 125/150%.
        top.update_idletasks()
        width = max(500, top.winfo_reqwidth())
        height = max(255, top.winfo_reqheight())
        rx = self.root.winfo_x() + (self.root.winfo_width() - width) // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - height) // 2
        top.geometry(f"{width}x{height}+{max(rx, 0)}+{max(ry, 0)}")
        top.minsize(width, height)
        top.grab_set()

    def _update_progress_dialog(self, current: int, total: int, detail: str):
        if self._modal_progress:
            if not self._modal_determinate:
                self._modal_progress.stop()
                self._modal_progress.config(mode="determinate", value=0)
                self._modal_determinate = True
            self._modal_progress.config(maximum=max(total, 1), value=current)
        if self._modal_status:
            self._modal_status.config(text=detail)

    def _cancel_progress_dialog(self, button=None):
        if self._modal_cancel_event:
            self._modal_cancel_event.set()
        if button:
            button.config(state="disabled", text="Cancelando...")
        if self._modal_status:
            self._modal_status.config(text="Encerrando a operação com segurança…")

    def _close_progress_dialog(self):
        if self._modal_top:
            try:
                if self._modal_progress:
                    self._modal_progress.stop()
                self._modal_top.grab_release()
                self._modal_top.destroy()
            except Exception:
                pass
        self._modal_top = None
        self._modal_progress = None
        self._modal_status = None
        self._modal_cancel_event = None
        self._modal_determinate = False
