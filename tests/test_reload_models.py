import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import codefactory as sa  # noqa: E402


class ReloadModelsTests(unittest.TestCase):
    def test_running_agent_picks_up_models_saved_in_the_ui(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "p"
            (ws / "agent").mkdir(parents=True)
            agent = sa.Agent(ws)
            before = agent.cfg["models"]["planner"]
            self.assertEqual(agent.llm.cfg["models"]["planner"], before)
            (ws / "agent" / "config.json").write_text(json.dumps({
                "models": {"planner": "deepseek-r1:14b"}, "coder_model": "ollama_chat/deepseek-coder-v2:16b",
                "routing": {"mode": "off"}}))
            agent.reload_models()
            self.assertEqual(agent.cfg["models"]["planner"], "deepseek-r1:14b")
            self.assertEqual(agent.llm.cfg["models"]["planner"], "deepseek-r1:14b")
            self.assertEqual(agent.cfg["coder_model"], "ollama_chat/deepseek-coder-v2:16b")
            self.assertEqual(agent.cfg["routing"]["mode"], "off")
            self.assertEqual(agent.cfg["models"]["reviewer"], sa.DEFAULT_CONFIG["models"]["reviewer"])

    def test_missing_or_broken_config_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "p"
            (ws / "agent").mkdir(parents=True)
            agent = sa.Agent(ws)
            before = json.dumps(agent.cfg["models"]) + agent.cfg["coder_model"]
            (ws / "agent" / "config.json").write_text("{not json")
            agent.reload_models()
            self.assertEqual(json.dumps(agent.cfg["models"]) + agent.cfg["coder_model"], before)


if __name__ == "__main__":
    unittest.main()
