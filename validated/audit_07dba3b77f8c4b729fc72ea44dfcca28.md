### Title
Stop-hook loop-firing cap can be reset indefinitely by pacing exit(2) cycles beyond the TTL, defeating the security-review loop limiter - (File: plugins/security-guidance/hooks/diffstate.py)

### Summary
The `security-guidance` plugin's Stop hook enforces a cap, `MAX_STOP_HOOK_FIRINGS`, on how many times the "asyncRewake" loop (finding a vulnerability → `exit(2)` → model attempts a fix → Stop fires again) may re-trigger in a session. This cap is backed by a counter, `stop_hook_fire_count`, that is intentionally auto-expired after a fixed TTL, `STOP_LOOP_STATE_TTL_SEC = 120`, so that "a stale count from a prior turn doesn't block this one." [1](#0-0)  If more than 120 seconds elapse between consecutive Stop-hook firings, `consume_stop_state` treats the counter as expired and resets it to `0` before the cap check runs: [2](#0-1) . This mirrors the OpenBazaar `Escrow_v1_0` pattern where a party who controls the pacing of a state-mutating action can indefinitely reset a "time since last state change" counter and thereby prevent a limiter/timeout from ever engaging.

### Finding Description
The Stop-hook cap check reads:
```
if MAX_STOP_HOOK_FIRINGS > 0 and fire_count >= MAX_STOP_HOOK_FIRINGS:
    debug_log(f"Stop hook: already fired {fire_count} times (max {MAX_STOP_HOOK_FIRINGS}), skipping")
    _skip(2)
``` [3](#0-2) 

`fire_count` is not a monotonically-accumulating value guarded purely by a count; it is gated by elapsed wall-clock time via `stop_hook_fire_count_ts` and `STOP_LOOP_STATE_TTL_SEC`:
```
fire_ts = state.get("stop_hook_fire_count_ts", 0)
expired = (now - fire_ts) > STOP_LOOP_STATE_TTL_SEC
...
"fire_count": 0 if expired else state.get("stop_hook_fire_count", 0),
``` [4](#0-3) 

The code comment explicitly documents the intended cycle time as "~30-60s/cycle" and sets the TTL to "comfortably contain[]" the max-firings limit within that cadence [5](#0-4) . This is the same class of bug as the escrow contract's `lastModified`/`timeoutHours` logic: a safety threshold (`MAX_STOP_HOOK_FIRINGS`) is enforced only against a counter that resets whenever the interval since the last "modification" (here, the last Stop-hook fire) exceeds a fixed window. Any actor able to influence the pacing of the loop — for example, untrusted repository content that triggers the security-review "vulnerability found → fix" cycle and can be crafted to make each fix/review iteration take longer than 120 seconds (e.g., large diffs, slow tool calls, or an LLM review that is deliberately delayed) — can keep `now - fire_ts` above `STOP_LOOP_STATE_TTL_SEC` on every iteration. In that scenario `expired` is always `True`, the counter never accumulates past `MAX_STOP_HOOK_FIRINGS`, and the Stop hook can be forced to keep re-blocking the turn (`decision: block` semantics of the Stop hook) indefinitely, exactly as the buyer in the report indefinitely resets `lastModified` to defeat `isTimeLockExpired`.

### Impact Explanation
If the firing cap can be defeated by simply pacing loop iterations more slowly than the TTL, the intended defense-in-depth (bounding how many times the asyncRewake security-review loop can re-trigger per session) is nullified. Practically this enables a workspace/repository-controlled content path (files reviewed by the security-guidance hook) to keep the session's Stop hook perpetually blocking exit/continuing the review-fix cycle without ever hitting the configured ceiling, which is a resource-exhaustion / automation-bleed condition inside the hook's own trust boundary (hook-imposed loop limiter). It does not by itself grant shell execution or secret disclosure, but it defeats a specific anti-DoS/anti-loop control that the codebase's own comments indicate exists specifically to bound this loop.

### Likelihood Explanation
Exploitation requires the ability to influence the timing of the vulnerability-found → exit(2) → fix → Stop-fires-again cycle to consistently exceed 120 seconds, which is plausible given normal cycle times are already noted as 30-60s and various factors (large files, slow LLM review calls in `llm.py`, network latency, big diffs) can push a cycle past 120s without any special privilege — the attacker only needs to shape the content being reviewed. Because this is an unprivileged-content/workspace-driven trust boundary in a bundled plugin rather than requiring a malicious operator or external node, the likelihood is moderate.

### Recommendation
Do not gate `MAX_STOP_HOOK_FIRINGS` purely on a TTL-expiring counter tied to inter-fire latency. Instead, track a monotonically increasing per-session firing count that only resets on well-defined session-boundary events (e.g., explicit session end, explicit user-initiated reset, or `UserPromptSubmit` marking a genuinely new turn) rather than on elapsed wall-clock time since the last fire. If a TTL-based reset is required to avoid stale-turn carryover, decouple it from the firing cap itself — e.g., keep an absolute cap on total fires within a bounded wall-clock window (rate limiting) instead of resetting the count to zero whenever any single interval exceeds the TTL.

### Proof of Concept
1. Configure the `security-guidance` plugin with `ENABLE_CODE_SECURITY_REVIEW`/`ENABLE_STOP_REVIEW` on and a small `MAX_STOP_HOOK_FIRINGS` (e.g., 3).
2. Introduce a change/file that reliably triggers a vulnerability finding on each Stop-hook review pass, causing the loop's `exit(2)`-driven "asyncRewake" fix cycle documented in `diffstate.py` comments [5](#0-4) .
3. Ensure each fix/review round trip takes longer than `STOP_LOOP_STATE_TTL_SEC` (120s) — e.g., by making the file large enough that `compute_v2_review_set`/LLM review calls in `security_reminder_hook.py` take over 2 minutes, or introducing artificial latency in tool use between Stop-hook fires.
4. Observe via `debug_log` output that `fire_count` in `consume_stop_state` is reset to `0` on every fire because `expired` evaluates `True` each time [6](#0-5) , so the check at `security_reminder_hook.py:1766` (`fire_count >= MAX_STOP_HOOK_FIRINGS`) never trips, and the Stop hook keeps blocking/re-firing indefinitely regardless of `MAX_STOP_HOOK_FIRINGS`.

Note: I could not directly view the `_record_fire`/`stop_hook_fire_count_ts`-setting code path or the full `handle_stop_hook` function body due to tool-call limits reached before completing that read, so the exact write-side logic that increments/timestamps `stop_hook_fire_count` was not fully verified in this pass — only the read/expiry side in `consume_stop_state` and the cap-check call site were confirmed. A follow-up session with file access would be needed to confirm there is no additional monotonic safeguard elsewhere in `security_reminder_hook.py`.

### Citations

**File:** plugins/security-guidance/hooks/diffstate.py (L25-29)
```python
# stop_hook_fire_count expires after this many seconds.
# The asyncRewake loop (vuln→exit(2)→fix→Stop again) is ~30-60s/cycle, so 120s
# comfortably contains MAX_STOP_HOOK_FIRINGS while letting the next user turn
# proceed unblocked. Replaces the UPS-reset that raced against background Stop.
STOP_LOOP_STATE_TTL_SEC = 120
```

**File:** plugins/security-guidance/hooks/diffstate.py (L89-107)
```python
    def _snap(state):
        fire_ts = state.get("stop_hook_fire_count_ts", 0)
        expired = (now - fire_ts) > STOP_LOOP_STATE_TTL_SEC
        findings_ts = state.get("previous_findings_ts", fire_ts)
        findings_expired = (now - findings_ts) > PREVIOUS_FINDINGS_TTL_SEC
        snap = {
            "touched_paths": list(state.get("touched_paths", [])),
            "baseline_sha": state.get("baseline_sha"),
            "head_at_capture": state.get("head_at_capture"),
            "untracked_at_baseline": (
                dict(state["untracked_at_baseline"])
                if isinstance(state.get("untracked_at_baseline"), dict) else {}
            ),
            "fire_count": 0 if expired else state.get("stop_hook_fire_count", 0),
            "fire_count_expired": expired and state.get("stop_hook_fire_count", 0) > 0,
            "previous_findings": [] if findings_expired else list(state.get("previous_findings", [])),
        }
        state["touched_paths"] = []
        return snap
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
