import tempfile
import unittest
from pathlib import Path

from tsart_extractor.persistence import repository


class NsuPorCnpjTest(unittest.TestCase):
    def test_separa_estado_de_cada_empresa(self):
        with tempfile.TemporaryDirectory() as directory:
            original = repository.DB_PATH
            repository.DB_PATH = Path(directory) / "notas.db"
            try:
                repository.init_db()
                repository.set_last_nsu(283, "54.210.849/0001-19")
                repository.set_last_nsu(313, "58.616.851/0001-43")

                self.assertEqual(repository.get_last_nsu("54210849000119"), 283)
                self.assertEqual(repository.get_last_nsu("58616851000143"), 313)
                self.assertEqual(repository.get_last_nsu("13347016000117"), 0)
            finally:
                repository.DB_PATH = original


if __name__ == "__main__":
    unittest.main()
