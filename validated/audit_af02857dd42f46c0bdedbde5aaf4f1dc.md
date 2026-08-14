## Analysis Result



### Title
Push-sweep security review is bypassed when commits are pushed via `gh pr create`, letting unreviewed code reach the remote - ([File: plugins/security-guidance/hooks/security_reminder_hook.py])

### Summary
The `security-guidance` plugin's `PostToolUse` hook enforces mandatory LLM-based security review of commits by matching the literal Bash command against `git commit`/`git push` regexes and only fires when a top-level Bash invocation matches. When a user pushes via `gh pr create` (which internally spawns `git push` as a child process), the hook's matcher never observes that `git push`, so the review/accounting mechanism is bypassed entirely — analogous to the 0x report's disclosed transformers producing tokens that never route back through the accounted `outputToken` check.

### Finding Description
`_GIT_PUSH_RE` is the regex the `PostToolUse` handler uses to decide whether a Bash tool invocation constitutes a "push" that needs the push-sweep security review: [1](#0-0) 

The code explicitly documents that this detection is scoped to the top-level Bash argv only, and that `gh pr create` — which performs its own `git push` as a child process — is deliberately excluded from the matcher: [2](#0-1) 

This mirrors the root cause in the 0x report: the accounting/verification layer (`TransformController`/`transformERC20`'s `outputToken` balance check, here the push-sweep's `git push` regex match) only tracks one specific, declared signal, while the actual mechanism used to move the underlying asset (tokens transferred by other transformers / commits pushed by `gh`'s child-process `git push`) can occur through a path that is invisible to the accounting layer. In both cases, the imbalance is a *known, accepted* design gap rather than an oversight caught by tests — the 0x team "acknowledged" the risk and pushed responsibility onto the front-end/user; this codebase's comment similarly rationalizes the gap ("A separate entry would buy minimal extra coverage... Those sessions are caught on their next standalone `git push`") without closing it.

The dedup/routing logic downstream (`_claim_bash_hook_once`) further confirms that the hook's model of "what counts as a push" is strictly the literal Bash command string it can see, not the actual push side effect: [3](#0-2) 

### Impact Explanation
A user (or an agent instructed by a malicious prompt injection in the repo, an issue, or a file) can commit and open a pull request via `gh pr create --head <branch>` (or any workflow where `gh` internally issues the push) without ever triggering `handle_push_sweep_posttooluse` or `handle_commit_review_posttooluse` for the pushed commits. Because the plugin's stated purpose is to catch security-relevant code before/at the point it reaches the remote (`.git` push boundary) and gate a security review, this bypass means vulnerable or malicious code introduced by the agent can ship to the remote/PR entirely unreviewed by the tool that's supposed to be a safety net for exactly that class of issue. This is a concrete "unaccounted output" bypass of a security control at a git-automation trust boundary, not a theoretical or mocked-only gap — the comment in the source confirms the authors know real sessions "push only via gh" and are only caught later, "on their next standalone `git push`," which may never happen if the branch is done being worked on.

### Likelihood Explanation
Likelihood is moderate-to-high in practice: `gh pr create` is one of the most common ways an agentic coding session finishes a task (create branch, commit, open PR), and the code comments confirm the maintainers are aware this is "the common case" for compound commands (`... && gh pr create`). No special privileges or attacker-controlled infrastructure are needed — a normal agent-driven or user-driven workflow triggers it.

### Recommendation
Extend hook coverage to observe pushes performed by wrapper tools, not just literal `git push` invocations:
1. Add a `Bash(gh pr create:*)` (and similarly for other `gh`/CI subcommands that push) matcher in `hooks.json` so `security_reminder_hook.py` is invoked on those commands too, and teach the handler to run the same push-sweep diff logic based on the actual ref state change (comparing `git rev-parse @{u}` before/after) rather than relying solely on regex-matching the outer Bash string.
2. Alternatively/additionally, run the push-sweep as a `SessionEnd`/periodic check against the remote tracking ref regardless of which command triggered the push, closing the gap for any push mechanism (including `gh`, IDE Git panels, or other tools) rather than only the ones whose invocation string matches known patterns.

### Proof of Concept
1. Install/enable the `security-guidance` plugin with its default hooks configuration.
2. In a repo with an intentionally vulnerable change staged, run: `git checkout -b feature/x && git commit -am "add vuln" && gh pr create --fill --head feature/x`.
3. Observe that `security_reminder_hook.py`'s `PostToolUse` handler is not invoked for this Bash call (no `Bash(git push:*)`/`Bash(git commit:*)` matcher fires on the literal `gh pr create ...` command string), confirmed by the code's own explanatory comment at lines 620–626 stating `gh pr create` intentionally bypasses the push-command matcher.
4. The PR is created on the remote with the vulnerable commit, without the mandatory commit-review/push-sweep LLM security analysis ever having run — the equivalent of tokens leaving the `state.wallet` unaccounted for in the 0x scenario.

### Citations

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

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L2079-2109)
```python
    # twice with the same command string and the same tool_use_id. The python
    # cannot tell which `if` fired it.
    #
    # Routing therefore MUST check commit FIRST so that compound commit+push
    # commands continue to hit commit-review (the pre-existing behaviour) on
    # the commit-matcher invocation. The push-matcher invocation of the SAME
    # compound command is deduped by `_claim_bash_hook_once` below: the second
    # spawn loses the tool_use_id sentinel race and exits early with
    # `bash_hook_dedup`, so commit-review runs exactly once. The alternative —
    # checking push first — would silently DROP commit-review
    # on `git commit && git push`, which is a regression.
    #
    # The push-sweep does NOT run on the compound call. That's acceptable: the
    # just-made commit is recorded by commit-review, so the next standalone
    # push sees it as reviewed and the sweep base advances past it. Older
    # unreviewed commits in the range are caught on that next push.
    if tool_name == "Bash" and hook_event_name == "PostToolUse":
        cmd = (input_data.get("tool_input") or {}).get("command", "") or ""
        if not (_GIT_COMMIT_RE.search(cmd) or _GIT_PUSH_RE.search(cmd)):
            return
        if not _claim_bash_hook_once(input_data):
            # Another spawn for this same tool_use_id already claimed the
            # work (compound matched multiple `if` configs). Emit a single
            # metric so telemetry can count how often the de-dupe kicks in.
            print(json.dumps({"metrics": {"bash_hook_dedup": True}}), flush=True)
            sys.exit(0)
        if _GIT_COMMIT_RE.search(cmd):
            handle_commit_review_posttooluse(input_data)
        elif _GIT_PUSH_RE.search(cmd):
            handle_push_sweep_posttooluse(input_data)
        return
```
