"""Recursos visuais compartilhados por todas as janelas da aplicação."""

from tkinter import PhotoImage, Toplevel

from tsart_extractor.core import app_logging as logger
from tsart_extractor.core.settings import LOGO_PATH


class WindowingMixin:
    brand_logo: PhotoImage | None = None

    def _load_brand_assets(self) -> None:
        """Carrega uma única imagem e mantém referências para evitar coleta do Tk."""
        try:
            self.brand_logo = PhotoImage(file=str(LOGO_PATH))
            self.root.iconphoto(True, self.brand_logo)
        except Exception as exc:
            self.brand_logo = None
            logger.log_error("Carregamento da logo", exc)

    def _apply_window_icon(self, window) -> None:
        if self.brand_logo is not None:
            try:
                window.iconphoto(False, self.brand_logo)
            except Exception as exc:
                logger.log_error("Ícone da janela", exc)

    def _new_toplevel(self) -> Toplevel:
        """Cria janelas secundárias já vinculadas à identidade do aplicativo."""
        window = Toplevel(self.root)
        self._apply_window_icon(window)
        return window
