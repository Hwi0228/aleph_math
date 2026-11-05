import unittest
from pathlib import Path


class VersionMetadataTests(unittest.TestCase):
    def test_current_version_and_changelog_baseline_are_consistent(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / "VERSION").read_text(encoding="utf-8").strip(), "0.1.2")
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [0.1.2] - 2026-11-05", changelog)
        self.assertIn("## [0.0.0] - Legacy baseline", changelog)


    def test_release_workflow_publishes_new_versions_after_master_merge(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("branches: [master]", workflow)
        self.assertIn("VERSION", workflow)
        self.assertIn("CHANGELOG.md", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("BEFORE_SHA", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn(r'fullmatch(r"\d+\.\d+\.\d+', workflow)
        self.assertIn(r'output.write("should_publish=false\n")', workflow)
        self.assertIn("remaining = changelog[heading.end():]", workflow)

    def test_sidebar_displays_version_from_the_version_file(self):
        root = Path(__file__).resolve().parents[1]
        app_source = (root / "app.py").read_text(encoding="utf-8")
        sidebar = (root / "static/templates/partials/sidebar.html").read_text(encoding="utf-8")
        self.assertIn('app.config["APP_VERSION"]', app_source)
        self.assertIn("app_version", app_source)
        self.assertIn("HEXORA · v{{ app_version }}", sidebar)
