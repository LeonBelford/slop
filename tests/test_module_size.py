import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGACY = {"webui.py", "selfimprove.py"}  # still to be split; every other module must stay under the limit
LIMIT = 700  # lines: a small local model has to fit a whole module into its context window next to the prompt


class ModuleSizeTests(unittest.TestCase):
    def test_modules_stay_small(self):
        too_big = {str(p.relative_to(ROOT)): n for p in [*ROOT.glob("*.py"), *ROOT.glob("core/*.py"), *ROOT.glob("infra/*.py"), *ROOT.glob("dash/*.py")] if (n := len(p.read_text("utf-8").splitlines())) > LIMIT and p.name not in LEGACY}
        self.assertEqual(too_big, {}, "split big modules so small models can edit them")

    def test_codefactory_reexports_the_split_modules(self):
        import codefactory as sa
        for name in ("LLM", "Git", "Backlog", "Memory", "Agent", "sh", "DEFAULT_CONFIG", "_strip_think", "parse_json"):
            self.assertTrue(hasattr(sa, name), name)
        self.assertTrue(all(hasattr(sa.Agent, m) for m in ("attempt", "security_gate", "research", "status")))


if __name__ == "__main__":
    unittest.main()
