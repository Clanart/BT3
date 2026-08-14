### Title
Byte-budget diff truncation in `cap_diff_for_prompt` lets an attacker push malicious `+` lines past the review window with no alert - ([File: plugins/security-guidance/hooks/review_api.py])

### Summary
`cap_diff_for_prompt` silently truncates any file diff beyond `DIFF_PER_FILE_BYTES` (80000) and the whole diff beyond the cumulative `DIFF_TOTAL_BYTES` (400000), replacing the tail with a static marker string [1](#0-0) . An attacker who controls file content (e.g. a large benign prefix or padding earlier files in `touched_paths`) can guarantee that injected dangerous code (`os.system(...)`, etc.) lands past the cap and is never included in the prompt built by `build_investigate_prompt`, which discards the `dropped` byte count entirely [2](#0-1) .

### Finding Description
The pipeline `handle_commit_review_posttooluse -> get_git_diff -> parse_diff_into_files -> cap_diff_for_prompt -> build_investigate_prompt` builds the diff the LLM reviewer sees. `get_git_diff` and `parse_diff_into_files` produce full unified diffs per touched file [3](#0-2) . `cap_diff_for_prompt` then deterministically truncates: any single file's diff content beyond byte 80000 is cut and replaced with a `"... [truncated ...]"` marker, and once the running `total` across files reaches 400000 bytes, subsequent files are either truncated or fully replaced with `"[omitted by security-guidance: total diff byte cap reached]"` [1](#0-0) . `build_investigate_prompt` calls `cap_diff_for_prompt`, discards the returned `dropped` count with `_`, and sends only the capped text to the model — the caller has no way to know content was silently dropped [4](#0-3) . Because both the per-file and cumulative thresholds are fixed, public constants (`DIFF_PER_FILE_BYTES=80000`, `DIFF_TOTAL_BYTES=400000`), an attacker who fully controls the content of a committed file (or the order/size of files touched in the same commit) can trivially compute an offset ≥80001 (or push the cumulative total ≥400000 before their file) and place the malicious `+` line there. The marker text itself does not restate or preserve the omitted vulnerable lines, so the reviewer prompt never contains them.

### Impact Explanation
This breaks the "deny means deny" guarantee of the commit-review security gate: dangerous code that should trigger a Stop/finding is committed with the review returning a clean/incomplete result, because the LLM investigate stage in `AGENTIC_INVESTIGATE_SYSTEM` only ever sees the capped diff text passed into `build_investigate_prompt`. This is a genuine attacker vs. tool-capability-gate scenario ("the model is the attacker, the user is the victim" pattern the refute prompt explicitly calls out as never-refutable for hook capability gates) — the security review is the mitigating control, and its own truncation logic is the bypass. The scoped impact is: silent commit of attacker-planted dangerous code (e.g., `os.system`, hardcoded credential exfiltration, backdoor) with no Stop/commit-review finding raised, i.e., a false sense of security from an automated gate that appears to have run but structurally omitted the payload.

### Likelihood Explanation
Highly feasible and fully attacker-controlled: no privilege beyond writing ordinary file content in the repo is required. The thresholds are fixed integers read once from environment (defaulting to 80000/400000) [5](#0-4) , so an attacker can precisely compute the padding needed (a large benign prefix, e.g. comments/whitespace) to push the payload past byte 80000 in a single file, or arrange multiple large touched files ahead of the payload file so the cumulative 400000-byte budget is exhausted first. This is deterministic and repeatable across runs, not probabilistic.

### Recommendation
Do not silently truncate `+` (added) lines without surfacing incompleteness to the enforcement decision: either (a) fail closed and force a Stop/manual-review finding whenever `dropped > 0` instead of proceeding with a possibly-incomplete review, (b) prioritize/guarantee inclusion of all `+` lines (diff additions) within the byte budget before context/`-` lines, or (c) chunk oversized diffs into multiple LLM calls so no `+` line is ever dropped rather than truncating mid-file. At minimum, `build_investigate_prompt` should not discard the `dropped` count — it should propagate it to the caller so `handle_commit_review_posttooluse` can escalate (e.g., emit its own "diff too large to fully review" finding) rather than reporting a clean result.

### Proof of Concept
Unit test against `cap_diff_for_prompt` in `plugins/security-guidance/hooks/review_api.py`:
```python
def test_cap_diff_drops_late_payload():
    from review_api import cap_diff_for_prompt, DIFF_PER_FILE_BYTES

    benign_prefix = "+" + ("A" * (DIFF_PER_FILE_BYTES - 5)) + "\n"
    payload_line = "+os.system(attacker)\n"
    content = benign_prefix + payload_line

    capped, dropped = cap_diff_for_prompt([("evil.py", content)])

    capped_content = capped[0][1]
    assert "os.system(attacker)" not in capped_content
    assert "truncated by security-guidance" in capped_content
    assert dropped > 0
```
Expected: the assertions pass, proving `os.system(attacker)` is absent from the text that `build_investigate_prompt` sends to the LLM, confirming the truncation-based evasion.

### Citations

**File:** plugins/security-guidance/hooks/review_api.py (L27-28)
```python
DIFF_PER_FILE_BYTES = int(os.environ.get("DIFF_PER_FILE_BYTES", "80000"))
DIFF_TOTAL_BYTES = int(os.environ.get("DIFF_TOTAL_BYTES", "400000"))
```

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

**File:** plugins/security-guidance/hooks/gitutil.py (L615-654)
```python
def parse_diff_into_files(diff_output):
    """
    Parse unified diff output into a list of (file_path, diff_content) tuples.
    Only includes files with source code extensions.
    """
    if not diff_output or not diff_output.strip():
        return []

    files = []
    file_diffs = diff_output.split("diff --git ")

    for file_diff in file_diffs:
        if not file_diff.strip():
            continue

        # Extract filename from first line: "a/path/to/file b/path/to/file"
        lines = file_diff.split('\n')
        header_match = re.match(r'^a/(.+?) b/(.+)$', lines[0])
        if not header_match:
            continue

        file_path = header_match.group(2) or header_match.group(1) or ''

        # Filter to source code files only
        if not _is_reviewable_source(file_path):
            continue

        # Extract the diff content (from first @@ onwards)
        diff_lines = []
        in_hunks = False
        for line in lines[1:]:
            if line.startswith('@@'):
                in_hunks = True
            if in_hunks:
                diff_lines.append(line)

        if diff_lines:
            files.append((file_path, '\n'.join(diff_lines)))

    return files
```
