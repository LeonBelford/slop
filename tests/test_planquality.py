import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import codefactory as cf  # noqa: E402
from core import blueprint, cfa_gates, cfa_quality  # noqa: E402

DATA = {"architecture": {"contracts": ["parse(text) -> Doc"], "modules": [{"name": "Core", "responsibility": "r"},
                                                                          {"name": "Api", "responsibility": "r", "uses": ["Core"]}]},
        "dependencies": [{"name": "serde", "version": "1.0", "reason": "json"}],
        "layers": [{"name": "A", "goal": "g", "tasks": [{"key": "setup", "title": "Setup"}, {"key": "p", "title": "Parser", "risk": "high"}]},
                   {"name": "B", "goal": "g", "tasks": [{"key": "c", "title": "Logic"}]}]}


class AgentCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / "p"
        (ws / "agent").mkdir(parents=True)
        self.ag = cf.Agent(ws)
        self.ag.repo.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self.tmp.cleanup)


class BlueprintTests(unittest.TestCase):
    def test_foundation_tasks_come_first_in_order(self):
        bp = blueprint.normalize(DATA, skeleton=True, deps_task=True, contracts_task=True)
        t = [x["key"] for x in bp["layers"][0]["tasks"]]
        self.assertEqual(t, ["deps", "skeleton", "contracts", "setup", "p"])
        self.assertEqual(bp["layers"][0]["tasks"][1]["depends_on"], ["deps"])
        self.assertEqual(bp["dependencies"][0]["name"], "serde")
        self.assertEqual(bp["layers"][0]["tasks"][4]["risk"], "high")

    def test_flags_off_changes_nothing_and_model_skeleton_is_moved_first(self):
        self.assertEqual([x["key"] for x in blueprint.normalize(DATA)["layers"][0]["tasks"]], ["setup", "p"])
        d = {"layers": [{"name": "A", "tasks": [{"key": "x", "title": "X"}, {"key": "s", "title": "Skel", "skeleton": True}]}]}
        self.assertEqual([x["key"] for x in blueprint.normalize(d, skeleton=True)["layers"][0]["tasks"]], ["s", "x"])


class SizeSpecContractTests(AgentCase):
    def test_too_big_task_is_split_before_the_start(self):
        a = self.ag.backlog.add("Big", "d", force=True)
        calls = []

        def fake(name, *x, **k):
            calls.append(name)
            if name == "size_estimate":
                return {"files": 9, "lines": 900, "reason": "huge"}
            return {"cause": "big", "subtasks": [{"title": "S1", "description": "d"}, {"title": "S2", "description": "d"}]}
        self.ag.llm.json = fake
        self.assertTrue(self.ag.size_gate(a))
        self.assertEqual(a["status"], "cancelled")
        self.assertIn("before the start", a["notes"][-1])
        self.assertFalse(self.ag.size_gate(a))

    def test_small_task_passes(self):
        a = self.ag.backlog.add("Small", "d", force=True)
        self.ag.llm.json = lambda *x, **k: {"files": 1, "lines": 40}
        self.assertFalse(self.ag.size_gate(a))
        self.assertEqual(a["size_files"], 1)

    def test_spec_is_pinned_and_review_problems_are_passed_on(self):
        a = self.ag.backlog.add("Parse", "d", force=True)
        plan = {"steps": ["s1"]}
        self.ag.llm.json = lambda name, *x, **k: ({"files": [{"path": "a.py"}], "signatures": ["def parse(t)"], "edge_cases": ["empty"]}
                                                  if name == "spec" else {"ok": False, "problems": ["returns str not Doc"]})
        self.ag.make_spec(a, plan)
        self.assertIn("def parse(t)", self.ag.pinned)
        self.assertIn("returns str not Doc", self.ag.pinned)
        self.assertEqual(plan["spec"]["edge_cases"], ["empty"])

    def test_contract_test_removal_detected(self):
        diff = ("diff --git a/tests/contracts/test_a.py b/tests/contracts/test_a.py\n--- a/x\n+++ b/x\n-    assert f(1) == 2\n+    assert f(1)\n"
                "diff --git a/tests/test_b.py b/tests/test_b.py\n-gone\n")
        self.assertEqual(cfa_quality.removed_contract_lines(diff), ["assert f(1) == 2"])
        self.ag.git.diff = lambda *a, **k: diff
        self.assertIn("contract tests", self.ag.contract_violation({"key": "x"}))
        self.assertEqual(self.ag.contract_violation({"key": "contracts"}), "")


class DriftAndGateTests(AgentCase):
    def test_drift_finds_size_and_forbidden_import(self):
        mods = [{"name": "Core", "uses": []}, {"name": "Api", "uses": ["Core"]}, {"name": "Db", "uses": ["Core"]}]
        files = {"src/api/handler.py": "from src.db import x\n", "src/core/big.py": "x\n" * 900, "src/api/ok.py": "from src.core import y\n"}
        v = cfa_gates.drift(mods, files)
        self.assertEqual(sorted((x["file"], x["kind"]) for x in v), [("src/api/handler.py", "import"), ("src/core/big.py", "size")])

    def test_layer_gate_red_queues_fix_tasks_and_gives_up_after_two(self):
        self.ag.blueprint = lambda: {"layers": [{"name": "A", "goal": "works"}]}
        self.ag.verify = lambda: (False, "[test] FAILED boom")
        self.assertFalse(self.ag.layer_gate(0, "A"))
        self.assertEqual(self.ag.backlog.items[-1]["source"], "gate")
        self.assertFalse(self.ag.layer_gate(0, "A"))
        self.assertTrue(self.ag.layer_gate(0, "A"))
        self.assertEqual(self.ag.state["layer_gates"]["0"]["status"], "passed with open problems")

    def test_layer_gate_green_and_strategist_gap(self):
        self.ag.blueprint = lambda: {"layers": [{"name": "A", "goal": "works"}]}
        self.ag.verify = lambda: (True, "ok")
        self.ag.llm.json = lambda *a, **k: {"met": False, "missing": [{"title": "Missing piece", "description": "d"}]}
        self.assertFalse(self.ag.layer_gate(0, "A"))
        self.ag.llm.json = lambda *a, **k: {"met": True}
        self.assertTrue(self.ag.layer_gate(0, "A"))
        self.assertEqual(self.ag.state["layer_gates"]["0"]["status"], "passed")

    def test_spike_is_forced_for_risky_tasks(self):
        a = self.ag.backlog.add("Parser", "d", force=True)
        a["risk"] = "high"
        self.ag.cur_attempt = 1
        self.assertTrue(self.ag.spike_due(a, {}))


if __name__ == "__main__":
    unittest.main()
