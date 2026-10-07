"""Exercise portal.py against an in-process mock of the Frappe endpoints it uses.

Run: python3 tests/test_portal.py   (no network, no dependencies)
"""
import base64, json, os, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, unquote

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "8848-triage", "scripts", "portal.py")
KEY, SECRET = "k123", "s456"
EMAIL, PASSWORD = "dev@example.com", "p@ss$w0rd!'`x"  # shell-hostile on purpose
SID = "sess-1"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

TASK = {
    "doctype": "Task", "name": "TASK-2026-01234", "subject": "Order export is empty for multi-year ranges",
    "status": "Open", "priority": "High", "project": "PROJ-0042", "type": "Bug",
    "exp_end_date": "2026-10-15", "parent_task": None, "progress": 0, "owner": "pm@example.com",
    "description": '<div><p>When exporting <b>orders</b> across two years the file is empty.</p><ul><li>Branch office only</li><li>See <a href="https://example.com/spec">spec</a></li></ul><p><img src="/files/shot.png"></p></div>',
    "task_remarks": None, "custom_module": "Reports", "_assign": '["dev@example.com"]',
    "depends_on": [{"task": "TASK-2026-01200", "name": "x1", "idx": 1, "doctype": "Task Depends On"}],
}
FIELD_TYPES = {"task_remarks": "Small Text", "subject": "Data", "description": "Text Editor"}
PROJECT = {"name": "PROJ-0042", "project_name": "Demo Project", "status": "Open",
           "notes": "<p>Repo: https://github.com/example/demo-app</p>", "customer": "Acme"}
STATE = {"puts": [], "logins": 0, "logouts": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, raw=None, ctype="application/json"):
        body = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _authed(self):
        token_ok = self.headers.get("Authorization") == f"token {KEY}:{SECRET}"
        session_ok = f"sid={SID}" in (self.headers.get("Cookie") or "")
        if not (token_ok or session_ok):
            self._send(401, {"exc_type": "AuthenticationError", "_server_messages": json.dumps([json.dumps({"message": "Invalid API key"})])})
            return False
        return True

    def do_GET(self):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self._authed():
            return
        p = u.path
        if p == "/api/method/frappe.auth.get_logged_user":
            return self._send(200, {"message": "dev@example.com"})
        if p == "/api/method/frappe.desk.form.load.getdoc":
            if q.get("name") != TASK["name"]:
                return self._send(404, {"exc_type": "DoesNotExistError", "_server_messages": json.dumps([json.dumps({"message": f"Task {q.get('name')} not found"})])})
            return self._send(200, {"docs": [TASK], "docinfo": {
                "comments": [{"owner": "qa@example.com", "creation": "2026-10-01", "comment_type": "Comment", "content": "<p>Also happens for CSV exports. <img src=\"data:image/png;base64," + base64.b64encode(PNG).decode() + "\"></p>"}],
                "attachments": [{"file_name": "Bug report.pdf", "file_url": "/private/files/bug.pdf", "is_private": 1}],
                "communications": []}})
        if p == "/api/method/frappe.desk.form.load.getdoctype":
            return self._send(200, {"docs": [{"name": "Task", "fields": [{"fieldname": k, "fieldtype": v} for k, v in FIELD_TYPES.items()]}]})
        if p == "/api/resource/Task/" + TASK["name"]:
            return self._send(200, {"data": TASK})
        if p == "/api/resource/Project/PROJ-0042":
            return self._send(200, {"data": PROJECT})
        if p.startswith("/api/resource/Project/"):
            return self._send(404, {"exc_type": "DoesNotExistError"})
        if p == "/api/resource/Project":
            return self._send(200, {"data": [{"name": "PROJ-0042", "project_name": "Demo Project", "status": "Open"}]})
        if p == "/api/resource/Task":
            STATE["last_list"] = q
            return self._send(200, {"data": [{"name": TASK["name"], "subject": TASK["subject"], "status": "Open"}]})
        if p == "/files/shot.png":
            return self._send(200, raw=PNG, ctype="image/png")
        if p == "/private/files/bug.pdf":
            return self._send(200, raw=b"%PDF-1.4 fake", ctype="application/pdf")
        self._send(404, {"exc_type": "DoesNotExistError"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/method/login":
            if body.get("usr") == EMAIL and body.get("pwd") == PASSWORD:
                STATE["logins"] += 1
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"sid={SID}; Path=/; HttpOnly")
                payload = json.dumps({"message": "Logged In", "full_name": "Dev"}).encode()
                self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
                return
            return self._send(401, {"exc_type": "AuthenticationError", "message": "Invalid login credentials"})
        if self.path == "/api/method/logout":
            STATE["logouts"] += 1
            return self._send(200, {"message": None})
        self._send(404, {"exc_type": "DoesNotExistError"})

    def do_PUT(self):
        if not self._authed():
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        STATE["puts"].append(body)
        TASK.update(body)
        self._send(200, {"data": TASK})


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
# Drop any real portal credentials from the caller's shell so they never reach the mock.
clean = {k: v for k, v in os.environ.items() if not k.startswith("PORTAL_8848_")}
env = {**clean, "PORTAL_8848_URL": base + "/app/task", "PORTAL_8848_API_KEY": KEY, "PORTAL_8848_API_SECRET": SECRET}
fails = []


def run(*args, expect=0, e=None):
    r = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, env=e or env)
    if r.returncode != expect:
        fails.append(f"{args}: exit {r.returncode}\n{r.stderr}")
    try:
        return json.loads(r.stdout) if r.stdout else r.stderr
    except ValueError:
        return r.stdout


def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        fails.append(label)


out = run("check")
check(out["user"] == "dev@example.com" and out["auth"] == "token" and out["url"] == base, "check: user/auth/url (strips /app/task)")

err = run("check", expect=1, e={**env, "PORTAL_8848_API_SECRET": "wrong"})
check("HTTP 401" in err and "Invalid API key" in err, "check: bad secret -> readable 401")

t = run("task", base + "/app/task/TASK-2026-01234")
check(t["name"] == "TASK-2026-01234" and t["url"].endswith("/app/task/TASK-2026-01234"), "task: accepts URL, builds task url")
check("the file is empty" in t["description"] and "- Branch office only" in t["description"] and "(https://example.com/spec)" in t["description"], "task: HTML description -> text with bullets + link")
check(t["inline_images"][0] == "/files/shot.png" and any("embedded" in x for x in t["inline_images"]), "task: inline images from description + comments")
check(t["comments"][0]["text"].startswith("Also happens for CSV"), "task: comments as text")
check(t["attachments"][0]["file_url"] == "/private/files/bug.pdf", "task: attachments")
check(t["other_fields"].get("custom_module") == "Reports" and "owner" not in t["other_fields"], "task: custom fields kept, noise dropped")
check(t["depends_on"] == ["TASK-2026-01200"] and t["assigned_to"] == ["dev@example.com"], "task: depends_on + assignees")
check(t["project_details"]["project_name"] == "Demo Project" and t["repo_hints"] == ["https://github.com/example/demo-app"], "task: project details + repo hints")
check(t["remarks_field_exists"] and t["linked_github_issues"] == [], "task: remarks field present, no issue yet")

err = run("task", "TASK-2026-99999", expect=1)
check("HTTP 404" in err, "task: missing id -> 404 message")

f = run("find", "--project", "Demo", "--text", "export", "--mine")
filters = json.loads(STATE["last_list"]["filters"])
check(f["projects"][0]["name"] == "PROJ-0042" and ["project", "in", ["PROJ-0042"]] in filters and ["subject", "like", "%export%"] in filters and any(x[0] == "_assign" for x in filters), "find: project by title, text + mine filters")

with tempfile.TemporaryDirectory() as d:
    dl = run("download", "TASK-2026-01234", d)
    names = sorted(os.path.basename(s["path"]) for s in dl["saved"])
    check(len(dl["saved"]) == 3 and not dl["failed"], f"download: 3 files saved {names}")
    check(open(os.path.join(d, "Bug_report.pdf"), "rb").read().startswith(b"%PDF"), "download: private attachment fetched with auth")
    pngs = [s["path"] for s in dl["saved"] if s["path"].endswith(".png")]
    check(len(pngs) == 2 and all(open(x, "rb").read() == PNG for x in pngs), "download: inline + data-URI images decoded")

ISSUE = "https://github.com/example/demo-app/issues/130"
dr = run("link-issue", "TASK-2026-01234", "--url", ISSUE, "--dry-run")
check(dr["status"] == "dry-run" and not STATE["puts"], "link-issue: dry run writes nothing")
lk = run("link-issue", "TASK-2026-01234", "--url", ISSUE)
check(lk["status"] == "appended" and STATE["puts"][-1] == {"task_remarks": f"GitHub Issue: {ISSUE}"}, "link-issue: appends to empty Small Text")
again = run("link-issue", "TASK-2026-01234", "--url", ISSUE)
check(again["status"] == "already-present" and len(STATE["puts"]) == 1, "link-issue: idempotent")
TASK["task_remarks"] = "Discussed with QA"
run("link-issue", "TASK-2026-01234", "--url", ISSUE.replace("130", "131"))
check(STATE["puts"][-1]["task_remarks"] == "Discussed with QA\nGitHub Issue: " + ISSUE.replace("130", "131"), "link-issue: preserves existing text")
FIELD_TYPES["task_remarks"] = "Text Editor"; TASK["task_remarks"] = "<p>old</p>"
run("link-issue", "TASK-2026-01234", "--url", ISSUE.replace("130", "132"))
check(STATE["puts"][-1]["task_remarks"].startswith("<p>old</p><p>GitHub Issue: <a href="), "link-issue: HTML paragraph for Text Editor")
err = run("link-issue", "TASK-2026-01234", "--url", ISSUE, "--field", "nope", expect=1)
check("no field 'nope'" in err, "link-issue: unknown field -> clear error")
err = run("link-issue", "TASK-2026-01234", "--url", "https://github.com/x/y/pull/3", expect=1)
check("GitHub issue URL" in err, "link-issue: rejects non-issue URL")

# ---- email + password (the default login mode)
pw_env = {**clean, "PORTAL_8848_URL": base, "PORTAL_8848_USERNAME": EMAIL, "PORTAL_8848_PASSWORD": PASSWORD}
STATE["logins"] = STATE["logouts"] = 0
out = run("check", e=pw_env)
check(out["auth"] == "password" and out["user"] == EMAIL, "password: check logs in with email + password")
check(STATE["logins"] == 1 and STATE["logouts"] == 1, "password: session logged out after the command")
both = run("check", e={**pw_env, "PORTAL_8848_API_KEY": KEY, "PORTAL_8848_API_SECRET": SECRET})
check(both["auth"] == "password", "password: wins over API key pair when both are set")
err = run("check", expect=1, e={**pw_env, "PORTAL_8848_PASSWORD": "wrong"})
check("login as dev@example.com failed" in err and "PORTAL_8848_API_KEY" in err, "password: bad password -> hint about SSO/API keys")
t = run("task", "TASK-2026-01234", e=pw_env)
check(t.get("name") == "TASK-2026-01234" and len(t["comments"]) == 1, "password: task readable via session")
before = len(STATE["puts"]); outs = STATE["logouts"]
lk = run("link-issue", "TASK-2026-01234", "--url", ISSUE.replace("130", "140"), e=pw_env)
check(lk.get("status") == "appended" and len(STATE["puts"]) == before + 1, "password: link-issue writes via session")
check(STATE["logouts"] == outs + 1, "password: logout also runs after a write")
logouts = STATE["logouts"]
run("check")
check(STATE["logouts"] == logouts, "token: no logout call for API-key auth")
err = run("check", expect=1, e={**clean, "PORTAL_8848_URL": base})
check("PORTAL_8848_USERNAME" in err, "no credentials -> asks for email + password first")

srv.shutdown()
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILURE(S):\n" + "\n".join(fails))
sys.exit(1 if fails else 0)
