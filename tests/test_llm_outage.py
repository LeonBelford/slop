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
            ag.size_gate = lambda i: False
            with self.assertRaises(LLMError):
                ag.process(item)
            self.assertEqual((item["status"], item["attempts"]), ("pending", 0))
            self.assertEqual(len(ag.backlog.items), 1)


class CtxPersistTests(unittest.TestCase):
    def test_ctx_survives_restart_and_prompt_is_cut_before_the_first_call(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "llm_ctx.json"
            f.write_text(json.dumps({"ctx": 4096, "at": __import__("time").time()}))
            llm = LLM(cfg(), lambda m: None)
            llm.load_ctx(f)
            sent = []

            def fake(req, timeout=0):
                sent.append(json.loads(req.data))
                return io.BytesIO(b"")
            with mock.patch.object(cfllm.urllib.request, "urlopen", fake), mock.patch.object(LLM, "_read", lambda *a, **k: ("ok", "")):
                llm.chat("impact", "planner", "sys", "u" * 20000)
            self.assertEqual(len(sent), 1)
            self.assertLessEqual(sum(len(m["content"]) for m in sent[0]["messages"]), int(4096 * 0.6 * 3))

    def test_fit_stays_within_budget_in_one_step(self):
        from core.cfllm import fit_to_ctx
        s, u = fit_to_ctx("s" * 6000, "u" * 20000, 4096)
        self.assertGreater(len(s) + len(u), 6500)
        self.assertLessEqual(len(s) + len(u), int(4096 * 0.6 * 3) + 40)


class CtxTests(unittest.TestCase):
    MSG = 'request (5565 tokens) exceeds the available context size (4096 tokens), try increasing it'

    def test_server_ctx_is_parsed_and_prompt_fits_it(self):
        from core.cfllm import fit_to_ctx, server_ctx
        self.assertEqual(server_ctx(self.MSG), 4096)
        s, u = fit_to_ctx("s" * 4000, "u" * 21000, 4096)
        self.assertLessEqual((len(s) + len(u)) / 3.0, 4096 * 0.6 + 200)

    def test_learned_ctx_cuts_later_prompts_and_answer_budget(self):
        import json
        sent = []

        def fake(req, timeout=0):
            body = json.loads(req.data)
            sent.append(body)
            if len(sent) == 1:
                raise http400(json.dumps({"error": {"message": self.MSG}}).encode())
            return io.BytesIO(b"")
        llm = LLM(cfg(), lambda m: None)
        with mock.patch.object(cfllm.urllib.request, "urlopen", fake), mock.patch.object(LLM, "_read", lambda *a, **k: ("ok", "")):
            llm.chat("impact", "planner", "sys", "u" * 21000)
            llm.chat("again", "planner", "sys", "u" * 21000)
        self.assertEqual(llm.server_ctx, 4096)
        self.assertEqual(len(sent), 3)  # the second call fits at the first try
        self.assertLessEqual(sum(len(m["content"]) for m in sent[2]["messages"]) / 3.0, 4096)
        self.assertLessEqual(sent[2]["max_tokens"], 1700)
