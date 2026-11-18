import os
import pathlib
import tempfile
import unittest
from unittest import mock

from desk_ai import config


class ConfigTests(unittest.TestCase):
    def load_with(self, toml_text=None, env=None):
        with tempfile.TemporaryDirectory() as d:
            if toml_text is not None:
                p = pathlib.Path(d) / "desk-ai"
                p.mkdir()
                (p / "config.toml").write_text(toml_text)
            clean = {k: v for k, v in os.environ.items() if not k.startswith("NVIDIA_")}
            clean["XDG_CONFIG_HOME"] = d
            clean.update(env or {})
            with mock.patch.dict(os.environ, clean, clear=True):
                return config.load()

    def test_defaults_without_file(self):
        cfg = self.load_with()
        self.assertEqual(cfg.max_attempts, 4)
        self.assertEqual(cfg.warnings, [])

    def test_file_overrides_defaults(self):
        cfg = self.load_with('model = "x/y"\ntemperature = 1\nfallback_models = ["a", "b"]\n')
        self.assertEqual(cfg.model, "x/y")
        self.assertEqual(cfg.temperature, 1.0)
        self.assertIsInstance(cfg.temperature, float)
        self.assertEqual(cfg.fallback_models, ["a", "b"])

    def test_env_overrides_file(self):
        cfg = self.load_with('model = "from-file"\n',
                             env={"NVIDIA_MODEL": "from-env", "NVIDIA_FALLBACK_MODELS": "a, b ,"})
        self.assertEqual(cfg.model, "from-env")
        self.assertEqual(cfg.fallback_models, ["a", "b"])

    def test_unknown_and_wrong_type_warn_but_do_not_crash(self):
        cfg = self.load_with('nope = 1\nmax_attempts = "many"\nwarnings = ["x"]\n')
        self.assertEqual(cfg.max_attempts, 4)
        self.assertEqual(len(cfg.warnings), 3)

    def test_bool_is_not_an_int(self):
        cfg = self.load_with("max_attempts = true\n")
        self.assertEqual(cfg.max_attempts, 4)
        self.assertEqual(len(cfg.warnings), 1)

    def test_broken_toml_falls_back_to_defaults(self):
        cfg = self.load_with("this is = = not toml")
        self.assertEqual(cfg.model, config.Config().model)
        self.assertTrue(cfg.warnings)

    def test_api_key_env_then_file(self):
        with tempfile.TemporaryDirectory() as d:
            keyfile = pathlib.Path(d) / "k"
            keyfile.write_text("nvapi-file\n")
            cfg = config.Config(api_key_file=str(keyfile))
            with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-env"}):
                self.assertEqual(config.load_api_key(cfg), "nvapi-env")
            env = {k: v for k, v in os.environ.items() if k != "NVIDIA_API_KEY"}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(config.load_api_key(cfg), "nvapi-file")


if __name__ == "__main__":
    unittest.main()
