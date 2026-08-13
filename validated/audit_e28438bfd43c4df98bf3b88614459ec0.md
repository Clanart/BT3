## Title
Attacker-controlled repo activity can reset the Stop-hook loop counter to keep forcing costly LLM security reviews indefinitely - (File: plugins/security-guidance/hooks/security_reminder_hook.py)

### Summary
The `TRST-H-3` report describes a DCA automation whose continuation trigger (`hasZeroBalance`) is defined over state that an unprivileged third party can manipulate (donating dust tokens), so the automation never satisfies its own exit condition and keeps charging the victim for repeated low-value executions. The closest analog in this codebase is the `security-guidance` plugin's `Stop` hook loop-guard in `plugins/security-guidance/hooks/security_reminder_hook.py`, which caps repeated Stop-hook firings with `MAX_STOP_HOOK_FIRINGS` but resets that counter based on a time-to-live rather than on genuine task completion.

### Finding Description
The Stop hook increments a `fire_count` each time it runs inside an `asyncRewake` loop and refuses to keep firing once `fire_count >= MAX_STOP_HOOK_FIRINGS` [1](#0-0) . However, the surrounding comment states this count "auto-expires after `STOP_LOOP_STATE_TTL_SEC` so a stale count from a prior turn doesn't block this one" [1](#0-0) . This means the guard is time-bound, not action-bound: as long as an external actor can keep the session performing tool calls (e.g., commits/pushes) spaced out past the TTL window, or can keep injecting new file changes/commits into the repo that repeatedly re-trigger the security review via `PostToolUse[Bash]` `if: "Bash(git commit:*)"` / `"Bash(git push:*)"` hooks with `asyncRewake: true` [2](#0-1) , the loop-limiting counter resets and the expensive LLM-based review (`analyze_code_security`/`agentic_review`, which calls out to the Anthropic API) keeps re-firing rather than converging to a stop.

This mirrors the `canInitSwap` pattern in the report: the "keep going" condition (`fire_count < MAX_STOP_HOOK_FIRINGS`) is reset by a factor (elapsed time / new commits) that is outside the victim's control and can be perpetuated by anyone who can cause repo activity (e.g., a malicious pre-commit/CI script, a compromised dependency invoked during the session, or content that induces the agent to keep committing/pushing), rather than by the review genuinely completing.

### Impact Explanation
Each Stop-hook firing invokes an LLM-based security review over the diff (`analyze_code_security`, `agentic_review`) [3](#0-2) , which consumes API credits/tokens tied to the user's account, similar to the gas cost in the original report. If the TTL-based reset can be repeatedly triggered by externally influenced repo activity, the user is billed for review calls indefinitely without control over when the loop actually terminates, exactly analogous to the "attacker forces repeated low-value swaps forever" scenario.

### Likelihood Explanation
This is a **weak, largely unverified** analog. I was not able to fully inspect `STOP_LOOP_STATE_TTL_SEC`'s exact value or how `fire_count` state is persisted/reset (the file is large and I could not read the complete `handle_stop_hook` function, `consume_stop_state`, or `diffstate.py` in this pass due to iteration limits). It's plausible the reset window and gating (`ENABLE_STOP_REVIEW`, `ensure_anthropic_reachable`, `review_paths` emptiness checks at lines 1770-1795) are tuned tightly enough that this isn't practically exploitable by an unprivileged party — the mechanism looks like it was already purpose-built as a safety cap (comment explicitly calls out "prevent infinite loops"), suggesting the team already considered and mitigated most of this risk, unlike the DeFi bug which was undiscovered until reported.

### Recommendation
Given the incomplete verification, I cannot confidently assert this constitutes a concrete, exploitable vulnerability rather than an already-adequate mitigation. If pursued, a background agent should read the full `handle_stop_hook` function and `diffstate.py` in `plugins/security-guidance/hooks/security_reminder_hook.py` to confirm: (1) the exact TTL value and reset semantics of `fire_count`, (2) whether the counter is keyed per-session/per-turn or per some externally-influenceable identifier, and (3) whether repo activity that an unprivileged contributor could produce (e.g., via a PR, git hook, or CI) can force additional review cycles beyond the intended cap without genuine task completion, analogous to the `DUST_AMOUNT` mitigation recommended in the report (i.e., basing the exit/continue decision on genuine completion, not a resettable proxy).

### Proof of Concept
Not constructed — this requires confirming the TTL value and the storage location/scope of `fire_count`/`STOP_LOOP_STATE_TTL_SEC`, which was not fully readable within available tool budget. Given the uncertainty, treat this as a **speculative, low-confidence analog** rather than a confirmed vulnerability.

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L120-133)
```python
import llm  # noqa: E402  module ref for reassignable globals (_last_call_claude_http_error etc.)
from llm import (  # noqa: E402,F401
    ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, HAS_API_CREDENTIALS,
    SECURITY_REVIEW_MODEL, CLAUDE_CODE_SYSTEM_PROMPT,
    _last_call_claude_http_error,
    ensure_anthropic_reachable,
    _last_review_truncated_bytes, _auth_prefer_token,
    DIFF_PER_FILE_BYTES, DIFF_TOTAL_BYTES, _AGENTIC_INVESTIGATE_SYSTEM,
    _FINDINGS_SCHEMA, _SURVIVED_SCHEMA, _REWAKE_SUMMARY_BUDGET,
    _cap_files_for_prompt, _build_auth_headers, _call_claude, _call_claude_dual_or,
    _format_vulns_guidance, _format_vulns_summary, _finding_keys, _dedup_against_state,
    analyze_code_security, _agentic_commit_review_enabled, agentic_review,
    analyze_security_concerns,
)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1763-1768)
```python
    # Limit stop hook firings per asyncRewake loop to prevent infinite loops.
    # fire_count auto-expires after STOP_LOOP_STATE_TTL_SEC so a stale count
    # from a prior turn doesn't block this one.
    if MAX_STOP_HOOK_FIRINGS > 0 and fire_count >= MAX_STOP_HOOK_FIRINGS:
        debug_log(f"Stop hook: already fired {fire_count} times (max {MAX_STOP_HOOK_FIRINGS}), skipping")
        _skip(2)
```

**File:** plugins/security-guidance/hooks/hooks.json (L36-52)
```json
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
```
