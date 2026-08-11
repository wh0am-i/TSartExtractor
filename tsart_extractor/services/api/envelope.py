"""Leitura defensiva do envelope JSON retornado pelo ADN."""

import base64
import gzip
import io
import json
import re
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree as ET

_NSU_KEY_CANDIDATES = ("ultnsu", "ultimonsu", "proximonsu", "nsu", "maxnsu")


def _write_debug_json(path: Path, payload, raw_text=None):
    """Grava a última resposta da API sem interferir na execução principal."""
    try:
        data = payload
        if data is None:
            data = {"raw_response": raw_text}

        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        # Nunca deixa o log quebrar a execução principal
        pass

def _extract_next_nsu(payload, fallback: int) -> int:
    """Procura recursivamente por uma chave que pareça indicar o próximo NSU."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k.lower() in _NSU_KEY_CANDIDATES:
                    try:
                        found.append(int(v))
                    except (TypeError, ValueError):
                        pass
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return max(found) if found else fallback


def _normalize_date_to_iso(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""

    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", value)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    return value[:10]


def _find_xml_payloads(payload) -> List[bytes]:
    """
    Nova versão Otimizada (Extração baseada no schema real do ADN).
    A API retorna os XMLs base64 dentro do campo 'ArquivoXml' do array 'LoteDFe'.
    """
    results = []

    def try_decode(s: str) -> Optional[bytes]:
        if not s or len(s) < 20:
            return None
        try:
            raw = base64.b64decode(s, validate=True)
            # tenta gzip primeiro (padrão usado na distribuição de DF-e)
            try:
                data = gzip.decompress(raw)
            except Exception:
                data = raw  # pode já vir sem compressão
            
            # Validação simples
            if data[:1] in (b"<",) or b"<NFSe" in data[:200] or b"<Evento" in data[:200] or b"<?xml" in data[:200]:
                return data
            return None
        except Exception:
            return None

    # Tenta ler no caminho exato documentado no teste de API
    if isinstance(payload, dict):
        lote = payload.get("LoteDFe", [])
        if isinstance(lote, list):
            for doc in lote:
                # O schema real da API joga a string em "ArquivoXml"
                base64_str = doc.get("ArquivoXml")
                if base64_str:
                    xml_bytes = try_decode(base64_str)
                    if xml_bytes:
                        results.append(xml_bytes)
                else:
                    # Em caso de fallback onde ArquivoXml não exista
                    pass

    return results

