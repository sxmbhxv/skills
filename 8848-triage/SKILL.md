---
name: 8848-triage
description: Pick up a task from the 8848 Digital project portal (Frappe Projects) and carry it through to a ready-to-test code change. Fetch the task by ID, URL or project name. Open a GitHub issue that links to the task, and write the issue link back into the task's task_remarks field. Analyze the repository using its CLAUDE.md or AGENTS.md, implement the change on a fresh branch, and hand over a step-by-step test plan so the user can test and raise the PR. Use this whenever the user mentions a portal task or a task ID such as TASK-2026-01234, pastes a portal /app/task/ link, or says triage this task, pick up this task, work on my task from the portal, or create an issue for this task and fix it. Use it even if they don't name the skill.
---

# 8848 Triage

The user receives work as Tasks on the company project portal, which is a Frappe site. This skill turns one
Task into an uncommitted change on its own branch, linked both ways to a GitHub issue. It ends with a
summary that tells the user exactly how to test. **The user commits, pushes and opens the PR.** Leave all three to
them, because they want to review and test before anything is shared.

Input is one of: a task ID (`TASK-2026-01234`), a task URL, or a project name (optionally with words from
the task title).

Portal helper (standard library only, prints JSON). The setup, the full command reference and the error table are in
`references/frappe-portal.md`. Read that file when something fails or the environment variables are missing.

```bash
python3 ~/.claude/skills/8848-triage/scripts/portal.py check|task|find|download|link-issue|api …
```

The phases below are in order. Each one feeds the next, so don't skip ahead. A wrong base branch,
a duplicate issue or a misread requirement is costly to undo once code is written.

---

## Phase 0: Preflight

Run these together:

1. `portal.py check`. By default it logs in with the user's portal email and password
   (`PORTAL_8848_USERNAME` / `PORTAL_8848_PASSWORD`). An API key pair is the alternative for accounts that can't
   use a password. If it reports missing variables or a failed login, show the user the setup block from
   `references/frappe-portal.md#1-setup` and wait. Credentials live only in environment variables so that
   secrets stay out of repos and transcripts. So don't ask the user to paste their password into the chat,
   don't echo the variables' values, and don't write the values to any file. The user sets them up
   themselves.
2. `gh auth status`.
3. `git rev-parse --show-toplevel`, `gh repo view --json nameWithOwner,url`, `git status --porcelain`,
   `git branch --show-current`. If the working directory isn't a git repo, ask which local repo the task is for.
4. **Read the repo's instructions file, all of it.** That means `CLAUDE.md` and/or `AGENTS.md` at the repo root, plus nested
   ones in directories you'll touch. It is the source of truth for the integration (base) branch, branch
   naming, architecture, conventions, verification commands, environment traps and sibling repos. These
   files are often gitignored and kept only on the local machine, which is why they're more current than the README.
   - If both files exist and disagree on something you'll rely on (base branch above all), ask the user
     which one is right. A wrong base branch produces a PR full of unrelated commits.
   - If neither exists, say so, use README / package scripts / `.github/` for conventions, and ask for the
     base branch.

## Phase 1: Read the task

- With an ID or URL, run `portal.py task <id>`. With a project name, run
  `portal.py find --project "<name>" [--text "<words>"] [--mine]`. If more than one task fits, ask the user to pick
  (AskUserQuestion, up to 4 options showing subject, status and due date).
- Read **everything** in the output, not just the subject:
  - `comments` oldest to newest. Later comments often change the requirement. When a comment contradicts
    the description and it isn't clear which wins, that's a question for the user.
  - `other_fields` and `child_tables`. Custom fields often hold acceptance criteria, the module, the client or the environment.
  - `parent_task` / `depends_on`. Fetch them with `task` when they could change scope (e.g. this task is one slice
    of a larger feature).
  - `project_details.notes` and `repo_hints`.
- If there are `attachments` or `inline_images`, run `portal.py download <id> <scratch-dir>/task-<id>/` and
  **look at the images** with Read. Screenshots and mockups are often the real spec, and the text alone
  can be misleading without them.
- If `linked_github_issues` is non-empty, this task was triaged before. Reuse that issue in Phase 3.
- Then write a short restatement in the conversation: **goal, acceptance criteria, out of scope,
  unknowns.** This is what the issue and the test plan get built from.

## Phase 2: Clarify before committing to an interpretation

Ask now if an unknown would change *what* gets built. Ask before the issue is created, so the issue records
the clarified requirement and not a guess. See "Asking good questions" below. If nothing is
ambiguous, carry on without asking.

## Phase 3: GitHub issue and the two-way link

1. **Choose the repo.** The issue goes where the code change will land, which by default is the current repo. If the
   task or `repo_hints` point clearly at a different repo (e.g. the backend while you're in the frontend), ask
   before creating anything.
2. **Look for an existing issue.** An issue may already exist from an earlier run or a teammate:
   `gh issue list -R <repo> --state all --search "<TASK-ID> in:title,body" --json number,title,url,state`
   plus `linked_github_issues`. If one exists, reuse it (mention it if it's closed) and skip to step 5.
3. **Match the repo's style.** Skim recent issues (`gh issue list -R <repo> --state all --limit 5`,
   then `gh issue view <n>` on one or two) and copy their title style and section headings. Use labels only if they
   already exist (`gh label list -R <repo>`). Never create labels.
4. **Create it.** Write the body to a temp file (use the scratchpad dir when one exists) and run
   `gh issue create -R <repo> --title "<title>" --body-file <file> --assignee @me [--label <existing>]`.
   Put the task ID at the end of the title so the issue is searchable, e.g.
   `fix: export is empty when the date range spans two years [TASK-2026-01234]`. Body template (adapt the headings to
   the repo's style):

   ```markdown
   ## Problem
   <What's wrong or needed, stated in the codebase's terms. Synthesise the description, comments and screenshots.>

   ## Acceptance criteria
   - [ ] <observable, testable outcome>
   - [ ] …

   ## Notes
   <Clarifications the user gave, constraints, out-of-scope items. Omit if empty.>

   ---
   Portal task: [<TASK-ID>](<task url>): <task subject>
   Project: <project_name> · Priority: <priority> · Due: <exp_end_date>
   ```

   Keep the implementation plan out of the issue. The issue describes the *requirement*, and the PR will
   describe the solution.
5. **Back-link on the portal:** `portal.py link-issue <id> --url <issue-url>`. This appends
   `GitHub Issue: <url>` to `task_remarks` and keeps what's already there. It's a no-op if the link is
   already present. If the field doesn't exist or the save is refused, see the error table in the reference.
   Don't edit other task fields to get past a validation error.
6. Tell the user both links in one line, then move on.

## Phase 4: Branch

- Use the base branch from the instructions file. Run `git fetch origin <base>`.
- Name the branch `<prefix>/<task-id-lowercase>-<3-6-word-slug>`, taking the prefix from the repo's convention
  (typically `fix/` for bugs and `feat/` for features), e.g. `fix/task-2026-01234-export-date-range`.
- If a branch for this task already exists (`git branch --list "*<task-id-lowercase>*"`), switch to it.
  Don't create a second one.
- **If the working tree is dirty**, ask before switching: stash (recommended,
  `git stash push -u -m "before <TASK-ID>"`), carry the changes onto the new branch, or stop so the user can deal with
  them. Never discard their work.
- `git switch --no-track -c <branch> origin/<base>`. Use `--no-track` so that a later bare `git push` can't
  land in the base branch.

## Phase 5: Deep analysis

The aim is to understand the code well enough that the change is right for **every variant it touches**,
not only the scenario in the task.

- Follow the instructions file's playbook if it has one. It encodes lessons the team already paid for.
- **Find the entry point.** Use the task's wording, the screenshots and UI strings (grep the visible labels), plus
  routes, buttons, endpoints or commands.
- **Trace the data path end to end**: UI, then state, then API client, then backend handler, then storage, as far as the
  instructions file says the logic goes. If business rules live in another repo, read that repo too. Don't edit it
  unless the user asks.
- **Read the history**: `git log --oneline -S "<symbol>"`, recent `git log -p -- <file>`,
  `gh pr list -R <repo> --state merged --search "<keywords>"`, and related issues. Earlier fixes show what the
  code is meant to do and where it has broken before.
- **Look at real data or reproduce the problem** when the instructions file describes a safe, read-only way to do it.
- **Map the blast radius**: every caller and every branch on type, role or mode that passes through the code you'll change.
- Post a brief plan: the root cause (for bugs) or the approach, the files to change, the variants that must keep working, and the risks.
  If the plan raises a decision that belongs to the user (scope, UX, a backend change, a migration), ask now.

## Phase 6: Implement

- Make the smallest change that fully meets the acceptance criteria. Match the surrounding code and the instructions
  file's conventions (layering, naming, comment style, formatting of the file you're in).
- No drive-by refactors. Note them as follow-ups instead, because they make the PR harder to review and test.
- Do the side steps the instructions file requires (e.g. mirroring gitignored files into another repo) and keep a
  list of them. They won't appear in the PR diff, so the user has to hear about them from you.
- If the correct fix belongs in another repo or team, don't paper over it here. Say so with `file:line`
  and ask how to proceed.
- Treat shared environments as read-only. Don't write to shared databases or APIs unless the user explicitly asks.

## Phase 7: Verify

- Run the checks the instructions file lists (type check, lint on touched files, tests, build). Fix what
  you broke. If something was already failing on the base branch, show that it was and say so. Don't hide it, and
  don't "fix" it as part of this task.
- Run the app or flow when that's practical. Keep track of what you couldn't verify and why (a role, login,
  device or dataset you don't have).

## Phase 8: Hand over

Leave the changes **uncommitted** on the branch, then end with this summary (fill in every section, and write
"none" rather than dropping a section):

```markdown
## <TASK-ID>: <subject>
Portal: <task url> · Issue: <issue url> · Branch: `<branch>` (from `origin/<base>`, uncommitted)

### What changed
- `path/to/file.ts:42`: what changed and why (one line per change)
- Outside the PR diff: <gitignored/mirrored/other-repo changes, or "none">

### How to test
Setup: <env/backend to point at, login + role, data you need (e.g. "an order with 2 line items"), flags>
1. <exact action> → expect <observable result>
2. …
Regression checks: <neighbouring variants that share the code path, each with its expected result>

### Verified
- <command>: <result>
- Not verified: <what, and why>

### Open questions / follow-ups
- <backend change needed at file:line, deferred refactors, assumptions to confirm>

### When you raise the PR
Title: `<suggested title>` · Base: `<base>`
Body: summary + `Refs #<n>` + portal task link. (`Closes #<n>` only auto-closes on merges into the
repo's default branch, so if `<base>` isn't the default branch, close the issue by hand after merge.)
```

Mention the stash if you created one (`git stash list`), so the user can restore it.

---

## Asking good questions

The user explicitly wants questions rather than guesses where the answer isn't obvious. But each question
costs them a context switch, so make every one count:

- **Ask** when the answer changes what you build or where: two plausible readings of the requirement, missing
  acceptance criteria for a behaviour change, a task comment that conflicts with the description, which environment or
  role reproduces the bug, a screenshot referenced but not attached, the change seeming to belong in another repo,
  credentials or a field name you can't discover, or a trade-off with a user-visible effect.
- **Don't ask** what you can find out yourself: file locations, conventions, how an API behaves (read the
  code or query read-only), and anything already in the task, the instructions file or the repo history.
- **Batch them.** Use AskUserQuestion, up to 4 questions at a time. Give concrete options, put your recommended option first,
  and briefly say what you found that makes it a question.
- Ask at the phase where the question comes up (task reading, analysis, implementation) instead of guessing and moving on.
  A guess baked into code is much harder for the user to spot than a question.

## Re-runs, partial failures, dry runs

- Every step is safe to re-run. Existing issues are found and reused, `link-issue` skips a link that's already present, and an existing
  branch is switched to.
- If the issue was created but the back-link failed, report it and retry `link-issue`. Never create a
  second issue. If the user has no write access to the task, give them the exact line to paste.
- If the user says "dry run" or "preview", read and analyse as usual, but don't create the issue or write to the
  portal. Show the drafted issue and the remarks line instead. Implement only if they asked for it.
