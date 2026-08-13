### Title
Warning dedup keyed only on (filePath, category) lets a new dangerous commit permanently reuse a stale finding key and suppress its own warning - (File: `plugins/security-guidance/hooks/security_reminder_hook.py`)

### Summary
`handle_commit_review_posttooluse` records findings into the shared `previous_findings` state keyed on `(filePath, category)` only, and suppression of "already surfaced" findings is delegated entirely to the LLM prompt rather than enforced by deterministic code. An attacker who controls commit diff content (any normal edit/commit) can introduce a brand-new dangerous line in a file/category pair that was previously flagged, and the review pipeline has no code-level check confirming the new vulnerable code differs from what was already shown — it relies on the model correctly applying a soft instruction.

### Finding Description
When a commit is reviewed, `previous_findings` is loaded and passed to `analyze_code_security` as `prev_section` in `llm.py`: [1](#0-0) 
This text instructs the LLM: "DO NOT report any finding whose (filePath, category) pair matches an entry below — it was already handled... ONLY re-flag a (filePath, category) from this list if the code at that location was CHANGED... and introduces a new issue." Enforcement of this rule is not done deterministically in code — `_dedup_against_state` only strips vulns that a *concurrent* hook wrote to state during the LLM call (the race window), not vulns matching pre-existing `previous_findings` entries: [2](#0-1) 
The recording step in `handle_commit_review_posttooluse` then persists new findings keyed only on `(filePath, category)`: [3](#0-2) 
Because dedup granularity is `(filePath, category)` and the decision to re-flag a repeat pair is purely a soft LLM judgment call (not a deterministic content diff), an attacker who commits a first, low-severity issue in a file/category (e.g. "Command Injection" in `app.py`), gets it surfaced and recorded, then in a later commit introduces a second, unrelated, more dangerous `Command Injection` sink in the same file, can rely on the LLM treating the pair as "already handled" per the prompt's literal instruction, since the instruction's compliance is not independently verified by code. The same coarse key is reused by the Stop hook's `_record_fire` (`security_reminder_hook.py:1896-1916`) and push-sweep's `_record` (`security_reminder_hook.py:1671-1681`), so the bypass persists across retries, amends, and pushes as described in the invariant — no surface re-checks that the "already handled" code is actually the same code.

### Impact Explanation
A genuinely new, dangerous piece of code (e.g. a new command-injection or SSRF sink) can be silently committed without the security-guidance hook ever surfacing or blocking it via `exit(2)`/stderr rewake, because its `(filePath, category)` key collides with an old, already-dismissed finding. This breaks the stated invariant that "dangerous edits and commands must stay reviewable and blockable even across retries, amends, and pushes," and can lead to sensitive code/secrets/diff content being committed and pushed without the intended re-review gate, i.e. disclosure or persistence of a vulnerability to whatever sink the commit/push targets.

### Likelihood Explanation
Requires only normal repository activity by an unprivileged actor: two commits to the same file where the second introduces a different vulnerable line of the same LLM-assigned `category` as a previously-surfaced finding. No privilege escalation, no leaked keys — just ordinary Edit/Bash `git commit` flows already reachable via `PostToolUse commit review`. The main uncertainty is that final suppression depends on LLM classification (assigning the same `category` label and choosing to honor the "already handled" instruction rather than the "introduces a new issue" exception), so it is probabilistic rather than deterministic, but the code path provides no independent backstop when the LLM complies with the coarse instruction.

### Recommendation
Do not rely solely on the LLM's compliance with the `prev_section` prompt for enforcement. Add a deterministic layer: hash/fingerprint the actual vulnerable line(s) (not full diff-drifted `vulnerableCode`, but a normalized signature) per `(filePath, category)` bucket, and only suppress a new finding when its normalized code signature matches a previously recorded one for that key; otherwise always surface it regardless of what the LLM decides. Alternatively, key `previous_findings` on `(filePath, category, normalized_line_hash)` so multiple distinct vulnerable lines of the same category in the same file are tracked independently.

### Proof of Concept
1. Unit test `handle_commit_review_posttooluse` (or `_dedup_against_state`/`analyze_code_security` integration) with a mocked `previous_findings` state pre-seeded with `{"filePath": "app.py", "category": "Command Injection", "vulnerableCode": "os.system(f'echo {a}')"}`.
2. Simulate a second commit whose diff introduces a *different*, unrelated command-injection sink in `app.py` (e.g. `subprocess.call(cmd, shell=True)` sourced from network input), and stub the LLM response to omit it (as the prompt encourages when it perceives the `(filePath, category)` pair as "already handled").
3. Assert that `handle_commit_review_posttooluse` exits with code 0 (no warning, no rewake) instead of exit code 2 with the new finding, confirming the dangerous new line was never surfaced.
4. Expected (fixed) behavior: assert the hook always surfaces distinct vulnerable code locations of the same category in the same file, i.e., exits 2 with guidance mentioning the new sink, regardless of category-label collision with a prior, already-fixed finding.

### Citations

**File:** plugins/security-guidance/hooks/llm.py (L685-707)
```python
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

**File:** plugins/security-guidance/hooks/llm.py (L773-781)
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
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1334-1356)
```python
    # Record new findings into shared state. Key on (filePath, category) —
    # vulnerableCode bytes drift between fires (diff context lines shift) so
    # matching on it under-dedupes; this aligns with Stop's _record_fire.
    finding_snapshots = [
        {
            "filePath": v.get("filePath", ""),
            "category": v.get("category", "Unknown"),
            "vulnerableCode": v.get("vulnerableCode", ""),
        }
        for v in new_vulns
    ]

    def _record_findings(state):
        existing = [f for f in state.get("previous_findings", []) if isinstance(f, dict)]
        seen = {(f.get("filePath", ""), f.get("category", "")) for f in existing}
        for f in finding_snapshots:
            key = (f["filePath"], f["category"])
            if key not in seen:
                seen.add(key)
                existing.append(f)
        state["previous_findings"] = existing
        state["previous_findings_ts"] = _time.time()
    with_locked_state(session_id, _record_findings)
```
