"""Inicialização da aplicação desktop."""

from tkinter import Tk

from tsart_extractor.ui.app import App


def run() -> None:
    root = Tk()
    App(root)
    root.mainloop()
