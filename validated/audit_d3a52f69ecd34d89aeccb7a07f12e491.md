### Title
Commit/push security-review dedup keyed on (filePath, category) lets a new dangerous vulnerability in an already-flagged file/category escape review and rewake - (File: `plugins/security-guidance/hooks/security_reminder_hook.py`)

### Summary
The commit-review and push-sweep telemetry/rewake pipeline deduplicates LLM-found vulnerabilities against `previous_findings` using the tuple `(filePath, category)` rather than the actual vulnerable code. Once any finding of a given category has been reported for a file, subsequent distinct, more severe vulnerabilities of the same category in the same file are silently treated as "already known" and suppressed — no `exit(2)`, no `rewakeSummary`, no reviewable stderr guidance is emitted, even though the new code is genuinely dangerous and unreviewed.

### Finding Description
`handle_commit_review_posttooluse` runs after every `git commit`, calls `analyze_code_security`/`agentic_review` on the commit diff to get `vulns`, then dedups them: [1](#0-0) 

The dedup call is `_dedup_against_state(session_id, vulns, prompted=_finding_keys(previous_findings))` and, per the code's own comment, keys are intentionally `(filePath, category)` rather than the vulnerable code itself: [2](#0-1) 

That recorded state (`previous_findings`) persists for up to `PREVIOUS_FINDINGS_TTL_SEC` (default 3600s) and is shared across the Stop hook, commit-review, and push-sweep surfaces: [3](#0-2) 

Exploit flow: an attacker-influenced session (via ordinary edits/commits whose diff content and framing the attacker controls) first introduces a low-severity, easily-flagged issue in file `X` that the LLM classifies under some `category` (e.g., "Injection", "Path Traversal"). This gets flagged once, recorded in `previous_findings` keyed as `(X, category)`. A subsequent commit/amend/push then introduces a genuinely dangerous, unrelated vulnerable line in the *same file* that the reviewer classifies under the *same category* string. Because dedup matches on `(filePath, category)` and not on the actual vulnerable snippet, `_dedup_against_state` drops it as already-seen, `new_vulns` becomes empty, and the handler exits cleanly with `vulns_found: 0`, `deduped: n_deduped` — no `exit(2)`, no `rewakeSummary`, no PROVENANCE_BANNER/guidance written to stderr: [4](#0-3) 

The push-sweep path (`handle_push_sweep_posttooluse`) reuses the same `previous_findings` dedup semantics and the same `.git/sg-reviewed-shas` prefix-advance logic, so the same category-collision blind spot propagates to `git push` review as well — meaning retries, amends, and pushes across the same file/category are not guaranteed to be re-surfaced even when the actual dangerous code differs each time.

### Impact Explanation
This breaks the stated invariant that dangerous edits and commands must stay reviewable and blockable across retries, amends, and pushes. A dangerous command-injection/path-traversal/secrets-handling change can be smuggled into a file that already has one reported finding of the same LLM-assigned category, silently skipping the exit(2) rewake that would otherwise force Claude to review/block the change before proceeding — enabling execution/commit/push of unreviewed dangerous code, i.e. unauthorized local command execution that bypasses the intended approval/review control.

### Likelihood Explanation
Reachable with only standard, unprivileged inputs: normal `Edit`/`Write` tool use plus `git commit`/`git push` Bash calls, which are exactly the operations this hook is designed to observe. The only precondition is getting two findings in the same file classified under the same `category` string by the LLM within the `previous_findings` TTL window (1 hour by default) — plausible given how few, broad categories most static/LLM security classifiers use (e.g. "Injection", "Path Traversal", "Insecure Deserialization"). No admin privilege, key leakage, or social engineering required — this is a logic flaw in the plugin's own dedup key design, explicitly acknowledged as a tradeoff in the code comment.

### Recommendation
Change the dedup key to include a content-derived discriminator (e.g., a normalized hash of `vulnerableCode`, or a fuzzy/line-range diff against the previously recorded snippet for the same `(filePath, category)`) so a new, distinct vulnerable code fragment of the same category is not conflated with a previously reported and possibly-fixed one. At minimum, only suppress a `(filePath, category)` match when the new finding's `vulnerableCode` significantly overlaps the previously recorded snapshot's `vulnerableCode`.

### Proof of Concept
Unit test targeting `security_reminder_hook.py`/`llm._dedup_against_state`:
1. Seed session state with `previous_findings = [{"filePath": "app.py", "category": "Injection", "vulnerableCode": "os.system(user_input)"}]` and a fresh `previous_findings_ts`.
2. Call `handle_commit_review_posttooluse` (or directly `_dedup_against_state`) with a new `vulns` list containing a distinct, more severe finding: `{"filePath": "app.py", "category": "Injection", "vulnerableCode": "subprocess.run(shlex_unsafe_cmd, shell=True)"}`.
3. Assert (current buggy behavior) that `new_vulns` is empty and `emit_metrics` is called with `vulns_found: 0` and no `sys.exit(2)`/no `rewakeSummary` — demonstrating the second dangerous line is never surfaced.
4. Expected (fixed) behavior: `new_vulns` should contain the second finding, `emit_metrics` should include `vulns_found: 1`, and the handler should `sys.exit(2)` with guidance referencing the new `vulnerableCode`.

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1319-1332)
```python
    # Late dedup: drop only what a concurrent Stop hook wrote while our LLM
    # ran. Anything in `previous_findings` (the pre-LLM snapshot) that the
    # LLM chose to re-flag is an intentional "fix incomplete" verdict.
    new_vulns, n_deduped = _dedup_against_state(
        session_id, vulns, prompted=_finding_keys(previous_findings)
    )

    if not new_vulns:
        debug_log("Commit review: all findings already known, skipping")
        emit_metrics({
            "vulns_found": 0, **_base, **_agentic_m, "deduped": n_deduped,
            "files_reviewed": len(diff_files), "review_ms": review_ms,
        })
        sys.exit(0)
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

**File:** plugins/security-guidance/hooks/diffstate.py (L31-36)
```python
# previous_findings expires independently. Dedup is content-based ((filePath,
# vulnerableCode) — see _record_fire), so a longer TTL suppresses exact-repeat
# re-flags across turns without masking regressions that change the code. v2's
# git-derived review set can re-surface the same uncommitted file across turns;
# 120s could let warnings pile up over a long session.
PREVIOUS_FINDINGS_TTL_SEC = int(os.environ.get("PREVIOUS_FINDINGS_TTL_SEC", "3600"))
```
