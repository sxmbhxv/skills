# skills

Skills for [Claude Code](https://claude.com/claude-code). Each top-level folder is one skill: a
`SKILL.md` with the instructions Claude follows, plus any scripts and reference docs it uses.

| Skill | What it does |
|---|---|
| [`8848-triage`](8848-triage/SKILL.md) | Turns a task from the 8848 Digital project portal (Frappe) into a GitHub issue and a ready-to-test code change |

---

## 8848-triage

Give Claude a task ID from the project portal and it carries the task to a change you can test right
away:

1. **Preflight.** Checks the portal login and GitHub CLI login, finds the repo you're in, and reads that repo's
   `CLAUDE.md` / `AGENTS.md`. Those files decide the base branch, branch naming, conventions and verification
   commands. If the two files disagree on the base branch, it asks you.
2. **Reads the whole task:** description, comments (oldest to newest), custom fields, parent and dependent tasks,
   and project notes. It downloads attachments and screenshots and looks at them, then writes down the goal,
   the acceptance criteria and what's out of scope.
3. **Asks questions** when the requirement is ambiguous. It asks before creating the issue, so the issue records the
   answer rather than a guess.
4. **Creates the GitHub issue** in the repo's existing issue style, with the task ID in the title and a link to the
   portal task. It searches for an existing issue first, so re-runs never open a duplicate.
5. **Links back on the portal** by appending `GitHub Issue: <url>` to the task's `task_remarks` field,
   keeping whatever text is already there.
6. **Creates a branch** named `fix/task-2026-01234-<slug>` (or `feat/…`) from `origin/<base>`, with `--no-track` so a bare
   `git push` can't land in the base branch. If you have uncommitted changes, it asks first (stash, carry over, or
   stop).
7. **Analyzes, implements and verifies.** It traces the code end to end, reads git history and past PRs, maps every
   variant the change touches, makes the smallest complete change, and runs the checks the instructions file
   lists.
8. **Hands over a summary**: what changed and why, step-by-step test instructions with expected results,
   regression checks, what it couldn't verify, and a suggested PR title and body.

**It never commits, pushes or opens the PR.** You test the changes, commit them and raise the PR.

### Requirements

- Claude Code
- [GitHub CLI](https://cli.github.com/) logged in with `repo` scope (`gh auth status`)
- Python 3.8+ (the portal helper uses only the standard library)
- An account on the project portal with read access to Tasks and write access to the remarks field
- Works best in repos that have a `CLAUDE.md` or `AGENTS.md`

### Install

```bash
git clone https://github.com/sxmbhxv/skills.git ~/code/skills
mkdir -p ~/.claude/skills
ln -s ~/code/skills/8848-triage ~/.claude/skills/8848-triage
```

The symlink means a `git pull` updates the installed skill.

### Configure

You log in with your **portal email and password**. Credentials come only from environment variables, so they never
end up in a repo or a chat transcript. Set them up in your own terminal. Don't paste the password into Claude.

1. Create a private env file:

   ```bash
   mkdir -p ~/.config && nano ~/.config/portal-8848.env
   ```

   ```bash
   export PORTAL_8848_URL="https://<portal-host>"
   export PORTAL_8848_USERNAME='you@8848digital.com'
   export PORTAL_8848_PASSWORD='your-password'
   ```

2. Lock it down and load it from your shell profile (use `~/.zshrc` on zsh):

   ```bash
   chmod 600 ~/.config/portal-8848.env
   echo '[ -f ~/.config/portal-8848.env ] && . ~/.config/portal-8848.env' >> ~/.bashrc
   ```

3. Restart Claude Code, then check the setup:

   ```bash
   python3 ~/.claude/skills/8848-triage/scripts/portal.py check
   ```

   It should print `"auth": "password"` and your email.

Notes:
- **Use single quotes around the password.** Inside double quotes the shell expands `$`, `!` and backticks, and the
  login fails. Write a literal `'` in the password as `'\''`.
- Putting the exports straight into `~/.bashrc` also works. The separate `chmod 600` file just keeps the password out
  of a file that tends to get shared and backed up.
- Each portal command logs in and then logs out again. Logins show up in the portal's activity log, and your browser
  session isn't affected.

| Variable | Required | Meaning |
|---|---|---|
| `PORTAL_8848_URL` | yes | Portal base URL (no `/app/...` suffix) |
| `PORTAL_8848_USERNAME` | yes* | Your portal login email |
| `PORTAL_8848_PASSWORD` | yes* | Your portal password |
| `PORTAL_8848_API_KEY` / `PORTAL_8848_API_SECRET` | alt* | Only if your account can't log in with a password (see below) |
| `PORTAL_8848_REMARKS_FIELD` | no | Task field that receives the GitHub link. Default `task_remarks` |

\* Email + password is the default. If both pairs are set, email + password is used.

**If your account signs in with Google/SSO or has two-factor auth**, password login fails with `HTTP 401`. Use an
API key instead: in the portal, open the avatar menu, then **My Settings**, then **API Access**, and click **Generate Keys** (the secret is shown only
once). Put `PORTAL_8848_API_KEY` and `PORTAL_8848_API_SECRET` in the env file in place of the username and password lines.

### Use

In Claude Code, from inside the repo the task belongs to:

```text
/8848-triage TASK-2026-01234
/8848-triage https://<portal-host>/app/task/TASK-2026-01234
/8848-triage project "Mobile App" — the task about the export button
/8848-triage TASK-2026-01234 dry run
```

It also triggers without the slash command, e.g. *"pick up TASK-2026-01234 from the portal"*.

A **dry run** reads and analyses the task but doesn't create the issue or write to the portal. It shows you
the drafted issue and the remarks line instead. Use it on a new repo or task type to check its reading of the
task before anything is published.

Every step is safe to re-run. An existing issue is found and reused, the remarks link isn't added twice, and an
existing branch for the task is switched to rather than recreated.

### What it writes

| Where | What |
|---|---|
| GitHub | One issue in the current repo, assigned to you. Uses only labels that already exist |
| Portal | One appended line in the task's remarks field. No other task fields are touched |
| Local git | A new branch with uncommitted changes, plus a stash if you chose to stash local work |

### Portal helper

`8848-triage/scripts/portal.py` is the client Claude uses to talk to the portal. It prints JSON and you can also run
it by hand:

| Command | Does |
|---|---|
| `check` | Verifies the URL and credentials and prints the logged-in user |
| `task <id or url>` | Task with description as text, comments, attachments, custom fields, project notes |
| `find [--project P] [--text T] [--mine] [--all]` | Lists tasks by project name, subject text, or tasks assigned to you |
| `download <id> <dir>` | Saves attachments and inline screenshots |
| `link-issue <id> --url <issue> [--dry-run]` | Appends the issue link to the remarks field. Does nothing if the link is already there |
| `api <path> [--params JSON]` | Read-only GET for anything else |

The error table and the raw API equivalents are in
[`8848-triage/references/frappe-portal.md`](8848-triage/references/frappe-portal.md).

### Tests

```bash
python3 tests/test_portal.py
```

The tests run `portal.py` against an in-process mock of the Frappe endpoints it uses. They need no network and no
dependencies.

---

## Layout

```
.
├── 8848-triage/
│   ├── SKILL.md                    workflow Claude follows
│   ├── scripts/portal.py           Frappe portal client (stdlib only)
│   └── references/frappe-portal.md setup, command reference, troubleshooting
└── tests/
    └── test_portal.py              mock-server tests for portal.py
```
