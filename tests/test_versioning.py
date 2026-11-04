import unittest
from pathlib import Path


class VersionMetadataTests(unittest.TestCase):
    def test_current_version_and_changelog_baseline_are_consistent(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / "VERSION").read_text(encoding="utf-8").strip(), "0.1.0")
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [0.1.0] - 2026-11-04", changelog)
        self.assertIn("## [0.0.0] - Legacy baseline", changelog)
