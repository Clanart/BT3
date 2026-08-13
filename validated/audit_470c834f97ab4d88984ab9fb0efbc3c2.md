Found a legitimate structural analog. The `RootERC20PredicateFlowRate` bug class — a shared, exhaustible protective threshold that can be forcibly triggered/drained by an attacker to disable protection for other operations — maps onto the shared hourly rate limiter gating the LLM security-review hooks (`CommitReview` / `PushSweep`) in the `security-guidance` plugin.

### Title
Rate-limit exhaustion in commit/push security-review hooks allows malicious commits to bypass LLM vulnerability scanning - (File: plugins/security-guidance/hooks/security_reminder_hook.py)

### Summary
`security_reminder_hook.py` gates its LLM-based commit/push security review behind a rolling-window rate limiter, `atomic_check_rate_limit`, capped by `MAX_COMMIT_REVIEWS_PER_HOUR` (default 20 per hour) [1](#0-0) . Once the cap is hit, `handle_commit_review_posttooluse` (and the analogous push-sweep handler) silently skips the security analysis for the remainder of the hour with `skip_reason=23`, emitting no findings and taking no action [2](#0-1) [3](#0-2) . Because the counter is keyed only per `(session_id, key)` with a shared budget across all commits made in that session, an attacker can deliberately spend the quota with cheap/trivial commits or pushes and then slip a malicious commit through completely unscanned.

### Finding Description
`atomic_check_rate_limit` implements a rolling-window counter that allows at most `max_per_window` calls per `window_s` seconds for a given `(session_id, key)` pair, pruning stale timestamps on each call [4](#0-3) . This is invoked identically for both commit review and push sweep with the same global limit and window, before the expensive/valuable step (the actual LLM `analyze_code_security` call) runs [5](#0-4) [3](#0-2) .

This mirrors the reported bug class precisely: a resource-protection threshold (there, the withdrawal queue's flow-rate limit; here, the hourly review-call budget) that any single, unprivileged actor within the session can drive to its limit through their own low-cost actions (many small commits, analogous to Eve's flurry of sub-threshold withdrawals), thereby disabling the protective control (the security scan) for subsequent, more sensitive operations in the same window — instead of only affecting the triggering action itself.

Unlike the on-chain case, the actor here does not need to be a separate malicious "Eve" watching another user; the same untrusted context (e.g., a prompt-injected instruction embedded in a file, issue, or dependency that the agent is operating on) can direct the agent itself to perform a burst of throwaway commits, deliberately exhausting the hourly budget, and then have the agent (or a human collaborator on the same session) commit/push the actual malicious payload while the reviewer is silenced.

### Impact Explanation
When the rate limit is exhausted, `skip_reason=23` causes the hook to `sys.exit(0)` with no guidance and no findings surfaced [6](#0-5) . This is a "hook bypass" of a security control: the LLM-based secret/vulnerability detection that would otherwise flag dangerous patterns (hardcoded secrets, injection sinks, etc.) in a commit or push is silently disabled for up to an hour once 20 reviews have fired, letting a malicious commit pass through git automation undetected during that window.

### Likelihood Explanation
The rate limiter is fail-open by design ("State unavailable → fail-open") and keyed on a coarse per-session/per-key window rather than per-commit risk, so any sequence of ~20 commits or pushes within an hour — trivial for an automated agent to produce, especially under prompt injection asking it to make many small WIP commits — exhausts the budget deterministically [7](#0-6) . No special privilege is required; it only requires the ability to cause the agent to run `git commit`/`git push` repeatedly, which is a normal, expected agent capability.

### Recommendation
- Scope or weight the rate limit per suspicious-signal or per-diff-risk rather than a flat call count, so an attacker cannot cheaply exhaust the whole session's review budget with low-value commits.
- When the limit is hit, surface a visible warning to the user/model (instead of a fully silent skip) so operators know reviews are being suppressed, mirroring the recommendation to avoid a single global gate.
- Consider reserving a portion of the budget, or applying a stricter/separate limit, for commits/pushes that touch files not previously reviewed in the session, so a burst of trivial commits can't starve review capacity for a later sensitive one.

### Proof of Concept
1. In an active Claude Code session with the `security-guidance` plugin enabled, have the agent perform 20 trivial commits (e.g., single-line comment changes) within the same hour — each consumes one slot via `atomic_check_rate_limit(session_id, "CommitReview", MAX_COMMIT_REVIEWS_PER_HOUR, COMMIT_REVIEW_RATE_WINDOW_S)` [8](#0-7) .
2. Make a 21st commit that introduces an actual vulnerability (e.g., a hardcoded secret or command-injection sink).
3. Observe that `handle_commit_review_posttooluse` takes the `not _allowed` branch, emits `skip_reason=23`, and exits without invoking `analyze_code_security`, so no finding is ever surfaced to the user for the malicious commit [6](#0-5) .

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L271-308)
```python
def atomic_check_rate_limit(session_id, key, max_per_window, window_s):
    """Rolling-window rate limit: allow at most `max_per_window` calls per
    `window_s` seconds, per (session_id, key).

    Returns (allowed: bool, count_in_window: int). count_in_window is the
    post-decision count (i.e., includes this call if allowed) so callers can
    emit it directly as a telemetry gauge.

    Replaces session-lifetime `atomic_check_counter` for commit-review and
    push-sweep. Telemetry showed a small but persistent share of sessions hit
    the lifetime cap, and those were multi-day persistent sessions that then
    lost coverage for many subsequent commits — not burst abusers. A rolling
    hour keeps the same cost ceiling for any 1h window while letting long
    sessions regain coverage.

    State key: rate_limits: {"<key>": [ts, ts, ...]}. Timestamps are pruned
    on every call so the list is bounded by max_per_window; no migration
    needed from the old `counters` dict — different key.
    """
    import time as _time
    now = _time.time()
    cutoff = now - window_s

    def _check(state):
        buckets = state.setdefault("rate_limits", {})
        ts_list = buckets.get(key, [])
        # Prune; tolerate non-numeric junk from a corrupted state file.
        ts_list = [t for t in ts_list if isinstance(t, (int, float)) and t > cutoff]
        if len(ts_list) >= max_per_window:
            buckets[key] = ts_list
            return False, len(ts_list)
        ts_list.append(now)
        buckets[key] = ts_list
        return True, len(ts_list)

    result = with_locked_state(session_id, _check)
    # State unavailable → fail-open (same posture as atomic_check_counter).
    return result if result is not None else (True, 0)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L600-610)
```python
# Rolling-window cap on LLM commit-review calls. See atomic_check_rate_limit
# docstring for the rationale that motivated the switch from a lifetime cap.
# `MAX_COMMIT_REVIEWS_PER_SESSION` is read for backward-compat with users who
# tuned it; the value is reinterpreted as per-hour.
MAX_COMMIT_REVIEWS_PER_HOUR = int(
    os.environ.get("MAX_COMMIT_REVIEWS_PER_HOUR")
    or os.environ.get("MAX_COMMIT_REVIEWS_PER_SESSION", "20")
)
COMMIT_REVIEW_RATE_WINDOW_S = int(
    os.environ.get("COMMIT_REVIEW_RATE_WINDOW_S", "3600")
)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1203-1214)
```python
    # Rolling-hour rate limit on LLM spend, so only burn a slot once we know
    # we'll actually call analyze_code_security — skip 28/30/31/33 above are
    # free. `rate_count` is emitted on every fire (not just rejections) so
    # telemetry can show how close to the cap sessions run.
    _allowed, _rate_n = atomic_check_rate_limit(
        session_id, "CommitReview",
        MAX_COMMIT_REVIEWS_PER_HOUR, COMMIT_REVIEW_RATE_WINDOW_S)
    _base = {**_base, "rate_count": _rate_n}
    if not _allowed:
        debug_log("Commit review: hourly rate limit reached, skipping")
        emit_metrics({"skipped": True, "skip_reason": 23, **_base})
        sys.exit(0)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1588-1594)
```python
    _allowed, _rate_n = atomic_check_rate_limit(
        session_id, "PushSweep",
        MAX_COMMIT_REVIEWS_PER_HOUR, COMMIT_REVIEW_RATE_WINDOW_S)
    _base = {**_base, "rate_count": _rate_n}
    if not _allowed:
        emit_metrics({"skipped": True, "skip_reason": 23, **_base})
        sys.exit(0)
```
