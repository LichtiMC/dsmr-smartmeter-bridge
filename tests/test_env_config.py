import tempfile
import unittest
from pathlib import Path

import decrypt


class EnvConfigTests(unittest.TestCase):
    def test_load_env_file_reads_simple_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "GUEK=abc123\n"
                "GAK=def456\n"
                "SERIAL_INPUT_PORT=socket://example:5000\n"
                "DEBUG=true\n",
                encoding="utf-8",
            )

            values = decrypt.load_env_file(env_path)

            self.assertEqual(values["GUEK"], "abc123")
            self.assertEqual(values["GAK"], "def456")
            self.assertEqual(values["SERIAL_INPUT_PORT"], "socket://example:5000")
            self.assertEqual(values["DEBUG"], "true")


if __name__ == "__main__":
    unittest.main()
