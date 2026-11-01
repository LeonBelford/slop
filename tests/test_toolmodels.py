import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import codefactory as sa  # noqa: E402
import selfimprove  # noqa: E402
import webui  # noqa: E402
from dash import toolmodels  # noqa: E402
from webapi import common as webapi_common  # noqa: E402


def fresh_cfg():
    return json.loads(json.dumps(sa.DEFAULT_CONFIG))


class ToolModelsTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.state = Path(self.t.name) / "self_improve"

    def test_defaults_change_nothing(self):
        cfg = toolmodels.apply(fresh_cfg(), self.state)
        self.assertEqual(cfg["models"], sa.DEFAULT_CONFIG["models"])
        self.assertEqual(cfg["coder_model"], sa.DEFAULT_CONFIG["coder_model"])
        self.assertEqual(cfg["routing"]["mode"], "auto")

    def test_save_and_apply(self):
        toolmodels.save(self.state, {"planner": "deepseek-r1:14b", "coder": "devstral:24b",
                                     "routing": {"mode": "auto", "light": "qwen2.5-coder:7b", "heavy": "", "final_reviewer": "phi4:14b"}})
        cfg = toolmodels.apply(fresh_cfg(), self.state)
        self.assertEqual(cfg["models"]["planner"], "deepseek-r1:14b")
        self.assertEqual(cfg["models"]["reviewer"], sa.DEFAULT_CONFIG["models"]["reviewer"])
        self.assertEqual(cfg["coder_model"], "ollama_chat/devstral:24b")
        self.assertEqual(cfg["routing"]["light"], "qwen2.5-coder:7b")
        self.assertEqual(cfg["routing"]["final_reviewer"], "phi4:14b")

    def test_per_task_model_pins_the_coder(self):
        cfg = toolmodels.apply(fresh_cfg(), self.state, "qwen2.5-coder:3b")
        self.assertEqual(cfg["coder_model"], "ollama_chat/qwen2.5-coder:3b")
        self.assertEqual(cfg["routing"]["mode"], "off")

    def test_invalid_names_are_rejected_and_broken_files_ignored(self):
        with self.assertRaises(ValueError):
            toolmodels.save(self.state, {"coder": "x; rm -rf /"})
        with self.assertRaises(ValueError):
            toolmodels.save(self.state, {"routing": {"mode": "weird"}})
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "models.json").write_text(json.dumps({"coder": "bad name!", "planner": "ok:1b", "routing": "x"}))
        s = toolmodels.load(self.state)
        self.assertEqual((s["coder"], s["planner"], s["routing"]["mode"]), ("", "ok:1b", "auto"))

    def test_route_uses_the_saved_choice(self):
        toolmodels.save(self.state, {"routing": {"mode": "auto", "light": "qwen2.5-coder:3b", "heavy": "devstral:24b", "final_reviewer": "phi4:14b"}})
        cfg = toolmodels.apply(fresh_cfg(), self.state)
        llm = sa.LLM(cfg, lambda m: None)
        llm.installed = {"qwen2.5-coder:14b", "qwen2.5-coder:3b", "devstral:24b", "phi4:14b"}
        r = selfimprove._route(llm)
        self.assertEqual((r["light"], r["heavy"], r["final_reviewer"]), ("qwen2.5-coder:3b", "devstral:24b", "phi4:14b"))
        self.assertEqual(llm.overrides["reviewer"], "phi4:14b")
        toolmodels.save(self.state, {"routing": {"mode": "off"}})
        llm2 = sa.LLM(toolmodels.apply(fresh_cfg(), self.state), lambda m: None)
        llm2.installed = llm.installed
        self.assertEqual(selfimprove._route(llm2), {"light": None, "heavy": None, "final_reviewer": None})

    def test_service_endpoints(self):
        old = webapi_common.ROOT
        webapi_common.ROOT = Path(self.t.name)
        self.addCleanup(lambda: setattr(webapi_common, "ROOT", old))
        svc = webui.Service(Path(self.t.name) / "projects")
        svc.installed_models = lambda api: ["qwen2.5-coder:14b", "phi4:14b"]
        out = svc.tool_models_save({"reviewer": "phi4:14b"})
        self.assertEqual(out["saved"]["reviewer"], "phi4:14b")
        self.assertIn("phi4:14b", out["installed"])
        self.assertEqual(svc.tool_models()["saved"]["reviewer"], "phi4:14b")
        with self.assertRaises(webui.UiError):
            svc.tool_models_save({"coder": "bad name!"})
        with self.assertRaises(webui.UiError):
            svc.tool_task({"task": "do something useful here", "model": "bad name!"})


if __name__ == "__main__":
    unittest.main()
