import datetime
import pathlib
import tempfile
import unittest

from ordis import prompt
from ordis.config import Config

NOW = datetime.datetime(2026, 11, 18, 9, 0)
TOOLS = ["open_app", "run_command"]


class PromptTests(unittest.TestCase):
    def build(self, **kw):
        return prompt.build(Config(**kw), TOOLS, now=NOW)

    def test_default_prompt_is_fully_rendered(self):
        text, warning = self.build()
        self.assertIsNone(warning)
        self.assertIn("open_app, run_command", text)
        self.assertIn("Wednesday 18 November 2026", text)
        self.assertIn(str(prompt.APP_DIR), text)
        for name in prompt.PLACEHOLDERS:
            self.assertNotIn("{" + name + "}", text)

    def test_default_prompt_has_no_hardcoded_project_path(self):
        self.assertNotIn("Projects/assist", prompt.DEFAULT_SYSTEM)

    def test_inline_system_prompt_replaces_default(self):
        text, _ = self.build(system_prompt="Be a pirate. Tools: {tools}")
        self.assertEqual(text, "Be a pirate. Tools: open_app, run_command")

    def test_file_beats_inline_and_default(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "p.md"
            f.write_text("from file on {date}")
            text, warning = self.build(system_prompt="inline", system_prompt_file=str(f))
        self.assertEqual(text, "from file on Wednesday 18 November 2026")
        self.assertIsNone(warning)

    def test_unreadable_file_falls_back_to_inline_with_warning(self):
        text, warning = self.build(system_prompt="inline", system_prompt_file="/nonexistent/p.md")
        self.assertEqual(text, "inline")
        self.assertIn("/nonexistent/p.md", warning)

    def test_empty_file_falls_back_with_warning(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "p.md"
            f.write_text("  \n")
            text, warning = self.build(system_prompt_file=str(f))
        self.assertIn("desktop assistant", text)
        self.assertIn("empty", warning)

    def test_extra_is_appended_to_default_and_to_overrides_and_is_rendered(self):
        text, _ = self.build(extra_system_prompt="Call me qmil. Model: {model}")
        self.assertIn("desktop assistant", text)
        self.assertTrue(text.rstrip().endswith("Call me qmil. Model: " + Config().model))
        text, _ = self.build(system_prompt="base", extra_system_prompt="extra")
        self.assertEqual(text, "base\n\nextra\n")

    def test_unknown_placeholders_and_json_braces_are_left_alone(self):
        text, _ = self.build(system_prompt='Reply as {"ok": true}. {unknown} stays. {tools}')
        self.assertEqual(text, 'Reply as {"ok": true}. {unknown} stays. open_app, run_command')


if __name__ == "__main__":
    unittest.main()
