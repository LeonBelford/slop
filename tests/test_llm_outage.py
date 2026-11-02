import io
import unittest
import urllib.error
from unittest import mock

from core import cfllm
from core.cfllm import LLM, LLMError, shrink


def cfg():
    return {"llm_api": "http://x/v1/chat/completions", "api_key": "k", "limits": {"llm_timeout": 5},
            "models": {"planner": "m", "reviewer": "m", "strategist": "m"}}


def http400(body=b'{"error":"too long"}'):
    return urllib.error.HTTPError("http://x", 400, "Bad Request", {}, io.BytesIO(body))


class OutageTests(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.llm = LLM(cfg(), self.logs.append)

    def test_400_is_retried_with_a_smaller_prompt_and_the_body_is_logged(self):
        sizes = []

        def fake(req, timeout=0):
            import json
            sizes.append(sum(len(m["content"]) for m in json.loads(req.data)["messages"]))
            if len(sizes) == 1:
                raise http400()
            return io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')
        with mock.patch.object(cfllm.urllib.request, "urlopen", fake), mock.patch.object(cfllm.time, "sleep"), \
                mock.patch.object(LLM, "_read", lambda *a, **k: ("ok", "")):
            self.assertEqual(self.llm.chat("impact", "planner", "sys", "u" * 20000), "ok")
        self.assertLess(sizes[1], sizes[0])
        self.assertTrue(any("too long" in m for m in self.logs))

    def test_permanent_400_ends_quickly_as_llm_error_without_sleeping(self):
        with mock.patch.object(cfllm.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(http400())), \
                mock.patch.object(cfllm.time, "sleep") as sl:
            with self.assertRaises(LLMError) as c:
                self.llm.chat("impact", "planner", "sys", "u" * 1000)
        self.assertEqual(c.exception.status, 400)
        sl.assert_not_called()

    def test_shrink_cuts_user_part(self):
        s, u = shrink("s" * 100, "u" * 10000)
        self.assertEqual(len(s), 100)
        self.assertLess(len(u), 7000)


if __name__ == "__main__":
    unittest.main()


class ProcessOutageTests(unittest.TestCase):
    def test_llm_outage_does_not_count_split_or_block_the_task(self):
        import tempfile
        from pathlib import Path
        import codefactory as cf
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "p"
            (ws / "agent").mkdir(parents=True)
            ag = cf.Agent(ws)
            ag.repo.mkdir(parents=True, exist_ok=True)
            item = ag.backlog.add("Colors", "d", force=True)

            def boom(*a, **k):
                raise LLMError("HTTP 400", 400)
            ag.attempt = boom
            ag.retry_strategy = lambda *a, **k: None
            with self.assertRaises(LLMError):
                ag.process(item)
            self.assertEqual((item["status"], item["attempts"]), ("pending", 0))
            self.assertEqual(len(ag.backlog.items), 1)
