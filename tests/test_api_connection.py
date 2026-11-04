import json
import unittest
from unittest.mock import Mock, patch

import requests

from tsart_extractor.core import settings
from tsart_extractor.services.api.client import NFSeClient, _WindowsStoreCertSession


class ApiConnectionTest(unittest.TestCase):
    def make_client(self, status_code, payload):
        response = Mock()
        response.status_code = status_code
        response.text = json.dumps(payload, ensure_ascii=False)
        response.json.return_value = payload

        client = object.__new__(NFSeClient)
        client.session = Mock()
        client.session.get.return_value = response
        client.client_cnpj = ""
        return client

    def test_preserves_api_rejection_response(self):
        client = self.make_client(
            400,
            {
                "StatusProcessamento": "REJEICAO",
                "Erros": [
                    {
                        "Codigo": "E2213",
                        "Descricao": "Certificado de transmissão expirado.",
                    }
                ],
            },
        )

        ok, message = client.test_connection()

        self.assertFalse(ok)
        self.assertIn('"Codigo": "E2213"', message)
        self.assertIn("Certificado de transmissão expirado", message)

    def test_preserves_rejection_response_without_error_details(self):
        client = self.make_client(
            400,
            {
                "TipoConsulta": "InscricaoCertificado",
                "StatusProcessamento": "REJEICAO",
                "Erros": [],
                "TipoAmbiente": "PRODUCAO",
                "VersaoAplicativo": "1.0.0.0",
                "DataHoraProcessamento": "2026-11-03T11:06:50-03:00",
            },
        )

        ok, message = client.test_connection()
        self.assertFalse(ok)
        self.assertFalse(ok)
        self.assertIn('"Erros": []', message)
        self.assertIn('"VersaoAplicativo": "1.0.0.0"', message)
        self.assertIn('"DataHoraProcessamento": "2026-11-03T11:06:50-03:00"', message)

    def test_connection_timeout_is_bounded_and_not_retried(self):
        client = object.__new__(NFSeClient)
        client.session = Mock()
        client.client_cnpj = ""
        client.session.get.side_effect = requests.exceptions.Timeout("tempo esgotado")

        ok, message = client.test_connection()

        self.assertFalse(ok)
        self.assertIn("Tempo limite após", message)
        client.session.get.assert_called_once()
        self.assertEqual(
            client.session.get.call_args.kwargs["timeout"],
            settings.HTTP_TIMEOUT,
        )
        self.assertEqual(
            client.session.get.call_args.args[0],
            settings.DFE_ENDPOINT.format(nsu=0),
        )
        self.assertEqual(
            client.session.get.call_args.kwargs["params"],
            {"lote": True},
        )

    def test_connection_sends_cnpj_when_available(self):
        client = self.make_client(
            200,
            {"StatusProcessamento": "DOCUMENTOS_LOCALIZADOS"},
        )
        client.client_cnpj = "54210849000119"

        ok, _ = client.test_connection()

        self.assertTrue(ok)
        self.assertEqual(
            client.session.get.call_args.kwargs["params"],
            {"lote": True, "cnpjConsulta": "54210849000119"},
        )

    @patch("tsart_extractor.services.api.client.subprocess.run")
    def test_windows_store_cancellation_is_reported_as_timeout(self, run_process):
        run_process.return_value = Mock(
            stdout=json.dumps(
                {
                    "ok": False,
                    "error_type": "System.Threading.Tasks.TaskCanceledException",
                    "error": "Uma tarefa foi cancelada.",
                }
            ),
            stderr="",
        )
        session = _WindowsStoreCertSession("A" * 40)

        with self.assertRaises(requests.exceptions.Timeout) as context:
            session.get("https://adn.nfse.gov.br/contribuintes/DFe/0", timeout=30)

        self.assertIn("excedeu 30 segundos", str(context.exception))


if __name__ == "__main__":
    unittest.main()
