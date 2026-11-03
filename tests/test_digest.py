import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from infra import digest, notify  # noqa: E402


class DigestTests(unittest.TestCase):
    def test_quiet_hours_wrap_over_midnight(self):
        self.assertTrue(digest.in_quiet("23:00-07:00", "02:00"))
        self.assertTrue(digest.in_quiet("23:00-07:00", "23:30"))
        self.assertFalse(digest.in_quiet("23:00-07:00", "12:00"))
        self.assertFalse(digest.in_quiet("", "02:00"))

    def test_message_has_the_key_figures(self):
        done = [{"id": "T0001", "title": "Login", "runtime_s": 600}]
        txt = digest.build(done, [{"cls": "no_edit"}, {"cls": "no_edit"}], [{"status": "pending"}, {"status": "blocked"}], "T0002 x", 3600, 5, True)
        for part in ("1 task(s) done", "T0001", "no_edit 2x", "1 open, 1 blocked", "5 merged", "now: T0002 x"):
            self.assertIn(part, txt)

    def test_done_is_not_pushed_per_task_while_a_digest_runs(self):
        cfg = {"notify": {"webhook": "https://ntfy.sh/x", "digest_minutes": 60}}
        with mock.patch("infra.notify.threading.Thread") as th:
            self.assertFalse(notify.send(cfg, "done", "p", "t"))
            self.assertTrue(notify.send(cfg, "blocked", "p", "t"))
        self.assertEqual(th.call_count, 1)

    def test_tick_sends_one_digest_per_period_and_idle_alert_once(self):
        sent = []
        agent = SimpleNamespace(cfg={"notify": {"webhook": "https://ntfy.sh/x", "digest_minutes": 60, "idle_minutes": 30}}, state={}, cur_task="",
                                ws=Path("/tmp/proj"), dir=Path("/tmp/none"), backlog=SimpleNamespace(items=[{"id": "T1", "status": "pending"}]))
        with mock.patch("infra.notify.send", lambda cfg, ev, proj, text, wait=False: sent.append(ev)), mock.patch("infra.digest.in_quiet", lambda *a: False):
            digest.tick(agent, 0)
            digest.tick(agent, 40 * 60)
            digest.tick(agent, 50 * 60)
            digest.tick(agent, 61 * 60)
        self.assertEqual(sent, ["idle", "digest"])

    def test_idle_alert_is_not_reset_by_an_unsent_digest(self):
        sent = []
        agent = SimpleNamespace(cfg={"notify": {"webhook": "https://ntfy.sh/x", "digest_minutes": 600, "idle_minutes": 30}}, state={}, cur_task="",
                                ws=Path("/tmp/proj"), dir=Path("/tmp/none"), backlog=SimpleNamespace(items=[{"id": "T1", "status": "pending"}]))
        with mock.patch("infra.notify.send", lambda cfg, ev, proj, text, wait=False: sent.append(ev)):
            digest.tick(agent, 0)
            agent.backlog.items[0]["status"] = "done"
            digest.tick(agent, 60)
            digest.tick(agent, 20 * 60)
            digest.tick(agent, 100 * 60)
        self.assertEqual(sent, ["idle"])


if __name__ == "__main__":
    unittest.main()
