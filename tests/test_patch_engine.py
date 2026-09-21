# Packages/Agentic/tests/test_patch_engine.py
import unittest
import sys
import os

# Ensure package root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lib.patch_parser import parse_patch_blocks
from lib.patch_engine import apply_patch_blocks, apply_single_block, PatchError


class TestPatchEngine(unittest.TestCase):

    def test_parse_single_block(self):
        sample = """
Here is the fix for `src/auth.py`:

src/auth.py
<<<<<<< SEARCH
def login():
    return False
=======
def login():
    return True
>>>>>>> REPLACE
"""
        blocks = parse_patch_blocks(sample)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].filepath, "src/auth.py")
        self.assertEqual(blocks[0].search_text.strip(), "def login():\n    return False")
        self.assertEqual(blocks[0].replace_text.strip(), "def login():\n    return True")

    def test_exact_match_replace(self):
        content = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
        search = "def foo():\n    return 1\n"
        replace = "def foo():\n    return 42\n"
        res = apply_single_block(content, search, replace)
        self.assertIn("return 42", res)
        self.assertIn("def bar():", res)

    def test_whitespace_tolerance(self):
        # Target file has trailing spaces, search block does not
        content = "def calculate():   \n    x = 10  \n    return x\n"
        search = "def calculate():\n    x = 10\n    return x"
        replace = "def calculate():\n    x = 20\n    return x\n"
        res = apply_single_block(content, search, replace)
        self.assertIn("x = 20", res)

    def test_fuzzy_matching(self):
        # Slight line comment deviation
        content = "def run():\n    # old comment\n    setup()\n    execute()\n"
        search = "def run():\n    # comment changed slightly\n    setup()\n    execute()"
        replace = "def run():\n    setup()\n    execute()\n    cleanup()\n"
        res = apply_single_block(content, search, replace)
        self.assertIn("cleanup()", res)

    def test_ambiguous_block_fails(self):
        content = "item = 1\nitem = 1\n"
        search = "item = 1\n"
        replace = "item = 2\n"
        with self.assertRaises(PatchError):
            apply_single_block(content, search, replace)


if __name__ == "__main__":
    unittest.main()