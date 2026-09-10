import re
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "start_camera_stack.ps1"


class TestStartCameraStackContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = SCRIPT.read_text(encoding="utf-8")
        match = re.search(
            r"\$line -match '([^']*CAMERA_SOURCE[^']*)'",
            cls.script,
        )
        if match is None:
            raise AssertionError("CAMERA_SOURCE .env detector is missing")
        cls.dotenv_pattern = re.compile(match.group(1))

    def fallback_selected(self, inherited, dotenv_lines):
        in_dotenv = any(
            self.dotenv_pattern.match(line) for line in dotenv_lines.splitlines()
        )
        return not inherited and not in_dotenv

    def test_inherited_camera_source_prevents_fallback(self):
        self.assertFalse(self.fallback_selected(True, ""))
        self.assertIn(
            '$null -ne [Environment]::GetEnvironmentVariable("CAMERA_SOURCE", "Process")',
            self.script,
        )

    def test_dotenv_camera_source_prevents_fallback(self):
        dotenv = """
            # CAMERA_SOURCE=ignored-comment

            export   CAMERA_SOURCE = rtsp://example.invalid/private
        """
        self.assertFalse(self.fallback_selected(False, dotenv))

    def test_missing_camera_source_uses_loopback_fallback(self):
        dotenv = """
            # CAMERA_SOURCE=ignored-comment
            OTHER_SETTING=value
        """
        self.assertTrue(self.fallback_selected(False, dotenv))

        script = SCRIPT.read_text(encoding="utf-8")
        guarded_default = re.search(
            r'if \(-not \$cameraSourceInherited -and -not \$cameraSourceInDotEnv\) \{\s*'
            r'\$env:CAMERA_SOURCE = "http://127\.0\.0\.1:5000/stream"\s*\}',
            script,
        )

        self.assertIsNotNone(guarded_default)
        self.assertLess(
            guarded_default.end(),
            script.index('Start-Process -FilePath $venvPy'),
        )


if __name__ == "__main__":
    unittest.main()
