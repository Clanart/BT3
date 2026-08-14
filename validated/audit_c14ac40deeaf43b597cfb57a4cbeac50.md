### Title
Unbounded per-commit diff size lets a malicious repo/commit exhaust CPU/memory in the security-review PostToolUse hook, defeating the security control - (File: plugins/security-guidance/hooks/security_reminder_hook.py)

### Summary
The Canto report describes a DoS where an attacker inflates the size of an array (`sdk.Coins` balances) that a downstream function must fully iterate/sort, exhausting gas and breaking pool operations. The closest reachable analog in this repo's actual code (as opposed to changelog-only entries) is the commit-review pipeline in the `security-guidance` plugin's `PostToolUse`/`Stop` hook, which unconditionally runs `git show`/`git diff` on attacker-influenced commit content and only caps the **file count**, not the size of any individual file/diff, before doing CPU-bound parsing and sorting work.

### Finding Description
`handle_commit_review_posttooluse` in `plugins/security-guidance/hooks/security_reminder_hook.py` resolves commit SHAs and, for each, shells out to `git show -p --no-color --no-ext-diff <sha> --` (or `git diff` for amends) with only a 15-second subprocess timeout [1](#0-0) . The full stdout is decoded and passed to `parse_diff_into_files`, accumulating an in-memory `diff_files` list [2](#0-1) .

Only after this full parse does the code apply any limiting logic, and that limiting logic is based on **file count**, not diff/file byte size:
- A "pathological" bail-out only triggers when `len(diff_files) > 10 * MAX_DIFF_FILES` (i.e., >300 files) [3](#0-2) .
- `_prioritize_diff_files` then sorts the (still fully materialized) file list by a per-file heuristic score computed by scanning `content.count("\n+")` over every file's full diff text [4](#0-3) .

This mirrors the Canto bug class precisely: the code guards against "too many discrete items" (analogous to `sdk.NewCoins()` accumulating many denoms) but does not bound the size of the underlying data blob that must be fully read, decoded, and iterated before the cap is even evaluated. A single attacker-controlled commit containing one enormous file (e.g., a huge generated/binary-as-text blob, or a file with an extremely long line) produces a diff that:
1. Is captured in full by `subprocess.run(..., capture_output=True)` with no output-size limit — only a wall-clock timeout.
2. Is decoded with `errors="replace"` over the whole byte stream.
3. Is fully scanned by `parse_diff_into_files` and then by `_prioritize_diff_files`'s `content.count("\n+")` heuristic — an O(n) scan per file, but with `n` (single-file diff size) unbounded.

Since this hook fires automatically after every `git commit` a session makes, a repository or task designed to make the agent commit attacker-supplied large files (e.g., a cloned malicious template repo, or a prompt-injected instruction to "commit this generated asset") can produce this pathological path without the file-count cap ever engaging, because it only counts files, not bytes.

### Impact Explanation
If the diff/content is large enough, this can cause:
- The 15-second subprocess timeout to be hit repeatedly, or the Python-side decode/scan to consume excessive CPU/memory in the async hook process.
- Because this is a security-relevant control (the LLM-backed vulnerability scanner that reviews every commit for injected security issues), degrading or crashing it means malicious/vulnerable code committed in the same or adjacent commits escapes review — a control-bypass outcome analogous to the coinswap pool becoming unusable due to gas exhaustion. The hook's own comments acknowledge this exact risk class ("Large commits ... used to bail here", "pathological case ... almost certainly a bad baseline") but the mitigation targeted file *count*, leaving file/diff *size* unbounded [3](#0-2) .
- Impact is bounded to the local, single-session commit-review control (not remote command execution, credential leak, or workspace escape), which caps the severity of this analog.

### Likelihood Explanation
Medium-low. It requires the agent (in an unprivileged, single-user local session) to actually run `git commit` on attacker-supplied oversized content — e.g., via a task that clones/uses a third-party repository containing a huge tracked file, or an instruction that causes a large file to be committed. This is plausible in agentic coding workflows (cloning templates, vendoring dependencies, committing generated artifacts) but requires a specific triggering scenario rather than being exploitable purely from an inbound message.

### Recommendation
- Enforce a byte-size cap on the captured diff/commit content *before* decoding and parsing (e.g., check `len(result.stdout)` immediately after `subprocess.run` and skip/truncate if it exceeds a threshold, mirroring `MAX_DIFF_FILES` but for total bytes).
- Bound `_prioritize_diff_files`'s per-file scan by truncating each file's diff content to a fixed byte length before computing the `added` heuristic, so a single oversized file cannot dominate parse time.
- Consider streaming/limiting `git show`/`git diff` output via `--stat`-first triage or a `ulimit`-style output cap, rather than relying solely on a post-hoc file-count check.

### Proof of Concept
Conceptual (not executed): in a repository processed by this plugin,
1. Create and commit a single file containing e.g. 500MB of `+`-prefixed-looking text (or one file with an extremely long single line) so that `len(diff_files) <= 10*MAX_DIFF_FILES` (well under 300 files) but the diff payload is very large.
2. Run `git commit` through the Bash tool so `PostToolUse` invokes `handle_commit_review_posttooluse`.
3. Observe that `git show -p ...` returns a very large stdout that is fully captured and decoded [5](#0-4) , and `_prioritize_diff_files`'s `content.count("\n+")` scan runs over the full content [6](#0-5) , causing excessive CPU/memory use in the hook process and potentially timing it out or exhausting resources — degrading/bypassing the security review for that and subsequent commits in the session.

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1119-1145)
```python
    for sha in shas:
        try:
            if pre_amend_sha:
                # Delta review: pre-amend → post-amend. `git diff` (not show)
                # so the output is a pure unified diff with no commit header.
                result = subprocess.run(
                    [*GIT_CMD, "diff", "--no-color", "--no-ext-diff", pre_amend_sha, sha, "--"],
                    cwd=repo_root, capture_output=True, timeout=15
                )
            else:
                result = subprocess.run(
                    [*GIT_CMD, "show", "-p", "--no-color", "--no-ext-diff", sha, "--"],
                    cwd=repo_root, capture_output=True, timeout=15
                )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            _cmd = "git diff" if pre_amend_sha else "git show"
            debug_log(f"Commit review: {_cmd} {sha} error: {e}")
            continue
        if result.returncode != 0:
            # SHA not in this repo (cross-repo commit) or already gc'd. Better
            # to skip than to fall back to HEAD and review the wrong commit.
            _cmd = "git diff" if pre_amend_sha else "git show"
            debug_log(f"Commit review: {_cmd} {sha} rc={result.returncode}")
            continue
        resolved += 1
        diff_files.extend(parse_diff_into_files(
            result.stdout.decode("utf-8", errors="replace")))
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1183-1196)
```python
    # Large commits (initial scaffolds, big refactors) used to bail here with
    # skip_reason=31. Large multi-file changes are exactly where
    # cross-file source→sink vulns hide. Reviewing nothing is
    # worse than reviewing the riskiest 30 — _cap_files_for_prompt already
    # bounds total bytes downstream so this can't blow context.
    # `diff_files_dropped` lets telemetry measure how often the prioritizer engages
    # and how much it drops; skip_reason=31 is now reserved for the truly
    # pathological case (e.g. >300 source files — almost certainly a bad
    # baseline, not a real commit).
    if len(diff_files) > 10 * MAX_DIFF_FILES:
        debug_log(f"Commit review: pathological diff ({len(diff_files)} files), skipping")
        emit_metrics({"skipped": True, "skip_reason": 31, **_base,
                      "diff_files_count": len(diff_files)})
        sys.exit(0)
```

**File:** plugins/security-guidance/hooks/gitutil.py (L512-547)
```python
def _prioritize_diff_files(diff_files, cap):
    """When `diff_files` exceeds `cap`, return the top-`cap` by security
    relevance plus the count dropped. Otherwise return (diff_files, 0).

    Score = (risk_tokens_in_path, not_low_priority, added_lines). The
    added-lines proxy is `content.count('\\n+')` which counts diff additions
    cheaply without re-parsing hunks. This is a heuristic, not a guarantee —
    the goal is to review the likely-dangerous subset of an over-cap diff
    instead of reviewing nothing. Diffs that exceed the cap are typically
    large multi-file scaffolds, and the cross-file source→sink vulnerabilities
    in them concentrate in a handful of api/client/route files.
    """
    if len(diff_files) <= cap:
        return diff_files, 0

    def _score(item):
        fp, content = item
        low = fp.lower()
        # Prepend "/" so leading-slash patterns in _LOW_PRIORITY_PATH_TOKENS
        # match top-level dirs (git diff paths are repo-root-relative, e.g.
        # `migrations/001.py` not `/migrations/001.py`). Same trick as
        # _is_reviewable_source.
        low_slashed = "/" + low
        risk = sum(1 for t in _SECURITY_RISK_PATH_TOKENS if t in low)
        low_prio = (
            fp.endswith(_LOW_PRIORITY_SUFFIXES)
            or any(t in low_slashed for t in _LOW_PRIORITY_PATH_TOKENS)
        )
        # added_lines: count('\n+') over-counts by including '+++' header and
        # any literal '+' at line start in context, but it's a consistent
        # ordinal across files in the same diff which is all we need.
        added = content.count("\n+")
        return (risk, not low_prio, added)

    ranked = sorted(diff_files, key=_score, reverse=True)
    return ranked[:cap], len(diff_files) - cap
```
