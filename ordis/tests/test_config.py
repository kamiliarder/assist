import os
import pathlib
import tempfile
import unittest
from unittest import mock

from ordis import config


class ConfigTests(unittest.TestCase):
    def load_with(self, toml_text=None, env=None):
        with tempfile.TemporaryDirectory() as d:
            if toml_text is not None:
                p = pathlib.Path(d) / "ordis"
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

    # -- base url

    def test_base_url_trailing_slash_is_stripped(self):
        cfg = self.load_with('base_url = "https://example.com/v1/"\n')
        self.assertEqual(cfg.base_url, "https://example.com/v1")
        self.assertEqual(cfg.warnings, [])

    def test_base_url_pasted_with_endpoint_suffix_is_fixed_with_a_note(self):
        cfg = self.load_with('base_url = "https://example.com/v1/chat/completions"\n')
        self.assertEqual(cfg.base_url, "https://example.com/v1")
        self.assertEqual(len(cfg.warnings), 1)
        self.assertIn("/chat/completions", cfg.warnings[0])

    def test_invalid_base_url_falls_back_to_default_with_warning(self):
        for bad in ("integrate.api.nvidia.com/v1", "ftp://x/v1", "https://", "https://x/v1?a=b"):
            cfg = self.load_with(f'base_url = "{bad}"\n')
            self.assertEqual(cfg.base_url, config.DEFAULT_BASE_URL, bad)
            self.assertEqual(len(cfg.warnings), 1, bad)

    def test_env_base_url_overrides_file_and_is_normalised(self):
        cfg = self.load_with('base_url = "https://file.example/v1"\n',
                             env={"NVIDIA_BASE_URL": "http://localhost:11434/v1/"})
        self.assertEqual(cfg.base_url, "http://localhost:11434/v1")

    def test_empty_env_base_url_is_ignored(self):
        cfg = self.load_with('base_url = "https://file.example/v1"\n', env={"NVIDIA_BASE_URL": ""})
        self.assertEqual(cfg.base_url, "https://file.example/v1")

    def test_plain_http_to_remote_host_warns_but_localhost_does_not(self):
        self.assertEqual(len(self.load_with('base_url = "http://example.com/v1"\n').warnings), 1)
        self.assertEqual(self.load_with('base_url = "http://localhost:8080/v1"\n').warnings, [])
        self.assertEqual(self.load_with('base_url = "http://127.0.0.1:8080/v1"\n').warnings, [])

    def test_requires_api_key_only_for_remote_servers(self):
        self.assertTrue(config.requires_api_key(config.Config()))
        self.assertFalse(config.requires_api_key(config.Config(base_url="http://localhost:11434/v1")))

    # -- system prompt settings

    def test_system_prompt_settings_load_and_are_type_checked(self):
        cfg = self.load_with('system_prompt = "hi {tools}"\nsystem_prompt_file = "~/p.md"\n'
                             'extra_system_prompt = "x"\n')
        self.assertEqual((cfg.system_prompt, cfg.system_prompt_file, cfg.extra_system_prompt),
                         ("hi {tools}", "~/p.md", "x"))
        cfg = self.load_with("system_prompt = 5\n")
        self.assertEqual(cfg.system_prompt, "")
        self.assertEqual(len(cfg.warnings), 1)

    # -- describe

    def test_describe_never_includes_the_key_and_reports_status(self):
        cfg = config.Config(system_prompt="secret prompt text")
        out = config.describe(cfg, "nvapi-SECRET")
        self.assertNotIn("nvapi-SECRET", out)
        self.assertNotIn("secret prompt text", out)
        self.assertIn("api key: set", out)
        self.assertIn("api key: MISSING", config.describe(cfg, None))

    def test_render_markdown_defaults_on_and_must_be_a_bool(self):
        self.assertTrue(self.load_with().render_markdown)
        self.assertFalse(self.load_with("render_markdown = false\n").render_markdown)
        cfg = self.load_with('render_markdown = "no"\n')
        self.assertTrue(cfg.render_markdown)
        self.assertEqual(len(cfg.warnings), 1)


if __name__ == "__main__":
    unittest.main()
