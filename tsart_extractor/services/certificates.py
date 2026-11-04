"""
Procura certificados A1 (.pfx / .p12) em dois lugares:

  1. Windows Certificate Store (CurrentUser\\My) — detectado automaticamente
     via PowerShell. Exporta os certificados exportáveis para arquivos .pfx
     temporários em config.CERT_TEMP_DIR; certificados não exportáveis ficam
     disponíveis pelo thumbprint para uso direto via Windows/SChannel.

  2. Pastas comuns do usuário (Desktop, Downloads, etc.) — busca por
     arquivos .pfx/.p12 no disco.

Certificados A3 (token físico/cartão, normalmente não exportáveis) são
listados e usados diretamente pelo repositório de certificados do Windows.
"""

import os
import sys
import base64
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List

from tsart_extractor.core import app_logging as logger
from tsart_extractor.core import settings as config


@dataclass
class CertEntry:
    """Representa um certificado disponível para uso no mTLS."""
    display_name: str        # texto exibido no combobox
    pfx_path: str            # caminho absoluto para .pfx (vazio = não exportável)
    pfx_password: str        # senha; vazio = usuário precisa digitar
    is_store_cert: bool = False
    thumbprint: str = ""
    exportable: bool = True
    cnpj: str = ""
    store_location: str = "CurrentUser"
    store_name: str = "My"
    has_private_key: bool = True
    is_expired: bool = False

    def __str__(self) -> str:
        return self.display_name


# Senha interna usada ao exportar certs do Store para .pfx temporário.
# O usuário nunca precisa digitá-la; ela é usada apenas internamente.
_TEMP_PASS = "NfseApp#2025!"


def extract_cnpj_from_text(text: str) -> str:
    """Extrai o primeiro CNPJ plausivel de um Subject/CN de certificado."""
    for match in re.finditer(r"\d{2}\D?\d{3}\D?\d{3}\D?\d{4}\D?\d{2}", text or ""):
        cnpj = re.sub(r"\D", "", match.group(0))
        if len(cnpj) == 14:
            return cnpj
    return ""


def find_certificates() -> List[CertEntry]:
    """
    Retorna lista combinada de certificados:
      - Certificados do Windows Certificate Store (CurrentUser\\My)
      - Arquivos .pfx/.p12 encontrados nas pastas em config.CERT_SEARCH_DIRS
    """
    results: List[CertEntry] = []
    seen: set = set()

    # ── 1. Windows Certificate Store (somente no Windows) ──────────────────
    if sys.platform == "win32":
        for entry in _find_windows_store_certs():
            key = entry.thumbprint or entry.pfx_path
            if key and key not in seen:
                seen.add(key)
                results.append(entry)

    # ── 2. Arquivos .pfx / .p12 em pastas comuns ───────────────────────────
    for base in config.CERT_SEARCH_DIRS:
        if not base.exists():
            continue
        base_depth = len(base.parts)
        for root_dir, dirs, files in os.walk(base):
            depth = len(Path(root_dir).parts) - base_depth
            if depth >= config.CERT_SEARCH_MAX_DEPTH:
                dirs[:] = []
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in ("node_modules", ".git")
            ]
            for fname in files:
                if fname.lower().endswith(config.CERT_EXTENSIONS):
                    p = Path(root_dir) / fname
                    s = str(p)
                    if s not in seen:
                        seen.add(s)
                        results.append(CertEntry(
                            display_name=f"\U0001f4c4 {fname}  ({p.parent})",
                            pfx_path=s,
                            pfx_password="",
                            is_store_cert=False,
                            exportable=True,
                        ))

    return results


def _find_windows_store_certs() -> List[CertEntry]:
    """
    Enumera certificados com chave privada do repositório CurrentUser\\My
    via PowerShell e tenta exportar cada um para um .pfx temporário.
    Certificados não exportáveis (A3/token) permanecem utilizáveis via
    thumbprint, sem arquivo .pfx.
    """
    ps_list = r"""
$specs = @(
    @{ Location = 'CurrentUser'; Name = 'My' },
    @{ Location = 'LocalMachine'; Name = 'My' },
    @{ Location = 'LocalMachine'; Name = 'WebHosting' }
)
$now = Get-Date
$items = foreach ($spec in $specs) {
    $location = [Enum]::Parse(
        [System.Security.Cryptography.X509Certificates.StoreLocation],
        $spec.Location
    )
    $store = [System.Security.Cryptography.X509Certificates.X509Store]::new(
        $spec.Name, $location
    )
    try {
        $flags = [System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly -bor
                 [System.Security.Cryptography.X509Certificates.OpenFlags]::OpenExistingOnly
        $store.Open($flags)
        foreach ($cert in $store.Certificates) {
            if ($cert.HasPrivateKey -and $cert.NotBefore -le $now -and $cert.NotAfter -gt $now) {
                [PSCustomObject]@{
                    Subject = $cert.Subject
                    Thumbprint = $cert.Thumbprint
                    FriendlyName = $cert.FriendlyName
                    NotAfter = $cert.NotAfter.ToString('yyyy-MM-dd')
                    StoreLocation = $spec.Location
                    StoreName = $spec.Name
                    HasPrivateKey = $true
                    IsExpired = $false
                }
            }
        }
    } catch {
        # Um store opcional pode não existir; os demais continuam sendo lidos.
    } finally {
        $store.Close()
    }
}
@($items) | ConvertTo-Json -Compress
"""
    try:
        proc = _run_powershell(ps_list, timeout=20)
        raw = (proc.stdout or "").lstrip("\ufeff").strip()
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or raw or "PowerShell encerrou com erro.").strip())
        if not raw:
            logger.log_info(
                "Busca de certificados",
                "Nenhum certificado utilizável foi encontrado nos repositórios do Windows.",
            )
            return []
        data = json.loads(raw)
        if data is None:
            return []
        if isinstance(data, dict):
            data = [data]
    except Exception as e:
        logger.log_error("PowerShell List Certificates", e)
        return []

    temp_dir = config.CERT_TEMP_DIR
    temp_dir.mkdir(parents=True, exist_ok=True)

    entries: List[CertEntry] = []

    for item in data:
        thumbprint = (item.get("Thumbprint") or "").strip()
        if not thumbprint:
            continue

        subject   = item.get("Subject") or ""
        friendly  = (item.get("FriendlyName") or "").strip()
        not_after = item.get("NotAfter") or ""
        store_location = item.get("StoreLocation") or "CurrentUser"
        store_name = item.get("StoreName") or "My"
        has_private_key = bool(item.get("HasPrivateKey"))
        is_expired = bool(item.get("IsExpired"))

        # Extrai CN do campo Subject
        cn = ""
        for part in subject.split(","):
            part = part.strip()
            if part.upper().startswith("CN="):
                cn = part[3:].strip()
                break

        # O restante do Subject pode conter o CNPJ da certificadora.
        cnpj = extract_cnpj_from_text(cn)

        label = friendly or cn or thumbprint[:16]

        m = re.search(r"(\d{4}-\d{2}-\d{2})", str(not_after))
        validade = m.group(1) if m else ""

        display_suffix = f"  (val. {validade})" if validade else ""

        if is_expired or not has_private_key:
            continue

        # Tenta exportar para .pfx temporário
        pfx_path = temp_dir / f"{store_location}_{store_name}_{thumbprint}.pfx"
        is_exportable = True

        if not pfx_path.exists():
            safe_path = str(pfx_path).replace("'", "''")
            ps_export = f"""
try {{
    $location = [System.Security.Cryptography.X509Certificates.StoreLocation]::{store_location}
    $store = [System.Security.Cryptography.X509Certificates.X509Store]::new(
        '{store_name}', $location
    )
    $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
    $cert = $store.Certificates.Find(
        [System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
        '{thumbprint}', $false
    )[0]
    if (-not $cert) {{ throw 'Certificado não localizado para exportação.' }}
    $bytes = $cert.Export(
        [System.Security.Cryptography.X509Certificates.X509ContentType]::Pfx,
        '{_TEMP_PASS}'
    )
    [System.IO.File]::WriteAllBytes('{safe_path}', $bytes)
    'OK'
}} catch {{
    'ERR:' + $_.Exception.Message
}} finally {{
    if ($store) {{ $store.Close() }}
}}
"""
            try:
                res = _run_powershell(ps_export, timeout=25)
                out = res.stdout.strip()
                if not out.startswith("OK") or not pfx_path.exists():
                    logger.log_info(
                        "Exportação de certificado",
                        f"{store_location}\\My\\{thumbprint}: {out or res.stderr.strip()}",
                    )
                    is_exportable = False
            except Exception as e:
                logger.log_error(f"PowerShell Export Certificate ({thumbprint})", e)
                is_exportable = False

        if is_exportable and pfx_path.exists():
            entries.append(CertEntry(
                display_name=f"\U0001f510 {label}{display_suffix}",
                pfx_path=str(pfx_path),
                pfx_password=_TEMP_PASS,
                is_store_cert=True,
                thumbprint=thumbprint,
                exportable=True,
                cnpj=cnpj,
                store_location=store_location,
                store_name=store_name,
                has_private_key=True,
                is_expired=False,
            ))
        else:
            # Certificado A3 (token físico) ou protegido — não exportável
            entries.append(CertEntry(
                display_name=f"\U0001f512 {label}{display_suffix}  [Windows Store \u2013 chave protegida]",
                pfx_path="",
                pfx_password="",
                is_store_cert=True,
                thumbprint=thumbprint,
                exportable=False,
                cnpj=cnpj,
                store_location=store_location,
                store_name=store_name,
                has_private_key=True,
                is_expired=False,
            ))

    return entries


def _run_powershell(script: str, timeout: int) -> subprocess.CompletedProcess:
    """Executa PowerShell sem perfil e devolve somente saída UTF-8 previsível."""
    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not executable:
        raise RuntimeError("PowerShell não encontrado no Windows.")
    prelude = (
        "$ErrorActionPreference='Stop';"
        "$ProgressPreference='SilentlyContinue';"
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
    )
    encoded = base64.b64encode((prelude + script).encode("utf-16le")).decode("ascii")
    return subprocess.run(
        [
            executable, "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
