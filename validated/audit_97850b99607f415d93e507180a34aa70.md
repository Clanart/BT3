### Title
Commit-Review Security Hook Bypass via `git -c` Global Options - (File: `plugins/security-guidance/hooks/security_reminder_hook.py`, `plugins/security-guidance/hooks/hooks.json`)

### Summary
The security-guidance plugin's LLM-based commit review is gated by an exact/prefix string match on the literal substring `git commit`, both in the hook's declarative trigger (`hooks.json`'s `"if": "Bash(git commit:*)"`) and in the Python detection regex `_GIT_COMMIT_RE`. Git allows arbitrary global options to be inserted between `git` and the subcommand (e.g. `git -c core.pager=cat commit -m "msg"`), which changes the literal prefix of the command string without changing its semantics. Because both matching layers only recognize the exact `git commit` token sequence, a command such as `git -c commit.gpgsign=false commit -m "..."` never triggers the PostToolUse commit-review hook at all, and even if the hook did run, the internal regex explicitly does not match it — silently skipping the security review of that commit's diff.

### Finding Description
This is the same bug class as the audited `DebtorNFT._validateLoan` issue: a security-relevant validation gate relies on brittle, exact/strict matching against a value an "attacker" (here, the model itself, or a user prompting the model) fully controls, and any deviation from the exact expected shape causes the gate's intended check to be skipped/never satisfied rather than degrade gracefully.

Concretely:
- `hooks.json` registers the commit-review hook only `"if": "Bash(git commit:*)"` [1](#0-0) , a literal prefix match on the Bash command string.
- The Python-side detector re-derives the same assumption: `_GIT_COMMIT_RE = re.compile(r'\bgit\s+commit(?:\s|$)')`, and the code comment explicitly documents the limitation: *"Mirrors Claude Code's own commit detection — it does NOT tolerate `git -c k=v commit` global options."* [2](#0-1) 
- `handle_commit_review_posttooluse` treats this regex as the authoritative signal for "is this a commit," and exits immediately (skipping all review logic) when it doesn't match: `if not isinstance(command, str) or not _GIT_COMMIT_RE.search(command): ... sys.exit(0)` [3](#0-2) 

Git natively supports global options before any subcommand — `git -c <key>=<value> commit ...`, `git --git-dir=... commit ...`, `git -C <path> commit ...`, etc. — all of which are functionally equivalent to a plain `git commit` but do not literally start with (or contain, as an anchored `\bgit\s+commit`) the substring pattern `git commit` when options separate the two words in a way the regex's `\bgit\s+commit` wouldn't span (e.g., `git -c x=y commit`). Any such invocation:
1. Fails to match the `hooks.json` `"if": "Bash(git commit:*)"` trigger, so the PostToolUse hook process is never even spawned for that Bash call, and
2. Even in the reflog-fallback path inside the same handler (which exists specifically to catch suppressed/redirected stdout), the guard at the top of the function using `_GIT_COMMIT_RE` is the very first check performed, and it exits before that fallback logic is ever reached.

This is analogous to the reported bug's root cause: an exact-match security check (`balanceOf() == debtAmount`) that a controllable actor can perturb to permanently defeat the intended validation. Here the "perturbation" is a syntactically valid but differently-shaped git invocation, and the effect is not a revert/DoS but a silent fail-open of a security control — the LLM diff review of the commit's contents (which screens for hardcoded secrets, injection, path traversal, etc.) is skipped entirely, with no error, warning, or metric indicating the review was bypassed.

### Impact Explanation
This falls squarely within the plugin's own stated threat model: `review_api.py`'s adjudication prompt explicitly instructs reviewers to **never** treat "LLM-agent capability gates (PreToolUse/PostToolUse hooks, bash allow/denylists, workspace path jails — the model is the attacker, the user is the victim)" as a no-privilege-boundary case [4](#0-3) . The model (Claude, potentially manipulated via prompt injection from untrusted content it processed earlier in the session) is exactly the actor that decides what Bash command to run. By simply running `git -c commit.gpgsign=false commit -m "msg"` (a common, unremarkable form for disabling GPG signing per-invocation) instead of a plain `git commit`, the model can commit arbitrary code — including code containing hardcoded secrets, injected backdoors, or other vulnerabilities the plugin is designed to catch — while completely bypassing the commit-time security review that is the plugin's core stop-hook defense-in-depth layer for this surface. The user is left believing their commits were screened when, for any commit using a `git -c ...` (or similarly reshaped) invocation, they were not.

### Likelihood Explanation
High. No privileged access is required — a single Bash tool call with a standard, valid git option ordering is sufficient, and such invocations are common in real workflows (e.g., disabling GPG signing, setting `user.name`/`user.email` inline, specifying `--git-dir`/`-C` for a different working directory). The bypass requires no exploitation of a parser bug beyond the well-documented, self-acknowledged gap in the maintainers' own code comment, making this trivially reproducible and highly likely to occur both accidentally (in normal developer workflows) and intentionally (if the model is steered by an attacker via prompt injection to phrase its commit command this way to evade review).

### Recommendation
Do not rely on literal-substring/regex prefix matching against `git commit` as the sole gate for detecting a commit action, in either the `hooks.json` `"if"` matcher or `_GIT_COMMIT_RE`. Instead:
- Broaden the detection regex to tolerate git's global-option syntax before the subcommand, e.g. matching `\bgit(?:\s+(?:-c\s+\S+|--\S+(?:=\S+)?|-C\s+\S+))*\s+commit\b`, or more robustly, tokenize the command (shell-aware split) and scan for the first non-option token equal to `commit`.
- Alternatively (and more robustly), stop trying to pattern-match the Bash command string entirely and instead have the PostToolUse hook always run for `matcher: "Bash"` and independently detect whether a commit occurred using git state (e.g., compare `HEAD` before/after, or use the reflog-based fallback as the *primary* signal rather than a fallback contingent on the regex already having failed to match, which is exactly the scenario this bug shows the fallback cannot reach).
- Ensure the reflog-based detection (`_git_reflog_recent_commits`) runs regardless of the initial regex result, not only when the regex's associated `commit_succeeded` heuristic and stdout parsing both fail, since the `sys.exit(0)` guard currently short-circuits before that logic is ever reached for non-matching command strings.

### Proof of Concept
1. Start a Claude Code session with the security-guidance plugin enabled and `ENABLE_COMMIT_REVIEW` at its default (enabled).
2. Have Claude (or a prompt-injected instruction) stage a file containing an obviously reviewable issue (e.g., a hardcoded API key) and run:
   ```
   git -c commit.gpgsign=false commit -m "add config"
   ```
3. Observe that:
   - `hooks.json`'s `"if": "Bash(git commit:*)"` matcher does not match the command (it does not start with the literal `git commit` prefix), so the PostToolUse hook process for commit review is never invoked for this Bash call.
   - Even if the hook were invoked, `_GIT_COMMIT_RE.search(command)` returns no match, causing `handle_commit_review_posttooluse` to immediately `sys.exit(0)` at the guard [3](#0-2)  before any reflog fallback or LLM analysis runs.
4. Compare against a plain `git commit -m "add config"` of the same content, which does trigger the review and would normally surface the hardcoded secret finding — demonstrating the differential bypass.

### Citations

**File:** plugins/security-guidance/hooks/hooks.json (L35-44)
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
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L593-598)
```python
# Regex matching `git commit` commands. Mirrors Claude Code's own commit
# detection — it does NOT tolerate `git -c k=v commit` global options, which
# keeps this hook aligned with CC's commit attribution on what counts as a
# commit.
_GIT_COMMIT_RE = re.compile(r'\bgit\s+commit(?:\s|$)')
_GIT_AMEND_RE = re.compile(r'\s--amend\b')
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

**File:** plugins/security-guidance/hooks/review_api.py (L250-254)
```python
        "  NEVER apply NO-PRIVILEGE-BOUNDARY to: SSRF/outbound-"
        "network sinks; LLM-agent capability gates (PreToolUse/"
        "PostToolUse hooks, bash allow/denylists, workspace path "
        "jails — the model is the attacker, the user is the "
        "victim); data-exposure findings (CWE-200/359/532, secrets-"
```
