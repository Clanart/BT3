### Title
Async commit/push security review is bypassable via `git -c alias.c=commit`, `git commit --amend`-adjacent aliasing, or global-option prefixes not matched by the `Bash(git commit:*)` / `Bash(git push:*)` `if` glob and the defensive `_GIT_COMMIT_RE`/`_GIT_PUSH_RE` regexes - (File: `plugins/security-guidance/hooks/hooks.json`)

### Summary
The `PostToolUse[Bash]` hook only spawns `security_reminder_hook.py` when the raw command string lexically starts with `git commit` or contains a bare `git push` (per the `if: "Bash(git commit:*)"` / `"Bash(git push:*)"` matchers and the redundant in-script `_GIT_COMMIT_RE` / `_GIT_PUSH_RE` regexes). Semantically identical commit/push invocations that use `git -c alias.c=commit c ...` or other global-option/alias forms do not match these string patterns, so the async LLM security review never fires even though the commit or push actually executes.

### Finding Description
The gate for the commit-review/push-sweep review is purely lexical, applied twice:
1. In `plugins/security-guidance/hooks/hooks.json`, the `PostToolUse` entry matched on tool `Bash` uses `"if": "Bash(git commit:*)"` and `"if": "Bash(git push:*)"` [1](#0-0) . These are CC's own prefix-glob matchers over the literal command string — Claude Code only invokes the python hook when the command string matches this glob.
2. Even when the python is spawned, it defensively re-checks with `_GIT_COMMIT_RE = re.compile(r'\bgit\s+commit(?:\s|$)')`, which the code's own comment says "does NOT tolerate `git -c k=v commit` global options" and intentionally mirrors "Claude Code's own commit attribution" [2](#0-1) .
3. `_GIT_PUSH_RE` is slightly more tolerant (handles `-c`/`-C`), but the docstring explicitly states the hooks.json `if` matcher itself is the real, non-tolerant gate: "The hooks.json `Bash(git push:*)` matcher (subcommand prefix) doesn't [tolerate globals], but those forms are rare in practice ... this regex is a defensive re-gate, not a widening" [3](#0-2) .

Because CC's `Bash(git commit:*)` prefix-glob is evaluated against the literal command string before the process even executes, and git itself resolves `git -c alias.c=commit c -m "msg"` (or a pre-configured `alias.c = commit` invoked as `git c ...`) to a full `git commit`, an attacker-influenced command (e.g. via prompt-injected instructions in repository content, an issue, or a malicious command/skill file that Claude reads and then executes) can perform a real commit or push while never matching `Bash(git commit:*)`/`Bash(git push:*)`. The async `security_reminder_hook.py` — the only mechanism reviewing the diff for injected vulnerabilities before/around the commit — is never spawned, so `handle_commit_review_posttooluse` / `handle_push_sweep_posttooluse` and the LLM vulnerability scan never run for that commit.

This is acknowledged in-repo as a known, intentional gap rather than something fixed: the comments explicitly describe non-tolerance of `-c` aliasing as "keeps this hook aligned with CC's commit attribution" and call the wider gap "rare in practice," but do not close it.

### Impact Explanation
This is a security-control bypass: the plugin's core promise ("LLM review of every commit/push for injected vulnerabilities") can be silently defeated by simply changing command syntax, with no error, warning, or metric indicating the review was skipped (skip_reason telemetry only fires when the hook *is* invoked and fails success detection, not when CC never invokes it at all). In an autonomous/agentic workflow where an attacker can influence what shell command Claude runs (e.g. via prompt injection in a README, issue, or tool output instructing "commit using `git -c alias.c=commit c -m ...`"), a malicious code change can be committed and/or pushed with the security review permanently and invisibly skipped for that action.

### Likelihood Explanation
Requires an attacker to influence the exact command string Claude executes (e.g. via prompt injection embedded in ordinary repo content, matching the "reachable from ordinary repository content" criterion). No privilege escalation, secrets, or admin access are needed — only that the agent be induced to phrase the git invocation slightly differently. This is a low-effort, deterministic, 100%-repeatable bypass (not probabilistic), since it is a pure string-matching gap, not a race or a heuristic miss.

### Recommendation
Replace (or supplement) the lexical `if` glob and `_GIT_COMMIT_RE`/`_GIT_PUSH_RE` regexes with actual argv-aware parsing that normalizes global options/aliases (e.g. resolve `git -c alias.<x>=<subcommand>` and known alias config before matching, or simply match on `\bgit\b.*\b(commit|push)\b` post `-c`/`-C` stripping consistent with CC's own subcommand-prefix-extraction fix referenced in the changelog for `git -C /path log`). Additionally, add a repo-state fallback (e.g. detect a new commit via reflog / `git rev-parse HEAD` diff after *any* Bash call touching a git repo, not gated on command-string matching at all) so review coverage doesn't depend solely on command syntax.

### Proof of Concept
Differential/fuzz test plan:
1. Build a corpus of semantically equivalent commit-producing invocations: `git commit -m x`, `git commit --amend -m x`, `git -c alias.c=commit c -m x`, `git config alias.c commit && git c -m x`, `GIT_DIR=... git commit`, `git commit -m x && true`.
2. For each command string `s`, actually execute it against a scratch git repo and assert a new commit object is created (`git rev-parse HEAD` changes) — this is the ground truth "did a commit happen."
3. Apply the same glob logic CC uses for `if: "Bash(git commit:*)"` (prefix match on command string) and separately `_GIT_COMMIT_RE.search(s)`.
4. Assert: for every `s` where step 2 shows a commit happened, both the `if` glob match and `_GIT_COMMIT_RE` must return true (100% trigger coverage invariant).
5. Expected result: the test fails for `git -c alias.c=commit c -m x` and `git config alias.c commit && git c -m x` — ground truth shows a commit occurred but neither the `if` glob nor `_GIT_COMMIT_RE` fire, proving the review-bypass. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** plugins/security-guidance/hooks/hooks.json (L35-55)
```json
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/sg-python.sh\" \"${CLAUDE_PLUGIN_ROOT}/hooks/security_reminder_hook.py\"",
            "if": "Bash(git commit:*)",
            "asyncRewake": true,
            "rewakeMessage": "Background security review of commit — address or acknowledge the findings below, then continue with the user's original request or continue waiting for their reply:",
            "rewakeSummary": "Commit security review found issues"
          },
          {
            "type": "command",
            "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/sg-python.sh\" \"${CLAUDE_PLUGIN_ROOT}/hooks/security_reminder_hook.py\"",
            "if": "Bash(git push:*)",
            "asyncRewake": true,
            "rewakeMessage": "Background security review of pushed commits not yet reviewed — address or acknowledge the findings below, then continue with the user's original request or continue waiting for their reply:",
            "rewakeSummary": "Push security review found issues"
          }
        ],
        "matcher": "Bash"
      }
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L593-597)
```python
# Regex matching `git commit` commands. Mirrors Claude Code's own commit
# detection — it does NOT tolerate `git -c k=v commit` global options, which
# keeps this hook aligned with CC's commit attribution on what counts as a
# commit.
_GIT_COMMIT_RE = re.compile(r'\bgit\s+commit(?:\s|$)')
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L612-629)
```python
# ─── push-sweep ─────────────────────────────────────────────────────────────
#
# Mirrors Claude Code's own push-command matching — tolerates `git -C <p>` /
# `git -c k=v` global options. The hooks.json `Bash(git push:*)` matcher
# (subcommand prefix) doesn't, but those forms are rare in practice
# and the python only ever runs after CC's matcher fired, so this regex is a
# defensive re-gate, not a widening — `git -C path push` won't reach python
# unless chained with a plain `git push` in the same compound command.
#
# `gh pr create` is intentionally NOT a separate hooks.json matcher: gh runs
# `git push` as a child process, which CC's matcher doesn't observe (it sees
# only the top-level `gh pr create` argv). A separate `Bash(gh pr create:*)`
# entry would buy minimal extra coverage (sessions that push only via gh) at
# the cost of an extra python spawn on every `... && gh pr create` compound
# (the common case). Those sessions are caught on their next standalone `git push`.
_GIT_PUSH_RE = re.compile(
    r'\bgit(?:\s+-[cC]\s+\S+|\s+--\S+=\S+)*\s+push\b'
)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L916-921)
```python
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not _GIT_COMMIT_RE.search(command):
        # Defensive only — hooks.json's `"if": "Bash(git commit:*)"` is the
        # real gate so CC never spawns python3 for ls/grep/etc. This catches
        # cases where CC's command matching fails open and spawns the hook anyway.
        sys.exit(0)
```
