import unittest
from pathlib import Path


class ResponsibleLanguageTests(unittest.TestCase):
    def test_executable_sources_contain_no_accusatory_labels(self):
        repo = Path(__file__).resolve().parents[3]
        roots = [repo / "apps" / "api" / "app", repo / "apps" / "web" / "app",
                 repo / "apps" / "web" / "components", repo / "apps" / "web" / "lib",
                 repo / "config"]
        labels = [
            "fr" + "aud", "con" + "firmed", "gui" + "lty", "crim" + "inal",
            "cul" + "prit", "off" + "ender", "suspi" + "cious",
            "illegal" + " activity", "money" + " laundering",
        ]
        findings = []
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in {".py", ".ts", ".tsx", ".js", ".json"}:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                for label in labels:
                    if label in text:
                        findings.append(f"{path.relative_to(repo)}: disallowed advisory-language label")
        self.assertEqual(findings, [], "\n".join(findings))


if __name__ == "__main__":
    unittest.main()
