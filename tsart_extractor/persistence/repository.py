"""
Camada de persistência local (SQLite) para as notas sincronizadas.
Guarda tudo localmente para que filtrar por período, reabrir o app, etc.
não exija bater na API do governo de novo.
"""

import sqlite3
import re
from dataclasses import dataclass, asdict
from typing import Optional, List
from datetime import datetime

from tsart_extractor.core.settings import DB_PATH


@dataclass
class Nota:
    chave: str
    nsu: int = 0
    tipo_documento: str = "NFSE"          # "NFSE" ou "EVENTO"
    cnpj_prestador: str = ""
    tomador_documento: str = ""
    tomador_nome: str = ""
    data_emissao: str = ""                # ISO "YYYY-MM-DD"
    situacao: str = "Emitida"             # "Emitida" | "Cancelada"
    valor_liquido: float = 0.0
    xml_local_path: Optional[str] = None
    pdf_local_path: Optional[str] = None


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_date_to_iso(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""

    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", value)
    if m:
        try:
            return datetime.strptime(m.group(0), "%d/%m/%Y").strftime("%Y-%m-%d")
        except ValueError:
            pass

    return value[:10]


def init_db():
    conn = _connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notas (
            chave TEXT PRIMARY KEY,
            nsu INTEGER,
            tipo_documento TEXT,
            cnpj_prestador TEXT,
            tomador_documento TEXT,
            tomador_nome TEXT,
            data_emissao TEXT,
            situacao TEXT,
            valor_liquido REAL,
            xml_local_path TEXT,
            pdf_local_path TEXT
        )
        """
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sync_state (chave TEXT PRIMARY KEY, valor TEXT)"
    )
    conn.commit()
    conn.close()


def _nsu_state_key(cnpj: str) -> str:
    """Cada contribuinte possui sua própria sequência de distribuição."""
    digits = re.sub(r"\D", "", cnpj or "")
    if len(digits) != 14:
        raise ValueError("CNPJ inválido para controlar o NSU.")
    # v2 invalida os estados que podiam ter sido gravados por uma conexão de
    # certificado antiga após o usuário trocar apenas a seleção na interface.
    return f"last_nsu_v2:{digits}"


def get_last_nsu(cnpj: str) -> int:
    conn = _connect()
    row = conn.execute(
        "SELECT valor FROM sync_state WHERE chave=?",
        (_nsu_state_key(cnpj),),
    ).fetchone()
    conn.close()
    return int(row["valor"]) if row else 0


def set_last_nsu(nsu: int, cnpj: str):
    conn = _connect()
    conn.execute(
        "INSERT INTO sync_state (chave, valor) VALUES (?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
        (_nsu_state_key(cnpj), str(nsu)),
    )
    conn.commit()
    conn.close()


def upsert_nota(nota: Nota):
    conn = _connect()
    nota.data_emissao = normalize_date_to_iso(nota.data_emissao)
    fields = asdict(nota)
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields.keys())
    updates = ", ".join(f"{k}=excluded.{k}" for k in fields.keys() if k != "chave")
    conn.execute(
        f"INSERT INTO notas ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(chave) DO UPDATE SET {updates}",
        fields,
    )
    conn.commit()
    conn.close()


def marcar_cancelada(chave: str):
    conn = _connect()
    conn.execute("UPDATE notas SET situacao='Cancelada' WHERE chave=?", (chave,))
    conn.commit()
    conn.close()


def set_paths(chave: str, xml_path: Optional[str] = None, pdf_path: Optional[str] = None):
    conn = _connect()
    if xml_path is not None:
        conn.execute("UPDATE notas SET xml_local_path=? WHERE chave=?", (xml_path, chave))
    if pdf_path is not None:
        conn.execute("UPDATE notas SET pdf_local_path=? WHERE chave=?", (pdf_path, chave))
    conn.commit()
    conn.close()


def listar_notas(
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    cnpj: Optional[str] = None,
    tipo: str = "emitidas",
) -> List[Nota]:
    """
    Lista NFSe por papel do CNPJ informado:
      - emitidas: CNPJ aparece como prestador/emissor
      - recebidas: CNPJ aparece como tomador
    Datas no formato ISO 'YYYY-MM-DD' (inclusive).
    """

    conn = _connect()
    data_expr = """
        CASE
            WHEN substr(data_emissao, 5, 1) = '-' THEN substr(data_emissao, 1, 10)
            WHEN substr(data_emissao, 3, 1) = '/' THEN
                substr(data_emissao, 7, 4) || '-' || substr(data_emissao, 4, 2) || '-' || substr(data_emissao, 1, 2)
            ELSE substr(data_emissao, 1, 10)
        END
    """

    query = """
            SELECT *
            FROM notas
            WHERE tipo_documento='NFSE'
            AND situacao IN ('Emitida', 'Cancelada')
        """

    params = []

    if data_inicio:
        query += f" AND {data_expr} >= ?"
        params.append(data_inicio)

    if data_fim:
        query += f" AND {data_expr} <= ?"
        params.append(data_fim)

    cnpj = (cnpj or "").strip()
    if cnpj:
        campo_doc = "tomador_documento" if tipo == "recebidas" else "cnpj_prestador"
        query += (
            " AND REPLACE(REPLACE(REPLACE("
            f"COALESCE({campo_doc}, ''),'.',''),'/',''),'-','') = ?"
        )
        params.append(cnpj)

    query += " ORDER BY data_emissao DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    return [
        Nota(**{k: row[k] for k in row.keys()})
        for row in rows
    ]


def buscar_nota(chave: str) -> Optional[Nota]:
    conn = _connect()
    row = conn.execute("SELECT * FROM notas WHERE chave=?", (chave,)).fetchone()
    conn.close()
    if not row:
        return None
    return Nota(**{k: row[k] for k in row.keys()})
