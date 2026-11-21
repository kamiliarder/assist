"""Toolbox dispatch. Needs PyGObject (GLib/Gio), which the nix dev shell provides."""
import unittest

from ordis.tools import Toolbox


class ToolboxErrorTests(unittest.TestCase):
    def setUp(self):
        self.tb = Toolbox(lambda _text: False)

    def test_tool_errors_reach_the_model_with_their_real_message(self):
        # Used to come back as "error: unknown tool error", so the model could not self-correct.
        out = self.tb.call("edit_file", {"file_path": "~/definitely-missing-file.txt",
                                         "search_block": "a", "replace_block": "b"})
        self.assertTrue(out.startswith("error:"))
        self.assertIn("does not exist", out)
        self.assertNotIn("unknown tool error", out)

    def test_unknown_tool_lists_the_valid_ones(self):
        out = self.tb.call("teleport", {})
        self.assertIn("unknown tool 'teleport'", out)
        self.assertIn("edit_file", out)

    def test_bad_arguments_are_reported(self):
        self.assertIn("bad arguments", self.tb.call("read_file", {"nope": 1}))


if __name__ == "__main__":
    unittest.main()
