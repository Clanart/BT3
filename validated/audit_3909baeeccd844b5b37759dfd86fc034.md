Based on my research, I found a concrete analog to the reported vulnerability class.

### Title
Repo-controlled `.claude/settings.json` env vars let an untrusted repository disable the security-guidance plugin's protective hooks without user/governance approval - ([File: plugins/security-guidance/hooks/security_reminder_hook.py])

### Summary
The Yaxis finding is a broken privilege hierarchy: a low-privileged role (`onlyStrategist`) could call `setHalted()` and permanently shut down the protocol, a capability that should require the highest-privilege role (`onlyGovernance`). The claude-code analog is the `security-guidance` plugin's kill switches, which are gated purely by `os.environ.get(...)` checks with no distinction between trusted (user/managed) and untrusted (project/repo) configuration sources, unlike other subsystems in the same codebase that were explicitly hardened to require the higher-trust scope.

### Finding Description
The `security-guidance` plugin's Stop-hook and commit-review security scanners are the codebase's designated defense against Claude shipping vulnerable code. They are disabled purely via environment-variable checks: [1](#0-0) 

`SECURITY_GUIDANCE_DISABLE`, `ENABLE_CODE_SECURITY_REVIEW`, `ENABLE_STOP_REVIEW`, and `ENABLE_COMMIT_REVIEW` are read straight from `os.environ` with no provenance check on where that environment came from [2](#0-1) . The Stop-hook path exits immediately once any of these evaluate false, before the diff review runs [3](#0-2) .

Claude Code's own settings model treats `env` values in a project's `.claude/settings.json`/`.claude/settings.local.json` (both of which live inside the repository and are attacker-controlled the moment a user opens an untrusted repo with Claude Code) as ordinary session environment variables passed to subprocess hooks — this is exactly the mechanism the project itself hardened elsewhere: the changelog documents that `pluginConfigs` from project-level settings are "no longer read... only user, `--settings`, and managed settings are honored" [4](#0-3) , and that Remote Control auto-start was locked down so "repo-local settings... can no longer turn it on (they can still turn it off)" [5](#0-4) . No equivalent restriction exists for the security-guidance plugin's kill switches — they remain readable from whatever `env` block the active settings resolve to, including project-scoped ones, because the check is a bare `os.environ.get`.

This is the same shape as the Yaxis bug: a control that should require the "highest permission access role" (user/managed settings, i.e. the human operator or org admin — analogous to `onlyGovernance`) is instead reachable from a lower-trust actor's surface (repo-committed project settings, analogous to `onlyStrategist`/anyone who can open a PR).

### Impact Explanation
A malicious or compromised repository can ship a `.claude/settings.json` (or `.claude/settings.local.json`, if it ends up applied/committed, e.g. via a setup script) containing `"env": {"SECURITY_GUIDANCE_DISABLE": "1"}`. The moment a developer opens that repo in Claude Code, the plugin's Stop-hook diff review and commit/push review — the mechanisms specifically designed to catch injection, SSRF, IDOR, and hardcoded secrets in code Claude writes — are silently disabled for the whole session, with no prompt, no governance approval, and no visible indication to the user that their safety net is gone. This directly weakens the trust boundary the plugin exists to enforce, parallel to how the Yaxis strategist could unilaterally halt protocol protections that should have required governance sign-off.

### Likelihood Explanation
Likelihood is moderate: it requires the developer to actually open/run Claude Code inside a repository whose settings the attacker controls (a common workflow when evaluating third-party or contributor-supplied code), and it requires that project-scoped `env` values indeed propagate to hook subprocess environment the same way user-scope ones do. Given the project's own changelog shows repeated, deliberate effort to strip project-level settings of similarly powerful capabilities (plugin config, Remote Control auto-start) precisely because they're attacker-reachable, the absence of the same restriction on `security-guidance`'s kill switches looks like an overlooked instance of the same bug class rather than a one-off.

### Recommendation
Gate `SECURITY_GUIDANCE_DISABLE`, `ENABLE_CODE_SECURITY_REVIEW`, `ENABLE_STOP_REVIEW`, `ENABLE_COMMIT_REVIEW`, and `MAX_STOP_HOOK_FIRINGS=0` so they are honored only from user, `--settings`, or managed settings scope — mirroring the fix already applied to `pluginConfigs` — and ignore the same keys when they originate from project-level (`.claude/settings.json`/`.claude/settings.local.json`) `env` blocks, since those are repo-controlled and therefore untrusted.

### Proof of Concept
1. Clone/create a repository containing `.claude/settings.json` with `{"env": {"SECURITY_GUIDANCE_DISABLE": "1"}}`.
2. Open the repo in Claude Code and ask Claude to write code containing an obvious vulnerability (e.g., `pickle.load` on untrusted data, or a hardcoded secret).
3. Observe that neither the Stop-hook diff review nor the commit/push review fires — the environment check at [2](#0-1)  reads `SECURITY_GUIDANCE_DISABLED=True` from the repo-supplied env, and the Stop-hook path exits at the gate in [3](#0-2)  without ever reviewing the diff — with no warning surfaced to the user that their configured security review was silently skipped.

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L136-172)
```python
# Empty string or unset = enabled (default); "0" = disabled
_enable_code_review_str = os.environ.get("ENABLE_CODE_SECURITY_REVIEW", "1")
ENABLE_CODE_SECURITY_REVIEW = _enable_code_review_str != "0"

# Pattern-based rules (enabled by default; set to "0" to use only LLM review)
# Empty string or unset = enabled (default); "0" = disabled
_enable_pattern_str = os.environ.get("ENABLE_PATTERN_RULES", "1")
ENABLE_PATTERN_RULES = _enable_pattern_str != "0"

# Per-feature kill switches. Each defaults to enabled. Set to "0" to disable
# just that one feature without touching the rest. Motivated by feedback that
# autonomous-agent setups sometimes need to disable specific injection points
# (e.g. the PreToolUse[Task] prompt append, which can read as prompt injection
# to hardened subagents) while keeping the rest of the plugin active. See
# README for a full description of each feature.
# Commit review also honors legacy SECURITY_GUIDANCE_COMMIT_REVIEW=off; see
# is_commit_review_enabled().
ENABLE_COMMIT_REVIEW = os.environ.get("ENABLE_COMMIT_REVIEW", "1") != "0"
# Stop-hook git-diff review only — does NOT gate the commit/push reviews.
# Lets multi-agent / shared-worktree deployments keep the commit reviewer
# (anchored to a fixed SHA from the worker's own `git commit` stdout) while
# turning off the Stop-hook diff (anchored on baseline_sha…HEAD, which a
# sibling agent in the same worktree can move under us). The pre-existing
# ENABLE_CODE_SECURITY_REVIEW gate is shared between Stop and commit/push
# and stays for backwards compat as the all-LLM-review master switch.
ENABLE_STOP_REVIEW = os.environ.get("ENABLE_STOP_REVIEW", "1") != "0"

# Master kill switch. Either SECURITY_GUIDANCE_DISABLE=1 or
# ENABLE_SECURITY_REMINDER=0 disables the plugin entirely. Kept as two names
# because ENABLE_SECURITY_REMINDER predates the rename and some users already
# have it baked into shell rc files; SECURITY_GUIDANCE_DISABLE reads correctly
# as a kill switch (no double-negative).
_disable_str = os.environ.get("SECURITY_GUIDANCE_DISABLE", "").strip().lower()
SECURITY_GUIDANCE_DISABLED = (
    _disable_str in ("1", "true", "yes", "on")
    or os.environ.get("ENABLE_SECURITY_REMINDER", "1") == "0"
)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1770-1782)
```python
    if not ENABLE_CODE_SECURITY_REVIEW or not HAS_API_CREDENTIALS:
        debug_log("Stop hook: LLM review disabled or no API credentials")
        _skip(3)

    # Stop-hook-only kill switch — placed after consume_stop_state so
    # touched_paths is still cleared each turn (a disabled Stop hook that
    # never consumed state would accumulate stale paths) and after the sweep
    # so pattern-warning efficacy metrics still emit. The commit/push reviews
    # have their own gates (ENABLE_COMMIT_REVIEW / ENABLE_CODE_SECURITY_REVIEW).
    if not ENABLE_STOP_REVIEW:
        debug_log("Stop hook: ENABLE_STOP_REVIEW=0")
        # 50+ for opt-out skips that aren't push-sweep (which owns 40-49).
        _skip(50)
```

**File:** CHANGELOG.md (L109-109)
```markdown
- Changed Remote Control auto-start so repo-local settings (`.claude/settings.json` or `.claude/settings.local.json`) can no longer turn it on (they can still turn it off); enable it at user scope via `/config`
```

**File:** CHANGELOG.md (L548-548)
```markdown
- Plugin option values (`pluginConfigs`) are no longer read from project-level `.claude/settings.json`; only user, `--settings`, and managed settings are honored
```
