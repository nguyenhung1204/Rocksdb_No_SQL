import unittest

import app as app_module


class ServerOptionsTest(unittest.TestCase):
    def server_options(self, environ):
        options = getattr(app_module, "server_options", None)
        self.assertTrue(callable(options), "app.py should expose validated Waitress settings")
        return options(environ)

    def test_server_defaults_are_local_and_bounded(self):
        self.assertEqual(self.server_options({}), {
            "host": "127.0.0.1", "port": 5000, "threads": 8,
        })

    def test_server_settings_can_be_configured(self):
        self.assertEqual(self.server_options({
            "LOGSTORE_HOST": "0.0.0.0", "LOGSTORE_PORT": "5050", "LOGSTORE_THREADS": "12",
        }), {"host": "0.0.0.0", "port": 5050, "threads": 12})

    def test_invalid_server_settings_are_rejected(self):
        for name, value in (
            ("LOGSTORE_HOST", " "),
            ("LOGSTORE_PORT", "abc"),
            ("LOGSTORE_PORT", "0"),
            ("LOGSTORE_PORT", "65536"),
            ("LOGSTORE_THREADS", "zero"),
            ("LOGSTORE_THREADS", "0"),
            ("LOGSTORE_THREADS", "65"),
        ):
            with self.subTest(name=name, value=value):
                self.assertRaises(ValueError, self.server_options, {name: value})
