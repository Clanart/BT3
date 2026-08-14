### Title
Order-dependent per-file/total diff truncation in `cap_diff_for_prompt` can silently drop the security-relevant hunk of an attacker-authored file from the review prompt - (File: `plugins/security-guidance/hooks/review_api.py`)

### Summary
`cap_diff_for_prompt` truncates each file's diff content from the tail once it exceeds `DIFF_PER_FILE_BYTES` (80,000 bytes) and truncates/omits whole files once the running total exceeds `DIFF_TOTAL_BYTES` (400,000 bytes), processing files in whatever order `diff_files` is handed in. [1](#0-0) . Because truncation is purely byte-position/tail-based and not risk-aware, an attacker who authors the diff content (e.g., padding a file with large benign hunks before the actually dangerous lines, or padding earlier files in the change set) can push the sensitive lines past the cutoff, causing them to be replaced with a generic "truncated"/"omitted by security-guidance" marker rather than being shown to the review model.

### Finding Description
`build_investigate_prompt` calls `cap_diff_for_prompt(diff_files)` and feeds the capped output directly into the investigate-stage prompt sent to the review model [2](#0-1) . The capping logic has two truncation points that are both position-dependent rather than content-aware:

1. Per-file: `content[:DIFF_PER_FILE_BYTES]` keeps only the first 80KB of a file's diff and drops everything after it. [3](#0-2) 
2. Per-total: once the running `total` exceeds `DIFF_TOTAL_BYTES`, subsequent files in the loop are either truncated or fully replaced with `"[omitted by security-guidance: total diff byte cap reached]"`. [4](#0-3) 

Since diff hunk ordering within a file (and file ordering within the review set) is determined by normal git diff output — content the change author (i.e., the attacker/PR submitter, or Claude acting on attacker-influenced instructions) controls — an attacker can front-load a file with large innocuous diff noise (e.g., a big reformat, generated data, or verbose comment block) so the actually dangerous hunk lands past byte 80,000, or can add several large unrelated files ahead of the target file in the commit so the total-byte cap is reached before the target file is processed. The result is a marker string substituted for the real diff content, and the review model never sees the dangerous lines — it only reviews the truncation notice.

Whether this rises to the described "prompt injection to skip dangerous behavior" framing depends on the caller context, which I could not fully trace: `build_investigate_prompt` is used by `security_reminder_hook.py`'s agentic reviewer, and file ordering there is derived from `compute_v2_review_set`, which returns paths `sorted(review_set)` (alphabetical) [5](#0-4) . Alphabetical ordering means an attacker can influence which files sort first (via filename choice) to push a target file past the total cap, but I was unable to fully confirm from the indexed content how `diff_files` (content per file, not just paths) is assembled before being passed to `cap_diff_for_prompt`, nor whether there is any downstream safeguard (e.g., a check that flags truncated files as "unreviewed" rather than silently treating them as reviewed). This gap in the indexed code means I cannot confirm whether the omission is surfaced to the user/decision logic or is entirely silent to the invariant enforcement.

### Impact Explanation
If confirmed, the impact is that a maliciously crafted diff (large benign padding plus a small dangerous change) could cause the security-guidance review to approve/pass a commit or push containing a real vulnerability, because the vulnerable lines are replaced by a generic truncation marker before reaching the review LLM. This matches "review/export logic" bypass in the sense that the security control's core function — flagging dangerous diffs — is defeated by input size manipulation, which is a logic gap in the truncation strategy rather than a classic sink. It does not by itself grant unauthorized command execution or workspace escape; its impact is limited to reduced/false-negative security review coverage.

### Likelihood Explanation
Feasibility depends on attacker ability to control both diff size/ordering and diff content within a single review pass, which requires the attacker to already be able to introduce a commit/diff into the reviewed change set (e.g., a malicious contributor or a compromised/adversarial automation flow feeding diffs to this API). The 80KB per-file and 400KB total thresholds are large but not adversarially hardened, and padding a diff to exceed them is trivial (e.g., a vendored/minified file, generated code, or a large comment block). I could not verify from the available index whether upstream callers apply any additional size-based rejection, flagging, or reviewer alert when truncation occurs, which affects the true likelihood of this reaching an exploitable end-to-end outcome.

### Recommendation
Make truncation risk-aware and observable: (1) prioritize keeping high-signal diff regions (e.g., hunks touching security-sensitive sinks/patterns, or hunks near the end of large files) rather than always taking the head of the byte stream; (2) when any file's diff is truncated or omitted due to caps, propagate an explicit "INCOMPLETE REVIEW — file X truncated/omitted" signal into the findings/format output (not just inline in the prompt) so downstream logic can treat that file as unreviewed rather than implicitly cleared; (3) consider truncating from a fixed-size sliding window around each diff hunk rather than a single head-truncation, so no single contiguous run of hunks is guaranteed lost regardless of position.

### Proof of Concept
Unit test plan for `cap_diff_for_prompt` in `plugins/security-guidance/hooks/review_api.py`:
1. Build a synthetic `diff_files` list where file `"vuln.py"` has content = `"+" * 79_900 + "\n+os.system(user_input)\n"` (dangerous line placed just past the 80,000-byte per-file cap).
2. Call `cap_diff_for_prompt([("vuln.py", content)])` and assert that the returned content for `"vuln.py"` does NOT contain `"os.system(user_input)"` — demonstrating the dangerous line is silently dropped and replaced by the truncation marker.
3. Build a second synthetic set with several large benign files (each near `DIFF_PER_FILE_BYTES`) placed before a small file `"target.py"` containing a dangerous one-line change, sized so cumulative bytes exceed `DIFF_TOTAL_BYTES` before `"target.py"` is reached; assert `cap_diff_for_prompt` returns `"[omitted by security-guidance: total diff byte cap reached]"` for `"target.py"` instead of its real diff content.
4. Feed the capped output through `build_investigate_prompt` and assert the dangerous line/path is absent from the resulting prompt string, confirming the review model never receives it.

### Citations

**File:** plugins/security-guidance/hooks/review_api.py (L42-64)
```python
    for fp, content in files:
        if len(content) > DIFF_PER_FILE_BYTES:
            dropped += len(content) - DIFF_PER_FILE_BYTES
            content = (
                content[:DIFF_PER_FILE_BYTES]
                + "\n... [truncated by security-guidance: file exceeds per-file byte cap]"
            )
        room = DIFF_TOTAL_BYTES - total
        if room <= 0:
            dropped += len(content)
            out.append(
                (fp, "[omitted by security-guidance: total diff byte cap reached]")
            )
            continue
        if len(content) > room:
            dropped += len(content) - room
            content = (
                content[:room]
                + "\n... [truncated by security-guidance: total diff byte cap reached]"
            )
        total += len(content)
        out.append((fp, content))
    return out, dropped
```

**File:** plugins/security-guidance/hooks/review_api.py (L156-176)
```python
def build_investigate_prompt(
    touched_paths: list[str],
    diff_files: list[tuple[str, str]],
    *,
    context_note: str = "",
) -> str:
    capped, _ = cap_diff_for_prompt(diff_files)
    diff_text = "\n\n".join(
        f"=== DIFF: {fp} ===\n{content}" for fp, content in capped
    )
    return (
        "Review this change for security vulnerabilities.\n\n"
        "Changed files (you may Read these and any other file in the repo):\n"
        + "\n".join(f"  - {p}" for p in touched_paths[:50])
        + context_note
        + "\n\nUnified diff (only + lines are new):\n\n"
        + diff_text
        + extensibility.guidance_block()
        + "\n\nInvestigate per the method in your instructions, then return "
        "the findings list."
    )
```

**File:** plugins/security-guidance/hooks/diffstate.py (L428-428)
```python
    review_paths = [os.path.join(repo, p) for p in sorted(review_set)]
```
