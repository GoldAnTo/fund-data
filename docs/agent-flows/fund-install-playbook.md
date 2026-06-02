# Fund Install Playbook

> **Last updated:** 2026-06-02
> **Audience:** Anyone — human or AI — who gets asked "how do
> I install `fund-data` into OpenClaw / Codex / Claude Code?",
> "why is the install failing?", "what's the difference between
> a symlink and a copy install?", or "what is the privacy
> flag for?". This is the **answer script** for the
> distribution boundary. Pair with
> [`fund-install-pipeline.md`](./fund-install-pipeline.md) for
> diagrams and code anchors.
>
> **Use it when:**
> - Onboarding a new developer or agent to the data plane.
> - Reviewing a PR that touches `install_skill.py`,
>   `SKILL.md` (the manifest), or `SKILLS.md` (the
>   per-platform layout).
> - Debugging a report of "the install said OK but the
>   agent does not see the skill" or "the install
>   refuses" or "the install leaks my IP".
> - Fielding a question about the privacy boundary
>   between a portable install and a public publish.
> - Adding a new agent platform to the install matrix.
>
> **Do NOT use it when:**
> - The question is about the runtime MCP surface →
>   use [`fund-mcp-server-pipeline.md`](./fund-mcp-server-pipeline.md).
> - The question is about the data plane (search,
>   sync, coverage, cloud bundle) — use the matching
>   playbook.
> - The question is about contributing to the
>   project (lint, test, CI) → use
>   [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md).

---

## TL;DR (60-second answer)

`fund-data` is a **single-source-tree, multi-platform
skill** that installs into OpenClaw / Codex / Claude Code /
a personal `~/.agents` directory from one source folder
(`fund-data/SKILL.md` and companion files). The installer
(`fund-data/scripts/install_skill.py`) is a 330-line,
dependency-free Python script with three actions
(`install` / `uninstall` / `status`), five targets, and
two install modes per target (symlink or copy).

The defining characteristics are:

- **One source tree, four discovery targets.** The
  installer creates a symlink or a copy at each target's
  path; the agent reads the file through the symlink or
  the copy. The four targets are
  `~/.claude/skills/fund-data` / `~/.codex/skills/fund-data`
  / `~/.openclaw/skills/fund-data` / `~/.agents/skills/fund-data`.
- **Symlink is the default, copy is the override.** For
  `claude` / `openclaw` / `agents` the installer
  symlinks; for `codex` it copies (because Codex's
  discovery does not follow symlinks in all cases).
  `--copy` forces a copy for all targets.
- **Three actions compose the lifecycle.** `install`
  creates or refreshes the target; `uninstall` removes
  it (with a safety check on real directories);
  `status` reports the current state.
- **Two data modes.** `none` (default) excludes the
  SQLite; `copy` (`--include-data`) includes a
  consistent snapshot. `--scrub-raw-responses` is the
  privacy flag that empties the `raw_responses` table
  on the destination.

---

## The full answer template (use this skeleton)

When asked "how do I install `fund-data` into agent X?",
structure the answer in **four paragraphs**, one per
concern. Order matters — it matches the installer's
decision flow.

### Paragraph 1 — Source tree and target matrix

> The source tree is the `fund-data/` folder at the repo
> root, which contains `SKILL.md` (the manifest) plus
> `scripts/`, `references/`, `agents/`, and the source
> for the SKILL.md body. The installer copies or
> symlinks this tree to one of four target paths:
> `~/.claude/skills/fund-data` (Claude Code),
> `~/.codex/skills/fund-data` (Codex),
> `~/.openclaw/skills/fund-data` (OpenClaw, managed
> scope), and `~/.agents/skills/fund-data` (OpenClaw,
> personal scope). The `--target` flag picks one or
> `all`; the default is `all`. A target whose parent
> directory does not exist (e.g. you have not installed
> OpenClaw) is `SKIP`'d, not failed.

### Paragraph 2 — Install mode (symlink vs copy)

> For `claude` / `openclaw` / `agents` the installer
> creates a symlink: the agent reads the file through
> it, and any local edit to the source tree is
> immediately visible. For `codex` the installer copies:
> Codex's discovery does not follow symlinks in all
> cases, so the install must be a real directory. The
> `--copy` flag forces a copy for all targets. The
> trade-off: symlink installs are zero-friction for
> developers editing the source tree; copy installs need
> a re-run of `install --copy` to refresh, which is the
> right shape for a CI runner that has a pinned skill
> version.

### Paragraph 3 — Data mode and the privacy flag

> The default data mode is `none`: the SQLite
> (`data/fund_data.sqlite`) and its WAL/SHM sidecars,
> `backfill_logs/`, `raw_responses/`, `backfill_state.json`,
> and `backfill_summary.json` are all skipped during a
> copy install. The agent can rebuild from the providers
> or pull the cloud bundle. For an air-gapped
> installation, `--include-data` (shorthand for
> `--copy --data-mode copy`) copies a consistent SQLite
> snapshot using `sqlite3 source.backup(target)`. The
> `--scrub-raw-responses` flag is the privacy opt-in:
> it deletes the `raw_responses` table on the destination
> after the backup, which removes any caller IP that
> the upstream proxy added. The installer prints a
> warning if `--data-mode copy` is in effect without
> the scrub; the warning is the operator's signal to
> re-run with the flag if the install will leave the
> machine.

### Paragraph 4 — Status, uninstall, and refresh

> `status` reports one of five states per target:
> `LINKED` (symlink to the source), `INSTALLED` (real
> directory with `SKILL.md`), `STALE` (symlink points
> elsewhere), `MISSING` (no entry), or `BROKEN` (entry
> exists but no `SKILL.md`). `uninstall` removes the
> entry: symlinks are unlinked, files are unlinked,
> and real directories are `rmtree`'d only if `--copy`
> is in `sys.argv` (the safety signal) or `--force` is
> passed. `install` is idempotent: an existing
> correctly-pointed symlink is a no-op; an existing
> copy is merged (source files overwrite, destination's
> own state is preserved). The lifecycle is fresh
> install → upgrade (re-run) → downgrade / replace
> (`--copy` to overwrite) → uninstall.

---

## The 12 most-asked questions (with full answers)

These are the questions that come up the most in onboarding,
support, and PR review. **Answer them in the order they
appear here, with the same level of detail** — these are the
explanations the team has settled on after multiple rounds of
"but why?".

### Q1. Why is the installer one Python script with no dependencies?

- **The installer is a side-effect tool, not part of
  the data plane.** It runs once per machine; it does
  not import `fund_data` or `fund_cloud`. The
  dependency-free design means the installer can run
  on a fresh checkout without `pip install`.
- **The 330 lines fit in one file.** The script is
  small enough to read end-to-end, which is what a
  security-conscious operator does before installing a
  skill. Splitting it across modules would add
  import-path complexity without making the
  installation story clearer.
- **The console script `fund-install-skill` is for
  `pip install -e .` users.** A developer who has the
  project on PATH gets the script for free; a CI
  runner that does `pip install` of the wheel gets it
  too. A developer who runs the script directly
  (without `pip install`) just calls
  `python3 fund-data/scripts/install_skill.py ...`.

### Q2. Why symlink for Claude / OpenClaw / `~/.agents` but copy for Codex?

- **Codex's discovery does not follow symlinks in all
  cases.** The team's testing showed that some
  Codex versions resolve the symlink at scan time and
  others do not; the inconsistent behaviour is
  fragile. A real directory is the safe shape.
- **Symlink is the right shape for editors.** Claude
  Code and OpenClaw follow symlinks transparently, so
  a developer editing `SKILL.md` in the source tree
  sees the change immediately in the agent. A copy
  install would require a re-run of `install --copy`
  for every edit, which is friction.
- **The team could have forced a copy for all
  targets.** The trade-off is "Claude / OpenClaw
  developers get a friction-free edit loop" vs
  "consistent install shape across all four targets".
  The team chose the friction-free loop. The
  consistency story is "`--copy` works for everyone".

### Q3. Why is the SQLite excluded from the default copy install?

- **The SQLite is 5+ GB and is rarely needed at
  install time.** An agent that just wants the
  `SKILL.md` and the scripts can pull the cloud
  bundle on first use (`fund-cli cloud pull`) or
  rebuild from the providers. The install is the
  metadata; the data is the runtime.
- **The exclude list is the privacy boundary.** Even
  with the SQLite excluded, the skip list
  (`raw_responses/`, `backfill_state.json`, etc.)
  is the explicit "this is operator telemetry, not
  skill content" list. The skill is what an agent
  needs; the audit trail is what the operator needs.
  Conflating them is a footgun.
- **`--include-data` is the explicit override.** A
  portable install for an air-gapped machine passes
  `--include-data` and gets the SQLite. The default
  is "be small"; the override is "be portable".

### Q4. Why is `--scrub-raw-responses` opt-in, not opt-out?

- **The default is "preserve everything the source
  has".** The installer is a copy tool; deleting data
  is a non-copy operation. Opt-in makes the
  destructive step explicit.
- **The warning is the operator's signal.** The
  installer prints
  `::warning::The SQLite snapshot includes the
  raw_responses table, which stores full upstream
  HTTP bodies...` when `--data-mode copy` is in
  effect without `--scrub-raw-responses`. The
  warning is the operator's chance to re-run with
  the flag. An operator who reads the warning
  *and* chooses to keep the data is making an
  informed decision.
- **The flag is named to be unambiguous.**
  `--scrub-raw-responses` is a verb-object pair that
  cannot be confused with anything else. A shorter
  flag like `--private` or `--safe` would be
  ambiguous about what is being scrubbed.

### Q5. Why does the installer skip `__pycache__` / `.pyc` / `backfill_logs/`?

- **These are runtime artifacts, not skill content.**
  `__pycache__/` and `*.pyc` are Python's bytecode
  cache; a fresh install rebuilds them on first
  import. `backfill_logs/` is the operator's audit
  log; the agent does not need it. `backfill_state.json`
  and `backfill_summary.json` are the backfill runner's
  state; transferring them would carry the previous
  machine's progress to the new machine.
- **The skip list is the explicit contract.** A new
  artifact added to the project (e.g. a new cache
  directory) is excluded by adding it to
  `_should_skip_install_path`. The contract is "skill
  content is what is in the source tree, minus the
  skip list".
- **The two-pass `_copy_into` ensures the destination
  never carries skip-list artifacts**, even if a
  previous install (with a different skip list) left
  them behind. The first pass skips at the source; the
  second pass unlinks at the destination.

### Q6. Why is `uninstall` so cautious about real directories?

- **The destination is `~/.claude/skills/fund-data` /
  `~/.codex/skills/fund-data` / etc.** A foreign
  skill with the same name (extremely rare, but
  possible if the operator manually created a
  directory there) would be `rmtree`'d by a careless
  uninstall. The `REFUSE` is the safety check.
- **`--copy` in `sys.argv` is the safety signal.**
  The team chose to detect the `--copy` flag literally
  in `sys.argv` because that flag is what the operator
  passes when they know the install is a copy (and
  therefore the destination is owned by the installer).
  An alternative would be a separate `--yes-i-know-what-
  im-doing` flag, but the team preferred the existing
  flag as the signal.
- **`--force` is the explicit override.** The operator
  who is sure they want a `rmtree` of a real directory
  passes `--force`. The flag is unambiguous and is
  unlikely to be passed by accident.

### Q7. Why is the `INSTALLED` status returned for a real directory, not `LINKED`?

- **The status reflects what the agent sees.** A
  symlink is a pointer; the agent sees the file
  through the pointer. The status `LINKED` is the
  user's signal that the symlink points at the
  expected source.
- **A real directory is the post-copy state.** The
  agent sees the files; the user's signal is
  `INSTALLED`. The distinction matters because
  re-running `install` on a `LINKED` install is a
  no-op (the symlink already points at the source),
  while re-running on an `INSTALLED` install merges
  the source on top of the destination.
- **A `STALE` install has a symlink that points
  somewhere other than the source.** The most common
  cause is the repo being moved or renamed; the fix
  is to remove the stale symlink and re-run `install`.
  The installer does not auto-rewrite a stale
  symlink because the wrong target might be a
  different repo entirely.

### Q8. Why does the installer's safety check use `if "--copy" in sys.argv`?

- **The check has to be cheap and reliable.** Looking
  for the literal `--copy` in `sys.argv` is the
  cheapest possible check: no argparse parsing, no
  validation, no edge cases. The trade-off is that
  the operator must pass `--copy` (or `--force`) on
  the uninstall command line; an alias or a wrapper
  script that drops the flag will fail the check.
- **The check is a contract, not a feature.** A
  future refactor that uses `args.copy` (the parsed
  argparse value) would be a one-line change, but
  the team prefers the literal `sys.argv` check for
  its transparency. A reviewer reading
  `_uninstall_one` knows immediately what the check
  is doing.
- **The check is at the level of intent, not
  capability.** "Did the operator pass `--copy`?" is
  the question; "Is the destination a copy install?"
  is a different question (and harder to answer
  without inspecting the filesystem). The literal
  check is the simplest intent test.

### Q9. Why is the console script `fund-install-skill` declared in `pyproject.toml` and not in `setup.py`?

- **The project uses `pyproject.toml` as the single
  source of truth for build metadata.** `setup.py`
  is not present; `pyproject.toml` declares the
  package, the dependencies, the entry points, the
  ruff config, the black config, and the mypy
  config. Adding a `setup.py` would split the
  metadata across two files.
- **`[project.scripts]` is the PEP 621 way to declare
  console scripts.** A `pip install -e .` reads the
  table and creates the entry point on PATH. The
  alternative (a `console_scripts` entry in
  `setup.py` with a separate `entry_points.txt`) is
  the legacy way; `pyproject.toml` is the modern
  way.
- **The console script is `fund-install-skill`,
  not `install_skill`.** The `fund-` prefix matches
  the other console scripts (`fund-cli`,
  `fund-mcp`, `fund-backfill`, `fund-doctor`,
  `fund-coverage-report`, `fund-retry-failures`).
  The prefix is the project's namespace on PATH.

### Q10. Why does the installer not check the source tree's `git status`?

- **The installer is a file-copy tool, not a VCS
  tool.** It does not know about `git`, `hg`, or
  `svn`. The source tree is whatever the operator
  has on disk; a dirty tree is installed as-is.
- **The operator is responsible for the source
  state.** An operator who wants a clean install
  should `git status` and `git stash` (or commit)
  before `install`. The installer is not the place
  to enforce VCS hygiene.
- **The alternative would be wrong.** A check like
  "refuse to install a dirty tree" would break the
  developer's "edit + install + test" loop, which is
  the common case for the symlink targets. The
  installer should not second-guess the operator's
  workflow.

### Q11. Why is the `codex` install always a copy, even when the user does not pass `--copy`?

- **The team's testing showed Codex does not
  consistently follow symlinks.** Some Codex
  versions resolve the symlink at scan time, others
  do not. The inconsistent behaviour is fragile;
  the safe shape is a real directory.
- **The team considered adding a `--link` flag to
  force symlink on Codex.** The flag would be a
  one-line change. The team did not add it because
  the use case is rare (developers who want a live-
  edit loop on Codex can edit the source tree and
  re-run `install --copy`; the loop is not as tight
  as the symlink loop on Claude, but it is
  acceptable).
- **The `--copy` flag is the explicit "force copy for
  all targets" override.** An operator who runs
  `install --copy` gets a copy for all four
  targets, regardless of the per-target default.

### Q12. Why does the installer's "merge" branch preserve the destination's own files?

- **The destination is the agent's working
  directory.** A copy install is the agent's copy
  of the skill; the agent may have side files
  (e.g. a per-agent log, a per-agent cache, a
  per-agent config) that the source tree does not
  have. The "merge" branch overwrites the source
  files (so the install is fresh) but preserves
  the destination's own files (so the agent's
  state survives).
- **The skip list runs twice to clean up
  destination artifacts.** If a previous install
  had a different skip list and left artifacts
  behind, the post-copy pass unlinks them. The
  operator's own files are not in the skip list,
  so they survive the cleanup.
- **The trade-off is a more complex copy.** A pure
  "delete destination, copy source" would be
  simpler, but it would lose the agent's side
  files. The merge is the right shape for a
  long-running install that gets refreshed.

---

## Design philosophy (the "why" of the three-action, two-mode shape)

Read this section once and the rest of the playbook
becomes obvious.

1. **The source tree is canonical.** The skill is
   the source tree; the install is a projection
   onto a target. The operator edits the source
   tree; the install is the bridge. The
   `SKILL.md` frontmatter (`name`, `version`,
   `description`, `tools`) is the contract; the
   install enforces the contract by copying the
   right files.
2. **Symlink vs copy is a discovery-mechanism
   decision, not a personal preference.** Claude
   Code and OpenClaw follow symlinks; Codex does
   not (in all cases). The installer picks the
   right shape per target and lets `--copy`
   override.
3. **The skip list is the privacy / hygiene
   boundary.** The skill is what an agent needs;
   the audit trail is what an operator needs.
   Conflating them is a footgun. The skip list
   is the explicit contract.
4. **`--scrub-raw-responses` is opt-in, not
   opt-out, because the default is "preserve
   everything the source has".** The warning is
   the operator's signal; the flag is the
   explicit destructive step.
5. **The installer's safety checks are at the
   level of intent, not capability.** "Did the
   operator pass `--copy`?" is the question; "Is
   the destination a copy install?" is harder to
   answer. The literal `sys.argv` check is the
   simplest intent test.
6. **`status` is a read-only introspection
   layer.** The five states (`LINKED` /
   `INSTALLED` / `STALE` / `MISSING` / `BROKEN`)
   are the operator's "is everything wired up?"
   check. The status is what a fresh-checkout
   script calls to verify the install.
7. **The lifecycle is `install → upgrade →
   replace → uninstall`.** Each transition is
   an action; the installer's idempotence
   (re-running `install` is a no-op when
   nothing changed) means the operator can
   re-run freely. The replace transition
   (`install --copy` over an existing install)
   is the upgrade path.
8. **The console script is the agent-friendly
   entry point.** A CI runner that has
   `fund-data` on its Python path can call
   `fund-install-skill install --target codex`
   without specifying the script path. The
   `fund-` prefix matches the other console
   scripts and is the project's namespace on
   PATH.

---

## What NOT to say (anti-patterns)

These are common wrong answers the team has seen in PR
reviews and support threads. Avoid them.

- **"The install is one command."** It is three
  actions (`install` / `uninstall` / `status`)
  with multiple flags. The "one command" framing
  hides the lifecycle.
- **"Symlink is the default."** It is the default
  for `claude` / `openclaw` / `agents`; the
  default for `codex` is copy. The "default"
  is per-target, not global.
- **"`--include-data` is safe."** It is safe
  with `--scrub-raw-responses`; without it, the
  install leaks caller IP from `raw_responses`.
  The flag is a half-safe operation without the
  scrub.
- **"Uninstall always works."** It refuses on
  real directories without `--copy` or `--force`.
  The refusal is the safety check; the
  workaround is the explicit flag.
- **"The installer validates the skill."** It
  does not. The only check is that `SKILL.md`
  exists. The skill content (the frontmatter,
  the body, the scripts) is the source tree's
  responsibility.
- **"Install on every machine."** Each machine
  has its own agent set; the installer is
  per-machine. A CI runner that has Claude
  installed will get the `claude` install
  automatically; a developer laptop that does
  not have OpenClaw will get a `SKIP`.
- **"The status is always current."** The
  status reflects the filesystem state at the
  time of the call. A symlink that was
  `LINKED` at call time may be `STALE` an
  hour later if the repo was moved. The status
  is a point-in-time check, not a continuous
  monitor.

---

## How to add a new install target (the contributor recipe)

When a new agent platform is added to the install matrix:

1. **Confirm the discovery path.** Ask the platform
   maintainer: where does the agent look for skills?
   The answer is one of the four existing patterns
   (`~/.claude/`, `~/.codex/`, `~/.openclaw/`,
   `~/.agents/`) or a new one. Document the
   discovery mechanism in `SKILLS.md` §"How each
   platform discovers the skill".
2. **Add the target path to `_target_paths()`.**
   Insert a new entry in the dict
   (`fund-data/scripts/install_skill.py:41-48`).
   Use the platform's standard namespace
   (`home / ".<platform>" / "skills" / SKILL_NAME`).
3. **Decide the install mode.** Symlink or copy?
   The choice is driven by the platform's
   discovery mechanism: if the platform follows
   symlinks, symlink is the right default; if
   not, copy. Document the choice in
   `SKILLS.md` §"Platform" for the new target.
4. **Document the platform in `SKILLS.md` and
   `SKILL.md` frontmatter.** The `tools:` list in
   `SKILL.md` may need to grow (a platform that
   needs `web_search` for example). `SKILLS.md`
   should have a section for the new platform's
   install command, the discovery mechanism, and
   any quirks.
5. **Add a unit test.** The test should run
   `_install_one` for the new target against a
   temp directory and verify the state.
6. **Bump the SKILL.md `version:` field.** The
   manifest version is the agent's signal that
   the skill changed; bump it for the new
   target's documentation.

---

## How to keep this playbook accurate

The playbook is the team's *settled* explanation, not
the live code. When the code changes, update the
playbook in the same PR. The check is:

- A new install target is added → update §3.1
  and the contributor recipe.
- The skip list changes → update §3.5 and Q5.
- The status semantics change (a new status
  added) → update §3.6.
- The privacy flag is renamed or the warning
  text changes → update §3.4 and Q4.
- A new CLI flag is added (e.g. `--link`) →
  update §4 and §7.
- The per-target default (symlink vs copy)
  changes → update Q2 and Q11.

If a PR changes any of the above and does not update
the playbook, request changes with a pointer to this
section.

---

## Related documents

- [`fund-install-pipeline.md`](./fund-install-pipeline.md) —
  diagrams + code anchors + env var table.
- [`fund-mcp-server-pipeline.md`](./fund-mcp-server-pipeline.md) —
  the runtime surface after install.
- [`fund-cloud-bundle-pipeline.md`](./fund-cloud-bundle-pipeline.md) —
  the data plane the install wires up.
- [`../../fund-data/SKILL.md`](../../fund-data/SKILL.md) —
  the agent-facing skill manifest (the file that
  gets installed).
- [`../../fund-data/SKILLS.md`](../../fund-data/SKILLS.md) —
  per-platform install layout for Codex / Claude /
  OpenClaw; the canonical install commands and
  refresh flow.
- [`../../fund-data/ARCHITECTURE.md`](../../fund-data/ARCHITECTURE.md) —
  the contributor-facing architecture reference.
- [`../../SECURITY.md`](../../SECURITY.md) —
  the privacy boundary documentation (raw_responses
  IP leak, `--scrub-raw-responses` opt-in).
- [`../../pyproject.toml`](../../pyproject.toml) —
  the `console_scripts` entry that installs
  `fund-install-skill` on PATH.
- [`../../README.md` §Known gaps](../../README.md#known-gaps-tracked-for-030) —
  the v0.3.0 backlog items.
