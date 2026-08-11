"""Composição da janela principal a partir dos módulos de interface."""

import queue
import threading
from tkinter import Label, Tk
from tkinter import ttk

from tsart_extractor.persistence import repository as storage
from tsart_extractor.services.certificates import CertEntry
from tsart_extractor.services.nfse_api import NFSeClient
from tsart_extractor.ui.certificates import CertificateMixin
from tsart_extractor.ui.events import EventMixin
from tsart_extractor.ui.exports import ExportMixin
from tsart_extractor.ui.layout import LayoutMixin
from tsart_extractor.ui.notes import NotesMixin
from tsart_extractor.ui.progress_dialog import ProgressDialogMixin
from tsart_extractor.ui.synchronization import SynchronizationMixin
from tsart_extractor.ui.windowing import WindowingMixin


class App(
    WindowingMixin,
    ProgressDialogMixin,
    LayoutMixin,
    CertificateMixin,
    SynchronizationMixin,
    NotesMixin,
    ExportMixin,
    EventMixin,
):
    COLORS = {
        "canvas": "#F3F4F1",
        "surface": "#FFFFFF",
        "surface_alt": "#F8F9F7",
        "border": "#DDE1DA",
        "text": "#202522",
        "muted": "#66706A",
        "accent": "#315C4C",
        "accent_hover": "#25483C",
        "success": "#2F7A55",
        "warning": "#A66B16",
        "danger": "#A9433B",
        "selection": "#DDEBE5",
    }

    def __init__(self, root: Tk):
        self.root = root
        self.root.title("TSartExtractor - Notas Fiscais de Serviço")
        self.root.geometry("1360x820")
        self.root.minsize(1080, 680)
        self._load_brand_assets()

        self.client: NFSeClient | None = None
        self.gui_queue: queue.Queue = queue.Queue()
        self.notas_atuais = []
        self._cert_entries: list[CertEntry] = []

        self._busca_cancelar: threading.Event | None = None
        self._modal_top = None
        self._modal_progress: ttk.Progressbar | None = None
        self._modal_status: Label | ttk.Label | None = None
        self._modal_cancel_event: threading.Event | None = None
        self._modal_determinate = False
        self._export_cancelar: threading.Event | None = None

        self.sincronizado = False
        self.periodo_sincronizado: tuple[str, str] | None = None
        self._sync_periodo_atual: tuple[str, str] | None = None
        self._sync_signature_atual: tuple[str, str, str, str] | None = None
        self._last_sync_signature: tuple[str, str, str, str] | None = None
        self._last_sync_completed_at: float | None = None

        storage.init_db()
        self._build_layout()
        self._poll_queue()
        self._reload_certs()
