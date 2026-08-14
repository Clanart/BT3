### Title
Coarse (filePath, category) matching in `prev_section` allows suppression of unrelated same-category findings - ([File: plugins/security-guidance/hooks/llm.py])

### Finding Description
`analyze_code_security` builds the `prev_section` prompt block directly from `previous_findings` entries and instructs the reviewer model to suppress any finding whose `(filePath, category)` pair matches an entry already surfaced: [1](#0-0) 

The instruction explicitly tells the model to match on file+category rather than exact code (`"match on file + category, not exact code bytes"`), and only re-flag if the code at that location changed and the fix is incomplete. This is a soft, model-interpreted rule — nothing in code enforces that the *same* vulnerability instance is being referenced. The companion state-level dedup helper (`_finding_keys` / `_dedup_against_state`) uses the identical coarse key: [2](#0-1) 

If a file accumulates a `previous_findings` entry for `(filePath=X, category=SQLi)` — whether from a real earlier finding or a model false positive on attacker-authored code in file `X` — any later, *distinct* SQL-injection vulnerability introduced elsewhere in the same file `X` shares the same `(filePath, category)` key. Because the prompt frames the whole file+category pair as "already handled," the model can (and per the instruction's phrasing, is encouraged to) suppress the new, unrelated vulnerability unless it independently judges the code as "changed" and still vulnerable — a judgment call, not a hard invariant.

### Impact Explanation
This is a security-review evasion / false-negative issue, not a direct RCE or auth bypass in Claude Code itself. Its practical effect is that a genuine, exploitable vulnerability in a shared file can fail to surface to the developer if an earlier, unrelated (or spoofed/false-positive) finding of the same category was recorded for that file. This weakens the security-guidance plugin's core guarantee (findings from this turn are surfaced), but it doesn't grant workspace escape, credential disclosure, or arbitrary code/command execution on its own.

### Likelihood Explanation
Exploitability depends on the model's own judgment, not a code-level suppression bug: the LLM is told to re-flag when "the change is an incomplete fix or introduces a new issue," so an attentive review can still catch the new vulnerability. There is no mechanism by which an attacker can write arbitrary JSON directly into `previous_findings`; the field is populated from genuine LLM analysis output of real files, so triggering the collision requires either (a) causing a benign/duplicate finding to be recorded for a shared file+category earlier in the session, or (b) getting the model to misjudge "already handled" scope. This makes exploitation probabilistic and dependent on LLM behavior rather than deterministic.

### Recommendation
Bind suppression to more than `(filePath, category)` — include a normalized/fuzzy match on `vulnerableCode` (e.g., line-range or snippet similarity) so the model, and any code-level dedup, cannot conflate two structurally different findings that merely share a file and category label. Tighten the prompt wording so `prev_section` is scoped per-finding (file+category+approximate location) and instruct the model to always re-scan the full file for other instances of the same category rather than treating the whole category as cleared for that file.

### Proof of Concept
Fuzz/invariant test plan (illustrative, not run against the live model — this requires mocking `_call_claude_dual_or` to simulate a compliant model, since the actual suppression decision is model-side):
1. Seed `previous_findings` with `[{"filePath": "app/db.py", "category": "SQL Injection", "vulnerableCode": "cursor.execute(f\"...{user}\")"}]`.
2. Call `analyze_code_security(files=[("app/db.py", <content with a DIFFERENT, unrelated SQL-injection line at a different function>)], previous_findings=previous_findings)`.
3. Assert the returned `vulns` list still contains a finding for `app/db.py`/`SQL Injection` referencing the new vulnerable line — i.e., that suppression logic (or the prompt's guidance) does not cause it to be silently dropped.
4. This test would currently rely entirely on model compliance with the "changed/incomplete fix" instruction rather than a code-enforced check, demonstrating the lack of a hard invariant at the `_finding_keys`/`prev_section` layer.

### Citations

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
