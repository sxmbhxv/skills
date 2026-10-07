# 8848 project portal: setup, script reference, troubleshooting

The portal is a Frappe site that uses the Projects module. Work items are **Task** documents. Their IDs are
usually `TASK-YYYY-NNNNN`, and each task opens at `<portal>/app/task/<id>`. Tasks belong to a **Project**
(`project` field, which holds the Project's `name`; the human title is `project_name`).

## Contents
1. Setup (environment variables)
2. `portal.py` commands and output
3. Troubleshooting
4. Raw API equivalents

---

## 1. Setup

The skill reads everything from environment variables, so no credentials ever live in a file inside a repo
or in the chat transcript.

| Variable | Required | Meaning |
|---|---|---|
| `PORTAL_8848_URL` | yes | Portal base URL, e.g. `https://projects.example.com` (no `/app/...`) |
| `PORTAL_8848_USERNAME` | yes* | **Default.** The portal login email |
| `PORTAL_8848_PASSWORD` | yes* | Password of that account |
| `PORTAL_8848_API_KEY` / `PORTAL_8848_API_SECRET` | alt* | Only for accounts that can't log in with a password (Google/SSO, two-factor) |
| `PORTAL_8848_REMARKS_FIELD` | no | Task field that gets the GitHub link. Default `task_remarks` |

\* Email + password is the default. When both pairs are set, email + password is used.

### Default: email + password

Give the user these steps. They should do them in their own terminal, because the password must not pass through the chat.

1. Put the variables in a private file:
   ```bash
   mkdir -p ~/.config && nano ~/.config/portal-8848.env
   ```
   with this content:
   ```bash
   export PORTAL_8848_URL="https://<portal-host>"
   export PORTAL_8848_USERNAME='you@8848digital.com'
   export PORTAL_8848_PASSWORD='your-password'
   ```
2. Lock the file down and load it from the shell profile (`~/.zshrc` on zsh):
   ```bash
   chmod 600 ~/.config/portal-8848.env
   echo '[ -f ~/.config/portal-8848.env ] && . ~/.config/portal-8848.env' >> ~/.bashrc
   ```
3. Restart Claude Code so new shells pick the variables up, then run
   `python3 ~/.claude/skills/8848-triage/scripts/portal.py check`. It should print `"auth": "password"` and the user's email.

Points to tell the user:
- **Single quotes around the password.** Inside double quotes the shell expands `$`, `!` and backticks,
  so the password the script receives would be wrong. A literal `'` inside the password is written `'\''`.
- A separate `chmod 600` file keeps the password out of `~/.bashrc`, which tends to get shared and backed up.
  Putting the exports directly in `~/.bashrc` works too.
- Each `portal.py` command logs in, does its work, and logs out again. Logins therefore show up in the portal's
  activity log, and the user's browser session is left alone.

### Alternative: API key + secret

Use this when password login fails because the account signs in with Google/SSO or has two-factor auth enabled.
In the portal, open the avatar menu, then **My Settings**, then the **API Access** section, then
**Generate Keys** (the secret is shown only once). If the section is missing, the user's role isn't allowed to
generate keys, and a portal admin has to do it. Put `PORTAL_8848_API_KEY` and `PORTAL_8848_API_SECRET` in the
same env file and remove the username/password lines, because the email + password pair wins when both are set.

### Rules for Claude

Don't ask the user to paste the password or secret into the chat. Never echo the variables' values, and
never write them to a file yourself. To check which variables are set without revealing them, run
`env | cut -d= -f1 | grep PORTAL_8848`.

---

## 2. `portal.py` commands

Run it as `python3 ~/.claude/skills/8848-triage/scripts/portal.py <command>`. It uses only the standard
library. Output is JSON on stdout. Errors go to stderr as `portal.py: HTTP <code>: <frappe message>` and
exit with code 1.

| Command | Does |
|---|---|
| `check` | Calls `frappe.auth.get_logged_user`. Prints `{url, user, auth, remarks_field}` |
| `task <id or url>` | Full task (see below). Accepts `TASK-…`, `…/app/task/TASK-…`, or `#Form/Task/TASK-…` |
| `find [--project P] [--text T] [--mine] [--all] [--limit N]` | Lists tasks. `P` is a Project name **or** part of a `project_name`. `--text` matches the subject. `--mine` keeps only tasks assigned to the logged-in user. Completed and Cancelled tasks are hidden unless `--all` is given |
| `download <id> <dir>` | Saves every attachment and every inline `<img>` from the description and comments. Prints the saved paths. Open the images with Read |
| `link-issue <id> --url <issue> [--label L] [--field F] [--dry-run]` | Appends `GitHub Issue: <url>` to the remarks field. Existing content is kept. Running it again with the same URL is a no-op |
| `api <path> [--params JSON]` | Read-only GET for anything else, e.g. `api /api/resource/Project/PROJ-0042` |

`task` output keys:
- `subject`, `status`, `priority`, `type`, `project`, `exp_start_date`, `exp_end_date`, `progress`, `url`
- `description` is plain text. `[image N]` marks where an inline image was, and the images are listed in `inline_images`
- `comments[]` (`by`, `at`, `text`) oldest first, and `emails[]` for communications on the task
- `attachments[]` (`file_name`, `file_url`, `is_private`)
- `other_fields` holds non-empty fields not listed above. Custom fields such as module, client or acceptance criteria end up here
- `child_tables` holds child rows (except `depends_on`, which is flattened into a list of task IDs)
- `parent_task`, `depends_on[]`. Fetch these with `task` when they could change the scope
- `remarks`, `remarks_field`, `remarks_field_exists`, `linked_github_issues[]` (issue URLs already in remarks)
- `project_details` (`project_name`, `status`, `customer`, `notes`), `repo_hints[]` (GitHub repo URLs found on the task or project)

The remarks field's type decides the format of the appended line:
- Text Editor or HTML: appends `<p>GitHub Issue: <a href=…>…</a></p>`.
- Small Text, Text or Long Text: appends a new line.
- Data: appends ` | GitHub Issue: …`.

The script reads the field type from the doctype meta. When the meta can't be read, it guesses from the current value.

---

## 3. Troubleshooting

| Symptom | Likely cause, and what to do |
|---|---|
| `PORTAL_8848_URL is not set` / `no credentials` | Variables not exported in this shell. Give the user the setup steps above. They need to restart Claude Code after editing the profile |
| `HTTP 401 … login as <email> failed` | Wrong email or password, often a password with `$`/`!` in double quotes (see the single-quote note). Ask the user to log in to the portal in a browser with the same email. If the account uses Google/SSO or two-factor auth, switch to the API key pair |
| `HTTP 401` with an API key pair | Wrong key or secret, or the keys were regenerated, which invalidates the old secret |
| User says the portal logged them out in the browser | The site limits simultaneous sessions per user. `portal.py` logs out after every command, so this should be rare. If it keeps happening, switch that user to the API key pair, which doesn't create sessions |
| `HTTP 403` on `task` | The user can't read this task (not assigned / project permission). Ask them to check the task in the browser |
| `HTTP 403` on `link-issue` | Read access but no write access to Task. Ask the user to add the link by hand, or to get write permission. Give them the exact line to paste |
| `HTTP 404` | Wrong ID (check the prefix/year), or a site URL for a different Frappe site |
| `HTTP 417` / validation message on `link-issue` | Saving the Task ran its validations and one failed (e.g. dates, a mandatory field, a closed project). Show the message to the user. Don't "fix" other task fields to get past it |
| `non-JSON response` | The URL points to a login page or website, not the Frappe site. Check `PORTAL_8848_URL` |
| `Task has no field 'task_remarks'` | The field name differs on this site. Ask the user for it (they can see it in Customize Form → Task), then set `PORTAL_8848_REMARKS_FIELD` or pass `--field` |
| `note: comments/attachments unavailable` | `frappe.desk.form.load.getdoc` was blocked, so only task fields came back. Ask the user whether there are comments or screenshots on the task you should know about |

---

## 4. Raw API equivalents (for debugging)

Email + password, using a cookie jar in a temp file that is deleted afterwards:

```bash
J=$(mktemp)
jq -n --arg u "$PORTAL_8848_USERNAME" --arg p "$PORTAL_8848_PASSWORD" '{usr:$u,pwd:$p}' |
  curl -s -c "$J" -H 'Content-Type: application/json' -d @- "$PORTAL_8848_URL/api/method/login"
curl -s -b "$J" "$PORTAL_8848_URL/api/method/frappe.auth.get_logged_user"
curl -s -b "$J" "$PORTAL_8848_URL/api/method/frappe.desk.form.load.getdoc?doctype=Task&name=TASK-2026-01234"
curl -s -b "$J" -G "$PORTAL_8848_URL/api/resource/Task" \
  --data-urlencode 'filters=[["project","=","PROJ-0042"]]' --data-urlencode 'fields=["name","subject","status"]'
curl -s -b "$J" -X POST "$PORTAL_8848_URL/api/method/logout"; rm -f "$J"
```

API key pair: replace `-b "$J"` with `-H "Authorization: token $PORTAL_8848_API_KEY:$PORTAL_8848_API_SECRET"`.
Don't print that header, because it contains the secret.
