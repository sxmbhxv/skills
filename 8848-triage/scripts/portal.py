#!/usr/bin/env python3
"""Client for the 8848 project portal (Frappe Projects: Project + Task doctypes).

Configuration comes from the environment, never from files:
  PORTAL_8848_URL             base URL of the portal, e.g. https://projects.example.com
  PORTAL_8848_API_KEY         } token auth (preferred)
  PORTAL_8848_API_SECRET      }
  PORTAL_8848_USERNAME        } fallback: session login
  PORTAL_8848_PASSWORD        }
  PORTAL_8848_REMARKS_FIELD   Task field that receives the GitHub link (default: task_remarks)

Every command prints JSON on stdout. Errors go to stderr with exit code 1.

  portal.py check                                  verify URL + credentials, print logged-in user
  portal.py task <id|url>                          task with description, comments, attachments, project
  portal.py find [--project P] [--text T] [--mine] [--all] [--limit N]
  portal.py download <id|url> <dir>                save attachments + inline images for viewing
  portal.py link-issue <id|url> --url <issue-url> [--label L] [--field F] [--dry-run]
  portal.py api <path> [--params JSON]             read-only GET, for anything not covered above
"""

import argparse
import base64
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from http.cookiejar import CookieJar

ENV_URL = "PORTAL_8848_URL"
ENV_KEY = "PORTAL_8848_API_KEY"
ENV_SECRET = "PORTAL_8848_API_SECRET"
ENV_USER = "PORTAL_8848_USERNAME"
ENV_PASSWORD = "PORTAL_8848_PASSWORD"
ENV_FIELD = "PORTAL_8848_REMARKS_FIELD"
DEFAULT_FIELD = "task_remarks"

# Bookkeeping columns that never carry requirements.
NOISE = {
    "doctype", "name", "owner", "creation", "modified", "modified_by", "docstatus", "idx",
    "parent", "parentfield", "parenttype", "lft", "rgt", "old_parent", "_user_tags",
    "_comments", "_assign", "_liked_by", "_seen", "__onload", "__last_sync_on", "__unsaved",
}
GITHUB_ISSUE_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/issues/\d+")
GITHUB_REPO_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+")


class PortalError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def die(message):
    print(f"portal.py: {message}", file=sys.stderr)
    sys.exit(1)


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


# ---------------------------------------------------------------- HTML -> text

class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.images, self._hrefs = [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li":
            self.out.append("\n- ")
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "img" and a.get("src"):
            self.images.append(a["src"])
            self.out.append(f"[image {len(self.images)}]")
        if tag == "a":
            self._hrefs.append(a.get("href") or "")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag == "a" and self._hrefs:
            href = self._hrefs.pop()
            if href and not href.startswith("#"):
                self.out.append(f" ({href})")

    def handle_data(self, data):
        self.out.append(data)


def looks_html(value):
    return isinstance(value, str) and re.search(r"<[a-zA-Z][^>]*>", value) is not None


def to_text(value):
    """Plain text plus the <img> sources found in it (images often carry the real requirement)."""
    if not isinstance(value, str):
        return value, []
    if not looks_html(value):
        return value.strip(), []
    parser = _TextExtractor()
    parser.feed(value)
    text = "".join(parser.out).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, parser.images


# ---------------------------------------------------------------- HTTP client

def _frappe_error(body, code):
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        snippet = body[:300].decode("utf-8", "replace") if isinstance(body, bytes) else str(body)[:300]
        return f"HTTP {code}: {snippet}"
    messages = []
    if payload.get("_server_messages"):
        try:
            for item in json.loads(payload["_server_messages"]):
                try:
                    messages.append(json.loads(item).get("message", item))
                except (ValueError, AttributeError):
                    messages.append(item)
        except ValueError:
            pass
    if not messages and payload.get("exception"):
        messages.append(str(payload["exception"]).strip().splitlines()[-1])
    if not messages and payload.get("exc_type"):
        messages.append(payload["exc_type"])
    if not messages and payload.get("message"):
        messages.append(str(payload["message"]))
    text = to_text(" | ".join(str(m) for m in messages))[0] or "no message"
    return f"HTTP {code}: {text}"


class Portal:
    def __init__(self):
        base = os.environ.get(ENV_URL, "").strip().rstrip("/")
        if not base:
            die(f"{ENV_URL} is not set. Export the portal base URL, e.g. export {ENV_URL}=https://projects.example.com")
        if not re.match(r"https?://", base):
            base = "https://" + base
        # Tolerate a pasted desk URL such as https://host/app/task
        self.base = re.sub(r"/(app|desk)(/.*)?$", "", base)
        self.host = urllib.parse.urlparse(self.base).netloc
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self.headers = {"Accept": "application/json"}
        key, secret = os.environ.get(ENV_KEY, "").strip(), os.environ.get(ENV_SECRET, "").strip()
        user, password = os.environ.get(ENV_USER, "").strip(), os.environ.get(ENV_PASSWORD, "")
        if key and secret:
            self.headers["Authorization"] = f"token {key}:{secret}"
            self.auth = "token"
        elif user and password:
            self.auth = "password"
            self.request("POST", "/api/method/login", body={"usr": user, "pwd": password})
        else:
            die(f"no credentials. Export {ENV_KEY} and {ENV_SECRET} (or {ENV_USER} and {ENV_PASSWORD}).")

    def _url(self, path, params=None):
        url = path if path.startswith("http") else self.base + path
        if params:
            encoded = {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in params.items()}
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(encoded)
        return url

    def request(self, method, path, params=None, body=None, raw=False):
        url = self._url(path, params)
        same_host = urllib.parse.urlparse(url).netloc == self.host
        # Never send portal credentials to another host (attachments can be external links).
        headers = dict(self.headers) if same_host else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        opener = self.opener if same_host else urllib.request.build_opener()
        try:
            with opener.open(req, timeout=60) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            raise PortalError(e.code, _frappe_error(e.read(), e.code)) from None
        except urllib.error.URLError as e:
            raise PortalError(0, f"cannot reach {url.split('?')[0]}: {e.reason}") from None
        if raw:
            return payload
        try:
            return json.loads(payload or b"{}")
        except ValueError:
            raise PortalError(0, f"non-JSON response from {url.split('?')[0]} (is {ENV_URL} the Frappe site URL?)") from None

    def get_resource(self, doctype, name):
        return self.request("GET", f"/api/resource/{urllib.parse.quote(doctype)}/{urllib.parse.quote(name, safe='')}")["data"]

    def list_resource(self, doctype, filters, fields, order_by="modified desc", limit=20):
        params = {"filters": filters, "fields": fields, "order_by": order_by, "limit_page_length": limit}
        return self.request("GET", f"/api/resource/{urllib.parse.quote(doctype)}", params)["data"]

    def logged_user(self):
        return self.request("GET", "/api/method/frappe.auth.get_logged_user")["message"]

    def task_url(self, name):
        return f"{self.base}/app/task/{urllib.parse.quote(name, safe='')}"

    def task_field_types(self):
        """fieldname -> fieldtype for Task, custom fields included. Empty if meta isn't readable."""
        try:
            r = self.request("GET", "/api/method/frappe.desk.form.load.getdoctype", {"doctype": "Task"})
        except PortalError:
            return {}
        for d in r.get("docs") or []:
            if d.get("name") == "Task":
                return {f["fieldname"]: f["fieldtype"] for f in d.get("fields", []) if f.get("fieldname")}
        return {}


def task_name(ref):
    """Accept TASK-2026-01234, a /app/task/<id> URL, or an old #Form/Task/<id> URL."""
    ref = ref.strip()
    m = re.search(r"(?:/app/task/|#Form/Task/)([^/?#]+)", ref)
    return urllib.parse.unquote(m.group(1)) if m else ref


def remarks_field(args_field=None):
    return args_field or os.environ.get(ENV_FIELD, "").strip() or DEFAULT_FIELD


# ---------------------------------------------------------------- commands

def load_task(p, ref):
    name = task_name(ref)
    info = {}
    try:
        r = p.request("GET", "/api/method/frappe.desk.form.load.getdoc", {"doctype": "Task", "name": name})
        doc, info = r["docs"][0], r.get("docinfo") or {}
    except PortalError as e:
        if e.code in (0, 401, 404):
            raise
        doc = p.get_resource("Task", name)  # getdoc blocked on some setups; resource API still works
    return doc, info


def shape_task(p, doc, info):
    field = remarks_field()
    images = []

    def text(value):
        t, imgs = to_text(value)
        images.extend(imgs)
        return t

    shown = {"subject", "status", "priority", "project", "type", "exp_start_date", "exp_end_date",
             "parent_task", "progress", "description", "depends_on", field}
    other, tables = {}, {}
    for k, v in doc.items():
        if k in NOISE or k in shown or v in (None, "", 0, [], "0"):
            continue
        if isinstance(v, list):
            tables[k] = [{ck: cv for ck, cv in row.items() if ck not in NOISE and cv not in (None, "")} for row in v]
        else:
            other[k] = text(v)

    try:
        assigned = json.loads(doc.get("_assign") or "[]")
    except ValueError:
        assigned = doc.get("_assign")
    raw_remarks = doc.get(field) or ""

    out = {
        "name": doc.get("name"),
        "url": p.task_url(doc.get("name")),
        "subject": doc.get("subject"),
        "status": doc.get("status"),
        "priority": doc.get("priority"),
        "type": doc.get("type"),
        "project": doc.get("project"),
        "exp_start_date": doc.get("exp_start_date"),
        "exp_end_date": doc.get("exp_end_date"),
        "progress": doc.get("progress"),
        "parent_task": doc.get("parent_task"),
        "depends_on": [row.get("task") for row in doc.get("depends_on") or []],
        "assigned_to": assigned,
        "description": text(doc.get("description")),
        "remarks_field": field,
        "remarks": text(raw_remarks),
        "remarks_field_exists": field in doc,
        "linked_github_issues": sorted(set(GITHUB_ISSUE_RE.findall(str(raw_remarks)))),
        "other_fields": other,
        "child_tables": tables,
        "comments": [
            {"by": c.get("owner"), "at": c.get("creation"), "text": text(c.get("content"))}
            for c in info.get("comments") or []
            if c.get("comment_type", "Comment") == "Comment"
        ],
        "emails": [
            {"from": c.get("sender"), "at": c.get("communication_date") or c.get("creation"),
             "subject": c.get("subject"), "text": text(c.get("content"))}
            for c in info.get("communications") or []
        ],
        "attachments": [
            {"file_name": a.get("file_name"), "file_url": a.get("file_url"), "is_private": a.get("is_private")}
            for a in info.get("attachments") or []
        ],
    }
    if not info:
        out["note"] = "comments/attachments unavailable (getdoc not permitted); only task fields shown"

    if doc.get("project"):
        try:
            proj = p.get_resource("Project", doc["project"])
            notes = to_text(proj.get("notes"))[0]
            out["project_details"] = {
                "project_name": proj.get("project_name"),
                "status": proj.get("status"),
                "customer": proj.get("customer"),
                "notes": notes[:3000] if isinstance(notes, str) else notes,
            }
            out["repo_hints"] = sorted(set(GITHUB_REPO_RE.findall(json.dumps(proj, default=str))))
        except PortalError as e:
            out["project_details"] = {"error": str(e)}

    hints = set(out.get("repo_hints", [])) | set(GITHUB_REPO_RE.findall(json.dumps(doc, default=str)))
    out["repo_hints"] = sorted(h for h in hints if "/issues" not in h)
    out["inline_images"] = [
        src if not src.startswith("data:") else f"(embedded {src[5:src.find(';')]}, use download)"
        for src in images
    ]
    return out


def cmd_check(p, args):
    emit({"url": p.base, "user": p.logged_user(), "auth": p.auth, "remarks_field": remarks_field()})


def cmd_task(p, args):
    doc, info = load_task(p, args.task)
    emit(shape_task(p, doc, info))


def cmd_find(p, args):
    filters, projects = [], []
    if args.project:
        try:
            proj = p.get_resource("Project", args.project)
            projects = [{"name": proj["name"], "project_name": proj.get("project_name"), "status": proj.get("status")}]
        except PortalError as e:
            if e.code != 404:
                raise
            projects = p.list_resource("Project", [["project_name", "like", f"%{args.project}%"]],
                                       ["name", "project_name", "status"], limit=20)
        if not projects:
            die(f"no project matches {args.project!r}")
        filters.append(["project", "in", [x["name"] for x in projects]])
    if not args.all:
        filters.append(["status", "not in", ["Completed", "Cancelled", "Template"]])
    if args.text:
        filters.append(["subject", "like", f"%{args.text}%"])
    if args.mine:
        filters.append(["_assign", "like", f"%{p.logged_user()}%"])
    tasks = p.list_resource("Task", filters,
                            ["name", "subject", "status", "priority", "project", "exp_end_date", "_assign", "modified"],
                            limit=args.limit)
    for t in tasks:
        t["url"] = p.task_url(t["name"])
    emit({"projects": projects, "tasks": tasks})


def _safe_name(name):
    return re.sub(r"[^\w.\-]+", "_", name).strip("._") or "file"


def cmd_download(p, args):
    doc, info = load_task(p, args.task)
    os.makedirs(args.dir, exist_ok=True)
    sources = [(a.get("file_name") or a.get("file_url", "").rsplit("/", 1)[-1], a.get("file_url"))
               for a in info.get("attachments") or [] if a.get("file_url")]
    blobs = [doc.get("description")] + [c.get("content") for c in info.get("comments") or []]
    for blob in blobs:
        for src in to_text(blob)[1]:
            sources.append((f"inline-{len(sources) + 1}", src))

    saved, failed, used = [], [], set()
    for label, src in sources:
        try:
            if src.startswith("data:"):
                header, _, payload = src.partition(",")
                ext = header[5:].split(";")[0].split("/")[-1] or "bin"
                data = base64.b64decode(payload) if ";base64" in header else urllib.parse.unquote(payload).encode()
                label = f"{label}.{ext}"
            else:
                data = p.request("GET", src if src.startswith("http") else p.base + src, raw=True)
                if "." not in label:
                    label += os.path.splitext(urllib.parse.urlparse(src).path)[1]
            fname = _safe_name(urllib.parse.unquote(label))
            stem, ext = os.path.splitext(fname)
            n = 1
            while fname in used:
                n += 1
                fname = f"{stem}-{n}{ext}"
            used.add(fname)
            path = os.path.join(args.dir, fname)
            with open(path, "wb") as fh:
                fh.write(data)
            saved.append({"path": os.path.abspath(path), "bytes": len(data), "source": src[:120]})
        except (PortalError, ValueError, OSError) as e:
            failed.append({"source": src[:120], "error": str(e)})
    emit({"task": doc.get("name"), "saved": saved, "failed": failed})


def cmd_link_issue(p, args):
    name = task_name(args.task)
    field = remarks_field(args.field)
    if not GITHUB_ISSUE_RE.fullmatch(args.url.strip()):
        die(f"--url should be a GitHub issue URL, got {args.url!r}")
    url = args.url.strip()
    doc = p.get_resource("Task", name)
    if field not in doc:
        die(f"Task has no field {field!r}. Set {ENV_FIELD} to the right fieldname, or pass --field.")
    current = doc.get(field) or ""
    if url in current:
        emit({"status": "already-present", "task": name, "field": field})
        return

    ftype = p.task_field_types().get(field) or ("Text Editor" if looks_html(current) else "Small Text")
    label = args.label
    if ftype in ("Text Editor", "HTML Editor") or looks_html(current):
        new = current + f'<p>{html.escape(label)}: <a href="{html.escape(url, quote=True)}">{html.escape(url)}</a></p>'
    elif ftype == "Data":
        new = f"{current} | {label}: {url}" if current.strip() else f"{label}: {url}"
    else:
        new = f"{current.rstrip()}\n{label}: {url}" if current.strip() else f"{label}: {url}"

    result = {"task": name, "field": field, "fieldtype": ftype, "new_value": to_text(new)[0]}
    if args.dry_run:
        emit({"status": "dry-run", **result})
        return
    p.request("PUT", f"/api/resource/Task/{urllib.parse.quote(name, safe='')}", body={field: new})
    after = p.get_resource("Task", name).get(field) or ""
    if url not in after:
        die(f"saved, but {field} does not contain the link afterwards; check the task in the portal")
    emit({"status": "appended", **result})


def cmd_api(p, args):
    params = json.loads(args.params) if args.params else None
    emit(p.request("GET", args.path, params))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    s = sub.add_parser("task"); s.add_argument("task")
    s = sub.add_parser("find")
    s.add_argument("--project"); s.add_argument("--text"); s.add_argument("--mine", action="store_true")
    s.add_argument("--all", action="store_true", help="include Completed/Cancelled")
    s.add_argument("--limit", type=int, default=30)
    s = sub.add_parser("download"); s.add_argument("task"); s.add_argument("dir")
    s = sub.add_parser("link-issue"); s.add_argument("task"); s.add_argument("--url", required=True)
    s.add_argument("--label", default="GitHub Issue"); s.add_argument("--field")
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("api"); s.add_argument("path"); s.add_argument("--params")
    args = parser.parse_args()

    handlers = {"check": cmd_check, "task": cmd_task, "find": cmd_find, "download": cmd_download,
                "link-issue": cmd_link_issue, "api": cmd_api}
    try:
        handlers[args.cmd](Portal(), args)
    except PortalError as e:
        die(str(e))


if __name__ == "__main__":
    main()
