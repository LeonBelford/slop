"""Tests for the control-tools batch: live control, roles, context tuner, code search, test generator, refactor runs,
sandbox, events/timeline, ADRs/health and the chat agent registry."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import control
from dash import adrs, chatagent, codesearch, docsync, events, refactor, roles, sandbox, testgen
from infra import ctxtune


def git_repo() -> Path:
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", "--allow-empty"], cwd=d, check=True)
    return d


def commit(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", rel], cwd=repo, check=True)


class ControlTests(unittest.TestCase):
    def test_update_load_roundtrip_and_hint_cap(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d)
            control.update(a, paused=True, step_mode=True, next="T0001")
            for i in range(8):
                control.update(a, hint=f"do thing {i}")
            c = control.load(a)
            self.assertTrue(c["paused"] and c["step_mode"])
            self.assertEqual(c["next"], "T0001")
            self.assertEqual(len(c["hints"]), control.MAX_HINTS)
            self.assertIn("do thing 7", c["hints"][-1]["text"])

    def test_decide_spec_requires_a_pending_design(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                control.decide_spec(Path(d), True)

    def test_control_wait_resets_stale_pause_and_removes_stale_spec_files_on_first_call(self):
        class Agent(control.ControlMixin):
            def __init__(self, d):
                self.dir = d
                self.logged = []

            def log(self, m):
                self.logged.append(m)

            def phase(self, *a, **k):
                pass

        with tempfile.TemporaryDirectory() as d:
            a = Path(d)
            control.update(a, paused=True)
            pending, decision = control.spec_paths(a)
            pending.write_text("{}", "utf-8")
            decision.write_text("{}", "utf-8")
            ag = Agent(a)
            ag.control_wait()  # must not block: the stale pause from a dead run is cleared on the first call
            self.assertFalse(control.load(a)["paused"])
            self.assertFalse(pending.exists())
            self.assertFalse(decision.exists())


class RolesTests(unittest.TestCase):
    def test_save_clean_apply_and_reset(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "roles.json"
            roles.save(p, ["planner"], "planner", {"temperature": "0.4", "prompt": "Be terse."})
            system, user, temp = roles.apply(roles.load(p)["planner"], "sys", "usr", 0.9)
            self.assertEqual(temp, 0.4)
            self.assertIn("Be terse.", system)
            self.assertEqual(user, "usr")
            with self.assertRaises(ValueError):
                roles.save(p, ["planner"], "planner", {"temperature": "9"})
            with self.assertRaises(ValueError):
                roles.save(p, ["planner"], "unknown", {})
            roles.reset(p, "planner")
            self.assertEqual(roles.load(p), {})

    def test_apply_without_override_is_a_no_op(self):
        self.assertEqual(roles.apply({}, "s", "u", 0.5), ("s", "u", 0.5))


class CtxTuneTests(unittest.TestCase):
    def test_recommend_scales_with_free_vram_and_falls_back_without_a_gpu_reading(self):
        big = ctxtune.recommend(4000, 7, 12000)
        self.assertGreaterEqual(big["num_ctx"], 8192)
        self.assertEqual(ctxtune.recommend(4000, 7, None)["num_ctx"], 8192)
        tiny = ctxtune.recommend(11000, 14, 12000)
        self.assertEqual(tiny["num_ctx"], 4096)

    def test_write_modelfile_rejects_bad_input_and_writes_a_valid_one(self):
        with tempfile.TemporaryDirectory() as d:
            r = ctxtune.write_modelfile(Path(d), "qwen2.5-coder:14b", 16384)
            self.assertIn("PARAMETER num_ctx 16384", Path(r["path"]).read_text())
            with self.assertRaises(ValueError):
                ctxtune.write_modelfile(Path(d), "qwen2.5-coder:14b", 1)


class CodeSearchTests(unittest.TestCase):
    def test_search_and_uses_find_a_python_function(self):
        repo = git_repo()
        try:
            commit(repo, "a.py", "def helper():\n    return 1\n")
            commit(repo, "b.py", "from a import helper\n\ndef run():\n    return helper()\n")
            hits = codesearch.search(repo, "helper")
            self.assertTrue(any(h["name"] == "helper" and h["file"] == "a.py" for h in hits))
            uses = codesearch.uses(repo, "helper")
            self.assertTrue(any(u["file"] == "b.py" for u in uses))
        finally:
            subprocess.run(["rm", "-rf", str(repo)])


class TestGenTests(unittest.TestCase):
    def test_untested_lists_files_no_test_mentions(self):
        repo = git_repo()
        try:
            commit(repo, "core/widget.py", "def build():\n    return 42\n" * 3)
            commit(repo, "tests/test_other.py", "def test_x():\n    assert True\n")
            u = testgen.untested(repo)
            self.assertTrue(any(f["file"] == "core/widget.py" for f in u))
            task = testgen.task_for("core/widget.py")
            self.assertEqual(task["type"], "test")
            self.assertIn("core/widget.py", task["description"])
        finally:
            subprocess.run(["rm", "-rf", str(repo)])

    def test_mutants_flip_an_operator(self):
        muts = testgen.mutants("def f(x):\n    return x + 1\n")
        self.assertTrue(muts)
        line, old, new, mutated = muts[0]
        self.assertNotEqual(old, new)
        self.assertIn(new, mutated)


class RefactorTests(unittest.TestCase):
    def test_rename_plan_and_apply_with_green_tests(self):
        repo = git_repo()
        try:
            commit(repo, "m.py", "def f():\n    return 1\n")
            plan = refactor.rename_plan(repo, "f", "g")
            self.assertEqual(plan["files"], {"m.py": 1})
            res = refactor.rename(repo, "f", "g", "true")
            self.assertEqual(res["files"], 1)
            self.assertIn("def g()", (repo / "m.py").read_text())
        finally:
            subprocess.run(["rm", "-rf", str(repo)])

    def test_rename_rejects_bad_identifiers_and_clashes(self):
        repo = git_repo()
        try:
            commit(repo, "m.py", "def f():\n    return 1\ndef h():\n    return 2\n")
            with self.assertRaises(ValueError):
                refactor.rename_plan(repo, "1bad", "ok")
            with self.assertRaises(ValueError):
                refactor.rename_plan(repo, "f", "h")
        finally:
            subprocess.run(["rm", "-rf", str(repo)])

    def test_big_modules_flags_oversized_files(self):
        repo = git_repo()
        try:
            commit(repo, "huge.py", "\n".join(f"x{i} = {i}" for i in range(600)))
            big = refactor.big_modules(repo, limit_lines=500)
            self.assertTrue(any(b["file"] == "huge.py" for b in big))
        finally:
            subprocess.run(["rm", "-rf", str(repo)])


class SandboxTests(unittest.TestCase):
    def test_run_executes_a_command_in_a_copy_of_the_repo(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "repo").mkdir()
            subprocess.run(["git", "init", "-q"], cwd=ws / "repo", check=True)
            (ws / "repo" / "f.txt").write_text("hi", "utf-8")
            r = sandbox.run(ws, "cat f.txt", 10)
            self.assertEqual(r["rc"], 0)
            self.assertIn("hi", r["out"])
            self.assertFalse((ws / "repo" / "f.txt").read_text() == "changed")  # the real repo is never touched

    def test_run_rejects_empty_commands(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                sandbox.run(Path(d), "  ", 10)


class EventsTests(unittest.TestCase):
    def test_add_and_timeline_newest_first(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d)
            events.add(a, "control", "paused")
            raw = (a / "events.jsonl").read_text("utf-8").splitlines()
            row = json.loads(raw[0])
            row["time"], row["text"] = "2099-01-01 00:00:01", "resumed"  # force a later timestamp than "paused"
            (a / "events.jsonl").write_text("\n".join(raw) + "\n" + json.dumps(row) + "\n", "utf-8")
            tl = events.timeline(a)
            self.assertEqual(tl[0]["text"], "resumed")
            self.assertEqual(tl[0]["icon"], "control")


class AdrsTests(unittest.TestCase):
    def test_decisions_filters_by_words_without_an_index(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "agent"
            (a / "memory").mkdir(parents=True)
            rows = [{"item": "T1", "title": "Use SQLite", "context": "simple storage", "decision": "SQLite", "consequences": "fewer deps"},
                    {"item": "T2", "title": "Use Postgres", "context": "scale", "decision": "Postgres", "consequences": "ops cost"}]
            (a / "memory" / "decisions.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", "utf-8")
            self.assertEqual(len(adrs.decisions(a)), 2)
            hit = adrs.decisions(a, "sqlite")
            self.assertEqual(len(hit), 1)
            self.assertEqual(hit[0]["item"], "T1")

    def test_health_scores_a_clean_empty_project_at_100(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "agent"
            a.mkdir()
            (a / "backlog.json").write_text("[]", "utf-8")
            repo = git_repo()
            try:
                h = adrs.health(a, repo)
                self.assertEqual(h["score"], 100)
            finally:
                subprocess.run(["rm", "-rf", str(repo)])


class DocSyncTests(unittest.TestCase):
    def test_mark_layer_appends_once(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            docsync.mark_layer(repo, 1, "Core")
            docsync.mark_layer(repo, 1, "Core")
            text = (repo / "CHANGELOG.md").read_text("utf-8")
            self.assertEqual(text.count("Layer 1 done: Core"), 1)

    def test_api_markdown_lists_files_with_symbols(self):
        md = docsync.api_markdown({"a.py": {"purpose": "does a thing", "symbols": ["f", "g"]}})
        self.assertIn("a.py", md)
        self.assertIn("`f`", md)


class ChatAgentTests(unittest.TestCase):
    def test_validate_checks_tool_project_link_and_required_args(self):
        with self.assertRaises(ValueError):
            chatagent.validate({"tool": "nope"}, ["demo"])
        with self.assertRaises(ValueError):
            chatagent.validate({"tool": "add_task", "project": "other", "text": "x"}, ["demo"])
        with self.assertRaises(ValueError):
            chatagent.validate({"tool": "add_task", "project": "demo"}, ["demo"])
        tool, args = chatagent.validate({"tool": "add_task", "project": "demo", "text": "Build X"}, ["demo"])
        self.assertEqual((tool, args["text"]), ("add_task", "Build X"))

    def test_extract_drops_invalid_blocks_and_keeps_valid_ones(self):
        text = ('Sure.\n```action\n{"tool": "status", "project": "demo"}\n```\nand\n```action\nnot json```\n'
                '```action\n{"tool": "nope", "project": "demo"}\n```')
        calls = chatagent.extract(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool"], "status")

    def test_describe_is_short_and_mentions_the_project(self):
        self.assertIn("demo", chatagent.describe("add_task", {"project": "demo", "text": "Build X"}))


if __name__ == "__main__":
    unittest.main()
