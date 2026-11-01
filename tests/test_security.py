import http.server
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from infra import security  # noqa: E402


def rules(findings):
    return {f["rule"] for f in findings}


class StaticRules(unittest.TestCase):
    def scan(self, name, text):
        return rules(security.scan_text(name, text))

    def test_secrets(self):
        self.assertIn("secret-aws", self.scan("a.py", 'k = "AKIAABCDEFGHIJKLMNOP"'))
        self.assertIn("secret-assignment", self.scan("a.py", 'api_key = "Zx81hD92kslQpw0Lm3"'))
        self.assertNotIn("secret-assignment", self.scan("a.py", 'api_key = os.environ["API_KEY"]'))
        self.assertNotIn("secret-assignment", self.scan("a.py", 'api_key = "your-api-key-here"'))

    def test_dangerous_calls(self):
        self.assertIn("code-eval", self.scan("a.py", "x = eval(data)"))
        self.assertIn("cmd-shell-true", self.scan("a.py", "subprocess.run(cmd, shell=True)"))
        self.assertIn("deserialize-pickle", self.scan("a.py", "pickle.loads(blob)"))
        self.assertIn("deserialize-yaml", self.scan("a.py", "yaml.load(f)"))
        self.assertNotIn("deserialize-yaml", self.scan("a.py", "yaml.load(f, Loader=yaml.SafeLoader)"))
        self.assertIn("tls-verify-off", self.scan("a.py", "requests.get(u, verify=False)"))

    def test_passwords_and_sql(self):
        self.assertIn("password-weak-hash", self.scan("a.py", "h = hashlib.sha256(password.encode()).hexdigest()"))
        self.assertIn("sql-concat", self.scan("a.py", 'cur.execute(f"SELECT * FROM u WHERE id={uid}")'))
        self.assertNotIn("sql-concat", self.scan("a.py", 'cur.execute("SELECT * FROM u WHERE id=?", (uid,))'))

    def test_xss_and_nosec(self):
        self.assertIn("xss-innerhtml", self.scan("a.js", "el.innerHTML = userInput"))
        self.assertEqual(self.scan("a.py", "x = eval(data)  # nosec"), set())

    def test_tests_are_downgraded(self):
        f = security.scan_text("tests/test_a.py", "x = eval(data)")
        self.assertEqual(f[0]["severity"], "low")


class RepoScan(unittest.TestCase):
    def test_env_file_and_gitignore(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / ".env").write_text("SECRET=1")
            (repo / "app.py").write_text("import hashlib\nhashlib.md5(password)\n")
            found = security.scan_repo(repo)
            self.assertIn("gitignore-env", rules(found))
            self.assertIn("password-weak-hash", rules(found))
            self.assertEqual(found[0]["rule"], "secret-file")
            self.assertEqual(security.summary(found)["critical"], 1)
            self.assertEqual(security.summary(found)["high"], 1)
            self.assertTrue(security.blocking(found, "high"))

    def test_only_limits_scope(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "a.py").write_text("eval(x)\n")
            (repo / "b.py").write_text("eval(y)\n")
            self.assertEqual({f["file"] for f in security.scan_repo(repo, ["a.py"])}, {"a.py"})


class Probe(unittest.TestCase):
    def serve(self, handler):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}"

    def test_leaky_server_is_flagged(self):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b"SECRET=abc" if self.path == "/.env" else b"<html>ok</html>")

        found = rules(security.probe(self.serve(H)))
        self.assertIn("exposed-path", found)
        self.assertIn("cors-open", found)
        self.assertIn("http-header-content-security-policy", found)

    def test_non_local_is_refused(self):
        self.assertEqual(security.probe("http://example.com"), [])


@unittest.skipUnless((Path(__file__).resolve().parent.parent / "docs").is_dir(), "docs not present (sandbox copy)")
class DocsTests(unittest.TestCase):
    def test_overview_and_security_docs_exist(self):
        docs = Path(__file__).resolve().parent.parent / "docs"
        text = (docs / "OVERVIEW.md").read_text()
        for word in ("Security", "Memory", "Web UI", "Models"):
            self.assertIn(word, text)
        self.assertTrue((docs / "SECURITY.md").exists())


if __name__ == "__main__":
    unittest.main()
