import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import webui  # noqa: E402
from core import cfprofile as cp  # noqa: E402


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.a = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def test_profile_roundtrip_and_block(self):
        self.assertEqual(cp.profile_block(self.a), "")
        cp.save_profile(self.a, {"description": "Builds a booking site", "tone": "concise", "instructions": "Always use TypeScript."})
        b = cp.profile_block(self.a)
        self.assertIn("Always use TypeScript.", b)
        self.assertIn("concise", b)
        with self.assertRaises(cp.ProfileError):
            cp.save_profile(self.a, {"tone": "angry"})
        with self.assertRaises(cp.ProfileError):
            cp.save_profile(self.a, {"instructions": "x" * 9000})

    def test_context_files_validated(self):
        cp.add_context(self.a, "spec.md", "# API\nPOST /book creates a booking")
        self.assertEqual([c["name"] for c in cp.list_context(self.a)], ["spec.md"])
        for bad in ("../evil.md", ".hidden.md", "image.png", "a/../../b.sh"):
            try:
                cp.add_context(self.a, bad, "x")
            except cp.ProfileError:
                continue
        self.assertFalse((self.a.parent / "evil.md").exists())
        with self.assertRaises(cp.ProfileError):
            cp.add_context(self.a, "bin.txt", "a\0b")
        with self.assertRaises(cp.ProfileError):
            cp.add_context(self.a, "big.txt", "x" * (cp.MAX_FILE + 1))
        with self.assertRaises(cp.ProfileError):
            cp.delete_context(self.a, "../profile.json")
        cp.delete_context(self.a, "spec.md")
        self.assertTrue(all((self.a / "context" / c["name"]).parent == self.a / "context" for c in cp.list_context(self.a)))

    def test_context_block_picks_relevant_part(self):
        big = "\n\n".join(["Paragraph about invoices and tax rates. " * 20, "Paragraph about booking calendar slots and timezones. " * 20, "Unrelated notes about lunch. " * 20])
        cp.add_context(self.a, "notes.md", big)
        block = cp.context_block(self.a, "booking calendar timezone", 3000)
        self.assertIn("calendar", block)
        self.assertNotIn("lunch", block)
        self.assertEqual(cp.context_block(self.a / "none", "x"), "")


class ServiceTests(unittest.TestCase):
    def test_actions_and_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "demo"
            (ws / "agent").mkdir(parents=True)
            (ws / "agent" / "GOAL.md").write_text("g")
            svc = webui.Service(Path(tmp))
            svc.act("demo", "profile", {"instructions": "Be brief", "tone": "thorough", "features": {"plan_critique": False, "e2e": "off"}})
            svc.act("demo", "context-add", {"name": "api.md", "text": "hello"})
            p = svc.profile("demo")
            self.assertEqual(p["profile"]["instructions"], "Be brief")
            self.assertEqual(p["context"][0]["name"], "api.md")
            self.assertEqual((p["features"]["plan_critique"], p["features"]["e2e"]), (False, "off"))
            with self.assertRaises(webui.UiError):
                svc.act("demo", "profile", {"features": {"nope": True}})
            with self.assertRaises(webui.UiError):
                svc.act("demo", "context-add", {"name": "x.exe", "text": "x"})
            svc.act("demo", "context-delete", {"name": "api.md"})
            self.assertEqual(svc.profile("demo")["context"], [])

    def test_agent_prompt_contains_profile_and_context(self):
        import codefactory as sa
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "p"
            (ws / "agent").mkdir(parents=True)
            cp.save_profile(ws / "agent", {"instructions": "Prefer small functions"})
            cp.add_context(ws / "agent", "style.md", "Use snake_case for booking helpers")
            ag = sa.Agent(ws)
            self.assertIn("Prefer small functions", ag.directive)
            pack = ag.mem.context_pack({"title": "booking helpers", "description": "", "id": "1"}, [])
            self.assertIn("Prefer small functions", pack)
            self.assertIn("snake_case", pack)
            self.assertTrue(json.dumps(pack))


if __name__ == "__main__":
    unittest.main()
