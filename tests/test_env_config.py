import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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

    def test_process_reconnects_after_repeated_empty_reads(self):
        decoder = decrypt.SmartMeterDecryptor()
        decoder._args = SimpleNamespace(serial_input_port="socket://example:5000", baudrate=115200)
        decoder._empty_reads = 1
        decoder._max_empty_reads_before_reconnect = 1

        class FakeConnection:
            def read(self, size):
                return b""

            def close(self):
                return None

        class ReplacementConnection:
            def read(self, size):
                return b""

            def close(self):
                return None

        decoder._connection = FakeConnection()
        reconnect_attempts = []

        def fake_connect():
            reconnect_attempts.append(True)
            decoder._connection = ReplacementConnection()
            return True

        decoder.connect = fake_connect
        decoder.process()

        self.assertEqual(len(reconnect_attempts), 1)
        self.assertIsInstance(decoder._connection, ReplacementConnection)


if __name__ == "__main__":
    unittest.main()
