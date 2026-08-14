### Title
Coarse (filePath, category) dedup key in `_dedup_against_state`/`_finding_keys` silently drops unrelated dangerous findings surfaced via `_agentic_review_with_race` - (File: `plugins/security-guidance/hooks/security_reminder_hook.py`)

### Summary
`_agentic_review_with_race` races an agentic reviewer (which can run for up to `SG_AGENTIC_RACE_DELAY_S`, default 180s) against a delayed single-shot fallback, and its caller filters the returned vulnerabilities through `_dedup_against_state`, which keys solely on `(filePath, category)` via `_finding_keys`. Because this key ignores the actual vulnerable code/line, a genuinely new and unrelated dangerous finding in the same file and category as one recorded by a concurrent Stop-hook or commit-review fire during the race window is silently dropped with no warning ever shown to the user.

### Finding Description
`_agentic_review_with_race` (`security_reminder_hook.py:832-899`) starts the agentic reviewer immediately and, after `delay_s` (default 180s), starts a fallback single-shot review concurrently if agentic hasn't finished. Whichever result wins is returned to `handle_commit_review_posttooluse`. [1](#0-0) 

Because agentic reviews can legitimately take up to and beyond the 180s race delay, an attacker driving normal edit/commit/amend/push activity has a wide window in which the concurrent Stop hook can also run and write `previous_findings` to shared session state.

After the race returns `vulns`, the caller calls:
```
new_vulns, n_deduped = _dedup_against_state(
    session_id, vulns, prompted=_finding_keys(previous_findings)
)
``` [2](#0-1) 

`_dedup_against_state` re-reads `previous_findings` under lock, computes `race_delta = _finding_keys(fresh) - prompted`, and drops any `vuln` in `race_delta`: [3](#0-2) 

`_finding_keys` reduces every finding to `(filePath, category)` only — it does not incorporate `vulnerableCode`, line numbers, or any content hash: [4](#0-3) 

This same coarse key is used everywhere findings are recorded/deduped: in the Stop hook's `_record_fire` (`state["previous_findings"]` dedup on `(filePath, category)`) [5](#0-4) 
and in the prompt fed back to the LLM, which explicitly instructs the model to match "on file + category, not exact code bytes" and to only re-flag if it independently judges the change an "incomplete fix or introduces a new issue": [6](#0-5) 

**Exploit flow**: An unprivileged contributor working in a normal cloned-repo workflow can, within a single session:
1. Introduce a vulnerability of category X in file F (e.g., a minor "Command Injection" finding) — recorded to `previous_findings` by the Stop hook or a fast-finishing commit-review.
2. While the long-running agentic review of a *different*, later commit touching the same file F is still racing (up to 180s), introduce a second, genuinely different and more dangerous Command Injection issue in file F (different function/sink/attack path) via another edit+commit.
3. When the delayed/raced review of the second commit completes and returns the new, distinct finding for `(F, "Command Injection")`, `_dedup_against_state` treats it as part of `race_delta` (since the key already exists in `previous_findings`) and silently drops it — the code path performs a hard key-equality drop with no re-examination of `vulnerableCode`, regardless of whether it's actually the same issue.
4. No warning is emitted for the newly introduced dangerous code (`new_vulns` becomes empty → `sys.exit(0)` at `security_reminder_hook.py:1326-1332`), and the commit/push proceeds unreviewed and unblocked.

This defeats the stated invariant that dangerous edits/commands must remain reviewable and blockable across retries, amends, and pushes, because the deterministic dedup logic (not merely LLM judgment) suppresses the second finding purely on key collision.

### Impact Explanation
A genuinely new, dangerous code change (e.g., a real command injection, SSRF, or secret-exfiltration sink) can be silently exempted from security review and surfaced to the user with zero warning, allowing it to be committed and pushed. This directly enables sensitive code/diff/token disclosure or unreviewed dangerous command execution to reach an unintended sink, matching the "Sensitive code, prompt, token, diff, or local file disclosure to an unintended local or remote sink" impact category, since the security control meant to catch and block such changes is bypassed by ordinary, unprivileged repository activity.

### Likelihood Explanation
This requires only: (a) two ordinary commits/edits touching the same file with a shared vulnerability category label within the review race window (which can be as long as 180s by default, or configurable via `SG_AGENTIC_RACE_DELAY_S`), and (b) normal git workflow (edit → commit → amend/push) that any unprivileged contributor already performs. No admin privilege, credential leak, or social engineering is needed — the collision is purely mechanical on `(filePath, category)`. The condition is fully attacker-controllable (they choose which category/file to reuse) and reproducible deterministically once the timing window is triggered.

### Recommendation
Strengthen the dedup key used in `_finding_keys`/`_dedup_against_state` (and the corresponding `_record_fire` state dedup) to include a content-derived discriminator — e.g., a normalized hash of `vulnerableCode` (ignoring only whitespace/diff-context drift) or the specific line range — rather than collapsing on `(filePath, category)` alone. At minimum, only suppress a race-delta finding when its `vulnerableCode` closely matches (e.g., via fuzzy/substring comparison) an entry already recorded, so a distinct dangerous finding in the same file/category is never dropped purely due to key collision.

### Proof of Concept
Integration test plan:
1. Simulate two concurrent commit-review invocations against the same fake `session_id` and file `app.py`.
2. Seed `previous_findings` in shared state with a finding `{"filePath": "app.py", "category": "Command Injection", "vulnerableCode": "os.system(f'ping {host}')"}` (as the Stop hook would).
3. Invoke `_agentic_review_with_race`/`_dedup_against_state` on a second, unrelated finding: `{"filePath": "app.py", "category": "Command Injection", "vulnerableCode": "subprocess.run(cmd, shell=True)"}` representing a distinct injection sink introduced in a later, different commit.
4. Assert (current buggy behavior): `_dedup_against_state` returns `n_deduped == 1` and drops the second finding solely due to key collision on `(filePath, category)`, even though `vulnerableCode` differs entirely and represents an unrelated vulnerability.
5. Expected (fixed) behavior: the second, content-distinct finding survives dedup and is surfaced via `emit_metrics`/`sys.exit(2)` guidance to the user.

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L838-899)
```python
    """Race the agentic reviewer against a delayed single-shot fallback.

    Agentic starts at t=0. After SG_AGENTIC_RACE_DELAY_S (default 180s), the
    single-shot diff reviewer also starts. Whichever finishes first wins. If
    agentic finishes before the delay elapses, the fallback never runs.

    Metrics added:
      race_winner    : 1 = agentic won, 2 = fallback won (CC accepts only
                       bool/finite-number metric values — strings would discard the dict)
      race_delay_s   : the configured delay
      race_started   : 1 if the fallback was actually launched, else 0

    Only the commit-review handler calls this — external harnesses invoke
    agentic_review() directly and are unaffected. SG_AGENTIC_NO_RACE=1
    disables the race for any other caller that wants pure agentic.
    """
    import queue as _queue
    import threading as _th
    import time as _t

    if os.environ.get("SG_AGENTIC_NO_RACE") == "1":
        return agentic_review(repo_root, diff_files, rel_touched)

    delay_s = int(os.environ.get("SG_AGENTIC_RACE_DELAY_S", "180"))
    q: "_queue.Queue[Tuple[str, Any]]" = _queue.Queue(maxsize=1)
    fallback_started = _th.Event()

    def _agentic() -> None:
        try:
            r = agentic_review(repo_root, diff_files, rel_touched)
        except Exception as e:  # pragma: no cover — crash → let fallback win
            r = (None, [], {"agentic_fallback": f"race_crash:{type(e).__name__}"})
        try:
            q.put_nowait(("agentic", r))
        except _queue.Full:
            pass

    def _fallback() -> None:
        _t.sleep(delay_s)
        if not q.empty():
            return  # agentic finished within the delay — never start fallback
        fallback_started.set()
        try:
            g, v = analyze_code_security(
                diff_files, is_diff=True, previous_findings=previous_findings
            )
        except Exception as e:  # pragma: no cover
            g, v = None, []
        try:
            q.put_nowait(("fallback", (g, v, {"agentic": False})))
        except _queue.Full:
            pass

    _th.Thread(target=_agentic, daemon=True).start()
    _th.Thread(target=_fallback, daemon=True).start()

    winner, (g, v, m) = q.get()
    m = dict(m)  # don't mutate the callee's metrics dict
    m["race_winner"] = 1 if winner == "agentic" else 2
    m["race_delay_s"] = delay_s
    m["race_started"] = 1 if fallback_started.is_set() else 0
    return g, v, m
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1319-1324)
```python
    # Late dedup: drop only what a concurrent Stop hook wrote while our LLM
    # ran. Anything in `previous_findings` (the pre-LLM snapshot) that the
    # LLM chose to re-flag is an intentional "fix incomplete" verdict.
    new_vulns, n_deduped = _dedup_against_state(
        session_id, vulns, prompted=_finding_keys(previous_findings)
    )
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1900-1911)
```python
            # appended findings since consume_stop_state snapshotted.
            # Dedupe on (filePath, category) — vulnerableCode includes diff
            # context lines that drift between fires, so byte-identical
            # matching let the same finding accumulate as "new" each fire.
            existing = [f for f in state.get("previous_findings", []) if isinstance(f, dict)]
            seen = {(f.get("filePath", ""), f.get("category", "")) for f in existing}
            for f in finding_snapshots:
                key = (f["filePath"], f["category"])
                if key not in seen:
                    seen.add(key)
                    existing.append(f)
            state["previous_findings"] = existing
```

**File:** plugins/security-guidance/hooks/llm.py (L680-707)
```python
def _finding_keys(findings: List[Dict[str, Any]]) -> set:
    return {(f.get("filePath", ""), f.get("category", ""))
            for f in findings if isinstance(f, dict)}


def _dedup_against_state(session_id: str, vulns: List[Dict[str, Any]],
                         prompted: set) -> Tuple[List[Dict[str, Any]], int]:
    """Drop vulns that a CONCURRENT asyncRewake hook wrote to
    previous_findings while this hook's LLM was running.

    `prompted` is the (filePath, category) set the LLM was already told about
    via the prev_section prompt block. The LLM is instructed to only re-flag
    those if the attempted fix is incomplete, so a re-flag of a `prompted`
    entry is an intentional "fix didn't work" verdict and MUST pass through.
    We therefore re-read state now and only filter the race delta —
    (seen_now − prompted) — i.e. findings the LLM was never told about
    because they were written mid-review by the other hook.
    Returns (surviving_vulns, n_dropped).
    """
    if not vulns:
        return vulns, 0
    fresh = with_locked_state(
        session_id, lambda s: list(s.get("previous_findings", []))
    ) or []
    race_delta = _finding_keys(fresh) - prompted
    kept = [v for v in vulns
            if (v.get("filePath", ""), v.get("category", "")) not in race_delta]
    return kept, len(vulns) - len(kept)
```

**File:** plugins/security-guidance/hooks/llm.py (L773-782)
```python
        prev_section = (
            "PREVIOUS FINDINGS (already surfaced to the developer earlier this turn — DO NOT re-flag):\n"
            "The exact findings below were already shown to the developer, who has either fixed them or "
            "acknowledged them as not applicable. DO NOT report any finding whose (filePath, category) pair "
            "matches an entry below — it was already handled. The vulnerableCode may differ slightly from "
            "what you see now (diff context lines shift between fires) — match on file + category, not exact "
            "code bytes. ONLY re-flag a (filePath, category) from this list if the code at that location was "
            "CHANGED since the prior review and the change is an incomplete fix or introduces a new issue.\n"
            f"{prev_lines}\n"
        )
```
