import os
import shutil
import subprocess
import unittest


class JavaScriptLogicTests(unittest.TestCase):
    def test_quick_add_and_palette_logic(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js ist optional und in dieser Umgebung nicht installiert")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        subprocess.run([node, os.path.join(root, "tests", "test_js_logic.js")], check=True, cwd=root)


if __name__ == "__main__":
    unittest.main()
