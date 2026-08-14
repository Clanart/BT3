### Title
Shared per-session commit-review rate-limit budget can be exhausted by trivial commits, DOSing the security review hook for the rest of the session - (File: `plugins/security-guidance/hooks/security_reminder_hook.py`)

### Summary
`handle_commit_review_posttooluse` gates the LLM-based commit security review behind a single shared rolling-window budget, `atomic_check_rate_limit(session_id, "CommitReview", MAX_COMMIT_REVIEWS_PER_HOUR, COMMIT_REVIEW_RATE_WINDOW_S)`, keyed only by the literal string `"CommitReview"` for the whole session [1](#0-0) . Any `git commit` invocation that produces a reviewable diff consumes one slot from this shared budget, regardless of who or what triggered the commit or how trivial the change is. This mirrors the `repayDebt`/`totalDebt` bug class: a single shared accounting value that any reachable action can drain, after which a security-relevant control (here, the automated vulnerability review on commits) silently stops functioning for the rest of the window — a DOS on the review functionality rather than a smart-contract market.

### Finding Description
The rate limiter itself is implemented in `atomic_check_rate_limit`, which tracks a rolling list of timestamps per `(session_id, key)` pair and returns `False` once `max_per_window` calls have occurred within `window_s` seconds [2](#0-1) . The commit-review handler consumes this budget under the fixed key `"CommitReview"` before running any LLM analysis: [1](#0-0) 

The comment explicitly documents that this budget replaced a lifetime cap specifically because "a small but persistent share of sessions hit the lifetime cap" and "lost coverage for many subsequent commits" — i.e., the designers already know exhausting this counter suppresses future security coverage; they only changed the reset cadence (rolling hour) rather than scoping the cap so that unrelated/attacker-triggered commits can't consume budget meant for legitimate ones [3](#0-2) .

Any `git commit` command matched by `_GIT_COMMIT_RE` inside a `Bash` `PostToolUse` event routes into this handler [4](#0-3) . As long as the commit produces at least one reviewable diff file (bypassing the earlier `if not diff_files: skip` gate), it consumes one slot of the shared `MAX_COMMIT_REVIEWS_PER_HOUR` budget for the session — exactly like `repayDebt()` decrementing the shared `totalDebt` regardless of which borrower's debt is nominally being repaid.

Because the counter is keyed only by the literal `"CommitReview"` string (not by file, author, or purpose of the commit), any reachable source of many small, real (non-empty) commits within a session — e.g. a compromised/malicious pre-existing repo automation script, a subagent instructed via prompt injection embedded in file content, or a plugin/skill invoked without the user's awareness — can run a loop of trivial `git commit` calls that each touch one reviewable line. Each iteration burns one budget slot. Once `MAX_COMMIT_REVIEWS_PER_HOUR` is reached, subsequent legitimate commits in the same rolling hour skip the review entirely (`skip_reason=23`) [5](#0-4) , silently disabling the protective security-review hook for the rest of that window — no error is surfaced to the user beyond a debug log line.

### Impact Explanation
This is a DOS of a security control rather than of a financial market, but the structural bug class is identical to the report: a single unprivileged, reachable action (issuing a `git commit`) decrements a shared counter that gates functionality relied upon by everyone in that session, with no per-actor/per-purpose accounting. The consequence is that the automated vulnerability review — the plugin's core protective mechanism — can be silently disabled for genuine, security-relevant commits for up to an hour, without any indication to the user that coverage was lost (only `debug_log`, not surfaced in the guidance shown to the user).

### Likelihood Explanation
Moderate. It requires an actor able to make the CC session issue a series of real (non-empty, diff-producing) `git commit` calls in quick succession — most plausibly a compromised repository automation script (e.g., a pre-commit/post-commit tooling script, a CI-like local script, or a subagent/skill run without careful human review) rather than a fully external attacker with no CC session access at all. Given how routine `git commit` usage is in agentic coding sessions, and that the budget is shared across the whole session with no per-source isolation, this is a plausible, low-effort DOS path once any code execution or automation within the session is attacker-influenced.

### Recommendation
Scope the rate-limit key (or add a secondary cap) so that unrelated/bulk commits cannot exhaust the budget meant for legitimate review coverage — e.g., track budget consumption per distinct file/diff-hash set, require a minimum "meaningful diff size" before consuming a slot, or surface a hard, visible warning to the user (not just a debug log) once the shared review budget is exhausted, so silent security-coverage loss is not possible.

### Proof of Concept
Not independently executed; reasoning is based on the static code path: `Bash`/`PostToolUse` → `_GIT_COMMIT_RE` match → `handle_commit_review_posttooluse` → `atomic_check_rate_limit(session_id, "CommitReview", MAX_COMMIT_REVIEWS_PER_HOUR, COMMIT_REVIEW_RATE_WINDOW_S)` [4](#0-3) [1](#0-0) . I was not able to read the exact numeric values of `MAX_COMMIT_REVIEWS_PER_HOUR` and `COMMIT_REVIEW_RATE_WINDOW_S` before the tool budget ran out (grep confirmed their definitions exist in the file but the surrounding lines were not retrieved), so the concrete number of commits needed to exhaust the budget is unconfirmed — this should be verified in a follow-up session before treating the severity as final.

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

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L2095-2109)
```python
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
