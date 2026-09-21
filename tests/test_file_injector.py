# Packages/Agentic/tests/test_file_injector.py
"""
Unit tests for Agentic File Injector Library.
Can be executed from CLI or within Sublime Text.
"""

import os
import sys
import unittest
import tempfile
import shutil

# Dynamically resolve package root so tests run from any working directory
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(CURRENT_DIR)
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from lib.file_injector import (
    extract_file_mentions,
    resolve_path,
    read_file_safe,
    format_file_attachment,
    process_messages_file_mentions,
    MAX_FILE_SIZE
)


class MockWindow:
    """Mock Sublime Text window providing folders() and views()"""
    def __init__(self, folders=None, views=None):
        self._folders = folders or []
        self._views = views or []

    def folders(self):
        return self._folders

    def views(self):
        return self._views


class MockView:
    """Mock Sublime Text view providing file_name()"""
    def __init__(self, file_name=None):
        self._file_name = file_name

    def file_name(self):
        return self._file_name


class TestFileInjector(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agentic_test_")

        # Create a sample Python source file
        self.file_py = os.path.join(self.test_dir, "test_module.py")
        with open(self.file_py, "w", encoding="utf-8") as f:
            f.write("def calculate_total(a, b):\n    return a + b\n")

        # Create a sample file with spaces in its name
        self.file_with_spaces = os.path.join(self.test_dir, "space file.txt")
        with open(self.file_with_spaces, "w", encoding="utf-8") as f:
            f.write("Text with space\n")

        # Create a sample Makefile
        self.makefile = os.path.join(self.test_dir, "Makefile")
        with open(self.makefile, "w", encoding="utf-8") as f:
            f.write("all:\n\t@echo done\n")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_extract_file_mentions(self):
        text = (
            "Please check @/var/log/syslog and @~/config.json and @src/main.py. "
            "Also inspect @\"docs/notes with spaces.md\" and @'scripts/deploy.sh'. "
            "Also check @Makefile. "
            "Ignore email user@example.com and decorators @property or @classmethod."
        )
        mentions = extract_file_mentions(text)

        self.assertIn("/var/log/syslog", mentions)
        self.assertIn("~/config.json", mentions)
        self.assertIn("src/main.py", mentions)
        self.assertIn("docs/notes with spaces.md", mentions)
        self.assertIn("scripts/deploy.sh", mentions)
        self.assertIn("Makefile", mentions)

        # Ensure emails and decorators are not matched
        self.assertNotIn("example.com", mentions)
        self.assertNotIn("property", mentions)
        self.assertNotIn("classmethod", mentions)

    def test_trailing_punctuation_stripping(self):
        text = "Can you look at @src/auth.py, and then verify @src/models.py? Thanks!"
        mentions = extract_file_mentions(text)
        self.assertIn("src/auth.py", mentions)
        self.assertIn("src/models.py", mentions)
        self.assertNotIn("src/auth.py,", mentions)
        self.assertNotIn("src/models.py?", mentions)

    def test_resolve_absolute_path(self):
        res = resolve_path(self.file_py)
        self.assertEqual(res, self.file_py)

        res_missing = resolve_path("/tmp/definitely_missing_file_987654321.py")
        self.assertIsNone(res_missing)

    def test_resolve_home_path(self):
        home_dir = os.path.expanduser("~")
        sample_home_file = os.path.join(home_dir, ".agentic_home_test_file.tmp")
        try:
            with open(sample_home_file, "w") as f:
                f.write("test")
            res = resolve_path("~/.agentic_home_test_file.tmp")
            self.assertEqual(res, sample_home_file)
        finally:
            if os.path.exists(sample_home_file):
                os.remove(sample_home_file)

    def test_resolve_project_relative_path(self):
        window = MockWindow(folders=[self.test_dir])
        res = resolve_path("test_module.py", window=window)
        self.assertEqual(res, self.file_py)

    def test_resolve_quoted_spaces_path(self):
        window = MockWindow(folders=[self.test_dir])
        res = resolve_path("space file.txt", window=window)
        self.assertEqual(res, self.file_with_spaces)

    def test_resolve_active_view_parent_dir(self):
        sub_dir = os.path.join(self.test_dir, "nested")
        os.makedirs(sub_dir, exist_ok=True)
        sibling_file = os.path.join(sub_dir, "sibling.py")
        with open(sibling_file, "w") as f:
            f.write("x = 1\n")

        view = MockView(file_name=sibling_file)
        res = resolve_path("sibling.py", active_view=view)
        self.assertEqual(res, sibling_file)

    def test_process_messages_success(self):
        window = MockWindow(folders=[self.test_dir])
        messages = [
            {"role": "developer", "content": "System prompt."},
            {"role": "user", "content": "Please review @test_module.py."}
        ]
        success, updated, err = process_messages_file_mentions(messages, window=window)
        self.assertTrue(success)
        self.assertIsNone(err)
        self.assertEqual(len(updated), 2)

        user_content = updated[1]["content"]
        self.assertIn("Please review @test_module.py.", user_content)
        self.assertIn("### Attached Context: `test_module.py`", user_content)
        self.assertIn("def calculate_total", user_content)

    def test_process_messages_missing_file_halts(self):
        window = MockWindow(folders=[self.test_dir])
        messages = [
            {"role": "user", "content": "Look at @non_existent_file.py please."}
        ]
        success, updated, err = process_messages_file_mentions(messages, window=window)
        self.assertFalse(success)
        self.assertIsNone(updated)
        self.assertIn("Could not find the following referenced file(s):", err)
        self.assertIn("@non_existent_file.py", err)

    def test_binary_file_rejection(self):
        bin_file = os.path.join(self.test_dir, "sample.bin")
        with open(bin_file, "wb") as f:
            f.write(b"\x00\x01\x02\x03\x00")

        ok, msg = read_file_safe(bin_file)
        self.assertFalse(ok)
        self.assertIn("Binary file cannot be injected", msg)

    def test_max_file_size_boundary(self):
        large_file = os.path.join(self.test_dir, "large.txt")
        with open(large_file, "wb") as f:
            # Write 1 MB + 100 bytes
            f.write(b"A" * (MAX_FILE_SIZE + 100))

        ok, msg = read_file_safe(large_file)
        self.assertFalse(ok)
        self.assertIn("exceeds maximum size limit", msg)

    def test_deduplication(self):
        window = MockWindow(folders=[self.test_dir])
        messages = [
            {"role": "user", "content": "Inspect @test_module.py and compare @test_module.py."}
        ]
        success, updated, err = process_messages_file_mentions(messages, window=window)
        self.assertTrue(success)
        user_content = updated[0]["content"]
        # Attached context block must only appear once
        self.assertEqual(user_content.count("### Attached Context: `test_module.py`"), 1)


if __name__ == "__main__":
    unittest.main()
