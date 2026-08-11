"""
Módulo de log centralizado.

Todos os erros são gravados com timestamp em ``var/logs/app.log``.
"""

from datetime import datetime
from tsart_extractor.core.settings import LOG_DIR

LOG_FILE = LOG_DIR / "app.log"


def log_error(contexto: str, erro: Exception | str) -> None:
    """
    Grava uma linha de erro no arquivo log.txt.

    Args:
        contexto: Descrição de onde ocorreu o erro (ex: 'Download XML', 'Conexão').
        erro: A exceção ou mensagem de erro a registrar.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mensagem = str(erro)
    linha = f"[{timestamp}] [{contexto}] {mensagem}\n"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(linha)
    except Exception:
        pass  # nunca deve travar o programa por causa do log


def log_info(contexto: str, mensagem: str) -> None:
    """Grava uma linha informativa no arquivo log.txt."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linha = f"[{timestamp}] [INFO/{contexto}] {mensagem}\n"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(linha)
    except Exception:
        pass
