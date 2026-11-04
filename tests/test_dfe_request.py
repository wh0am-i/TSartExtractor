import base64
import gzip
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.x509.oid import NameOID

from tsart_extractor.core import settings
from tsart_extractor.services.api.client import NFSeClient, _WindowsStoreCertSession
from tsart_extractor.services.certificates import _find_windows_store_certs


class DfeRequestTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.enterContext(patch.object(settings, "DEBUG_DIR", Path(self.directory.name)))
        self.enterContext(patch("tsart_extractor.services.api.client.logger.log_info"))
        self.enterContext(patch("tsart_extractor.services.api.client.logger.log_error"))

    @staticmethod
    def response(status, payload):
        return Mock(status_code=status, text=json.dumps(payload), json=Mock(return_value=payload))

    def test_cnpj_is_sent_on_every_page_and_retry(self):
        xml = b'<NFSe><numero>1</numero></NFSe>'
        document = base64.b64encode(gzip.compress(xml)).decode("ascii")
        client = object.__new__(NFSeClient)
        client.client_cnpj = "54210849000119"
        client.session = Mock()
        client.session.get.side_effect = [
            self.response(503, {}),
            self.response(200, {"LoteDFe": [{"NSU": 101, "ArquivoXml": document}]}),
            self.response(200, {"LoteDFe": [{"NSU": 102, "ArquivoXml": document}]}),
            self.response(204, {}),
        ]

        with patch("tsart_extractor.services.api.client.time.sleep"):
            batches = list(client.iter_dfe_batches(last_nsu=100))

        self.assertEqual(batches, [(101, [xml]), (102, [xml])])
        calls = client.session.get.call_args_list
        self.assertEqual([call.args[0] for call in calls], [
            settings.DFE_ENDPOINT.format(nsu=nsu) for nsu in (100, 100, 101, 102)
        ])
        for call in calls:
            self.assertEqual(call.kwargs["params"], {
                "lote": True, "cnpjConsulta": "54210849000119",
            })

    @patch("tsart_extractor.services.api.client.subprocess.run")
    @patch("tsart_extractor.services.api.client.sys.platform", "win32")
    def test_windows_transport_includes_encoded_parameters(self, run_process):
        run_process.return_value = Mock(
            stdout=json.dumps({"ok": True, "status_code": 200, "text": "{}"}),
            stderr="",
        )
        session = _WindowsStoreCertSession("A" * 40)
        response = session.get(
            "https://adn.nfse.gov.br/contribuintes/DFe/0?existing=1",
            params={"lote": True, "cnpjConsulta": "54210849000119"},
        )
        script = base64.b64decode(run_process.call_args.args[0][-1]).decode("utf-16le")
        url = re.search(r"^\$url = '([^']+)'$", script, re.MULTILINE).group(1)
        self.assertEqual(parse_qs(urlsplit(url).query), {
            "existing": ["1"], "lote": ["True"], "cnpjConsulta": ["54210849000119"],
        })
        self.assertEqual(response.url, url)

    @patch("tsart_extractor.services.api.client.Pkcs12Adapter")
    @patch("cryptography.hazmat.primitives.serialization.pkcs12.load_key_and_certificates")
    def test_manual_pfx_uses_holder_cnpj_and_never_issuer_ou(self, load_pfx, adapter):
        pfx = Path(self.directory.name) / "certificate.pfx"
        pfx.write_bytes(b"mock PKCS12")
        for common_name, expected in (
            ("EMPRESA TESTE:54210849000119", "54210849000119"),
            ("PESSOA TESTE:12345678901", ""),
        ):
            with self.subTest(common_name=common_name):
                certificate = Mock(subject=x509.Name([
                    x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "31725974000166"),
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                ]))
                load_pfx.return_value = (None, certificate, None)
                client = NFSeClient(pfx_path=str(pfx), pfx_password="test")
                self.addCleanup(client.session.close)
                self.assertEqual(client.client_cnpj, expected)
                with patch.object(client.session, "get", return_value=self.response(204, {})) as get:
                    self.assertTrue(client.test_connection()[0])
                params = get.call_args.kwargs["params"]
                self.assertEqual(params.get("cnpjConsulta", ""), expected)
                self.assertNotIn("31725974000166", params.values())

    @patch("tsart_extractor.services.certificates._run_powershell")
    def test_windows_certificate_discovery_does_not_use_issuer_cnpj(self, run_powershell):
        items = [
            {
                "Thumbprint": "A" * 40,
                "Subject": "OU=31725974000166, CN=EMPRESA:54210849000119, O=ICP-Brasil",
                "HasPrivateKey": True,
            },
            {
                "Thumbprint": "B" * 40,
                "Subject": "CN=PESSOA:12345678901, OU=31725974000166, O=ICP-Brasil",
                "HasPrivateKey": True,
            },
        ]
        run_powershell.side_effect = [
            Mock(stdout=json.dumps(items), returncode=0),
            Mock(stdout="OK", returncode=0),
            Mock(stdout="OK", returncode=0),
        ]
        with patch.object(settings, "CERT_TEMP_DIR", Path(self.directory.name)):
            entries = _find_windows_store_certs()
        self.assertEqual([entry.cnpj for entry in entries], ["54210849000119", ""])


if __name__ == "__main__":
    unittest.main()
