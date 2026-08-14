### Title
Amend delta-review path marks the entire post-amend commit as reviewed after only reviewing the amend's diff, permanently hiding unreviewed pre-amend content from push-sweep - (File: `plugins/security-guidance/hooks/security_reminder_hook.py`)

### Summary
`_resolve_amend_pre_sha` lets `handle_commit_review_posttooluse` review only the delta between the pre-amend commit and the post-amend commit on a `git commit --amend`, based purely on reflog subject-line heuristics with no check that the pre-amend content was ever actually reviewed. After this delta-only review, the code records the full post-amend SHA into `.git/sg-reviewed-shas`, which causes push-sweep (the primary net that catches skipped/never-reviewed commits) to treat the whole amended commit — including any dangerous content squashed in from the unreviewed pre-amend state — as already reviewed.

### Finding Description
`_resolve_amend_pre_sha` (`plugins/security-guidance/hooks/security_reminder_hook.py:517-573`) infers a "safe" pre-amend baseline purely from `git log -g -2 --format=%H|%gs HEAD` reflog subjects (`commit (amend)` pattern) plus a prefix match against the caller-supplied post-amend SHA. It has no way to verify, and does not check, whether HEAD@{1} (the pre-amend commit) was itself ever pushed through this hook's LLM review and recorded in the `sg-reviewed-shas` state.

In `handle_commit_review_posttooluse`, when `is_amend` is true and the fast-path SHA (not the reflog-fallback path) is used, `pre_amend_sha = _resolve_amend_pre_sha(...)` is computed and the review is narrowed to `git diff pre_amend_sha sha` instead of `git show sha` (`security_reminder_hook.py:1100-1132`). The comment at `security_reminder_hook.py:1064-1070` explicitly assumes "Pre-amend SHA comes from the reflog ... otherwise we fall back to full-commit review," but the assumption that the pre-amend content "already reviewed on the original commit" is never validated against the actual reviewed-SHA ledger.

Many legitimate, attacker-reachable code paths cause the original (pre-amend) commit's full review to be skipped entirely: `skip_reason` 21 (commit not detected/interrupted), 22 (LLM review disabled or missing API credentials), 24 (`api.anthropic.com` unreachable), 25/26 (no cwd / not a git repo), 28 (SHA not resolved in cwd repo), or 32 (`COMMIT_REVIEW_ENABLED=0`), all seen around `security_reminder_hook.py:991-1023`. None of these skip paths call `_append_reviewed_shas`, so the original commit's content is never marked reviewed.

If the user (or an agent acting on attacker-crafted instructions embedded in ordinary repo/file content) subsequently runs `git commit --amend` with a trivial, unrelated change (e.g., a typo fix), `_resolve_amend_pre_sha` will happily resolve HEAD@{1} as the "already reviewed" baseline and only the tiny amend delta is sent to the LLM. After the review completes, `_append_reviewed_shas(repo_root, full_shas, ...)` at `security_reminder_hook.py:1254-1263` records the **full post-amend SHA** — which contains all of the original unreviewed dangerous content plus the trivial delta — into `.git/sg-reviewed-shas`. From that point forward, push-sweep's range-advancing logic (`security_reminder_hook.py:1570-1623`, `_append_reviewed_shas(repo_root, tail, ...)`) will treat this commit as already covered and never diff it again, and the Stop hook's baseline-diff mechanism only looks forward from the turn's baseline, not backward into already-committed history. The net effect: the dangerous content is now permanently exempt from LLM review across Stop, commit-review, and push-sweep, despite never actually having been analyzed.

### Impact Explanation
This is a security-control bypass: dangerous code introduced by an ordinary edit/commit sequence can be committed once under any of the several documented skip conditions, then "laundered" through a single trivial `--amend`, causing the code-review ledger to record the entire commit (including the never-reviewed dangerous portion) as reviewed. This breaks the stated invariant that "dangerous edits and commands must stay reviewable and blockable even across retries, amends, and pushes," silently routing around the review/blocking boundary that `security-guidance` is meant to enforce, matching an Immunefi "security-control bypass / silently disables or routes around blocking, review" impact class.

### Likelihood Explanation
No privileged access is required — an unprivileged user/agent following normal or attacker-supplied prompts can trigger this with ordinary `git commit` + `git commit --amend` sequences. Several of the required skip conditions (transient network unreachability to `api.anthropic.com`, missing API credentials at commit time, `COMMIT_REVIEW_ENABLED=0`/env misconfig, or output redirection that defeats the reflog-freshness window) are common in real developer/agent workflows, not contrived edge cases, making this readily reproducible. The delta-review fast path itself requires only a single, non-chained `git commit --amend` invocation with visible `[branch sha]` output, which is the default/most common amend usage pattern.

### Recommendation
Before trusting the amend-delta path, `_resolve_amend_pre_sha` (or its caller) should verify that the pre-amend SHA is present in the `sg-reviewed-shas` ledger (i.e., was actually reviewed previously). If it is not present, fall back to a full `git show` review of the post-amend commit instead of a delta-only diff, and only mark the post-amend SHA as reviewed once the entirety of its content (not just the delta) has actually been analyzed or is provably a superset of already-reviewed content.

### Proof of Concept
Integration test plan (extending the existing hook test harness used for `handle_commit_review_posttooluse`):
1. Force a skip on the first commit: set `ENABLE_CODE_SECURITY_REVIEW=0` (or mock `HAS_API_CREDENTIALS=False`), then simulate a Bash `PostToolUse` event for `git commit -m "wip" ` that introduces a clearly dangerous pattern (e.g., `eval(request.args['cmd'])`). Assert `emit_metrics` records `skip_reason` in {21,22,24,25,26,28,32} and `.git/sg-reviewed-shas` does NOT contain the wip commit's SHA.
2. Re-enable review normally, then simulate a second Bash `PostToolUse` event for `git commit --amend --no-edit -m "fix typo"` that only changes an unrelated comment line.
3. Assert: (a) `_resolve_amend_pre_sha` resolves and `amend_delta_review=True` is set in metrics; (b) the LLM/security analyzer is invoked only with the trivial delta diff (not containing the `eval(...)` line); (c) after the call, `.git/sg-reviewed-shas` now contains the post-amend (full) SHA; (d) a subsequent simulated `git push` PostToolUse event (`handle_push_sweep_posttooluse`) does NOT re-surface the `eval(...)` finding because the commit range is already marked reviewed.
4. Expected (failing) assertion demonstrating the bug: the dangerous `eval(...)` code is never reported by any of Stop, commit-review, or push-sweep despite being present in the final `HEAD` tree — confirming permanent review bypass. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L517-573)
```python
def _resolve_amend_pre_sha(repo_root, expected_post_sha=None):
    """For a `git commit --amend` we just ran, return the pre-amend SHA via
    reflog, or None if it can't be safely determined.

    expected_post_sha: the post-amend SHA the caller parsed from bash stdout
    (or reflog). If provided, HEAD@{0} of `repo_root` must match it (prefix
    compare — bash stdout SHAs are abbreviated, reflog %H is 40 chars) before
    we trust the reflog-derived pre-amend SHA. This guards against the
    cross-repo case (`cd ../other && git commit --amend && cd -`) where
    `repo_root` happens to have its own recent amend that's unrelated to
    the bash command we're reviewing.

    We require HEAD@{0}'s reflog subject to start with `commit (amend)` —
    otherwise our `--amend` regex matched something that didn't actually
    perform an amend (e.g., `git commit --amend --dry-run`, aliased commands,
    aborted amends), and HEAD@{1} would be the wrong commit. Also requires
    HEAD@{1} to NOT itself be an amend, since back-to-back amends would have
    HEAD@{1} as the previous-amend's post state — the original commit we
    want to compare against is then HEAD@{2}, but at that point we're
    reaching and fall back to a full review.

    Bytes + decode('utf-8', errors='replace'): reflog subjects embed commit
    subjects, which git stores as raw bytes (commit messages may be latin-1
    / cp1252 / etc.). text=True would raise UnicodeDecodeError (a
    ValueError, not OSError) on non-UTF8 bytes and crash the hook.
    """
    if not repo_root:
        return None
    try:
        r = subprocess.run(
            [*GIT_CMD, "log", "-g", "-2", "--format=%H|%gs", "HEAD"],
            cwd=repo_root, capture_output=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if r.returncode != 0:
        return None
    stdout_text = r.stdout.decode("utf-8", errors="replace")
    lines = [ln for ln in stdout_text.splitlines() if "|" in ln]
    if len(lines) < 2:
        return None
    head0_sha, _, head0_subj = lines[0].partition("|")
    head1_sha, _, head1_subj = lines[1].partition("|")
    if not head0_subj.startswith("commit (amend)"):
        return None
    if head1_subj.startswith("commit (amend)"):
        return None
    # Cross-repo guard: the post-amend SHA the caller is about to review must
    # match HEAD@{0} of repo_root. Otherwise the bash command was likely run
    # in a different repo than repo_root, and the reflog we just read is
    # unrelated. Prefix-compare: expected_post_sha is typically the 7-char
    # abbreviated SHA captured from bash stdout by _COMMIT_SHA_RE (git's
    # default core.abbrev floor), while head0_sha is the full 40-char %H —
    # strict equality would always fail and silently disable the delta path.
    if expected_post_sha and not head0_sha.startswith(expected_post_sha):
        return None
    return head1_sha or None
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L991-1023)
```python
    if not commit_succeeded:
        debug_log("Commit review: commit did not succeed, skipping")
        emit_metrics({"skipped": True, "skip_reason": 21, **_base,
                      **({"skip_21_sub": 1} if interrupted
                         else {"skip_21_sub": _skip_21_sub} if _skip_21_sub
                         else {})})
        sys.exit(0)

    if not COMMIT_REVIEW_ENABLED:
        debug_log("Commit review: disabled, skipping")
        emit_metrics({"skipped": True, "skip_reason": 32, **_base})
        sys.exit(0)

    if not ENABLE_CODE_SECURITY_REVIEW or not HAS_API_CREDENTIALS:
        debug_log("Commit review: LLM review disabled or no API credentials")
        emit_metrics({"skipped": True, "skip_reason": 22, **_base})
        sys.exit(0)

    if not ensure_anthropic_reachable():
        debug_log("Commit review: api.anthropic.com unreachable")
        emit_metrics({"skipped": True, "skip_reason": 24, **_base})
        sys.exit(0)

    if not cwd:
        debug_log("Commit review: no cwd")
        emit_metrics({"skipped": True, "skip_reason": 25, **_base})
        sys.exit(0)

    repo_root = _git_toplevel(cwd)
    if not repo_root:
        debug_log("Commit review: not in a git repo")
        emit_metrics({"skipped": True, "skip_reason": 26, **_base})
        sys.exit(0)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1100-1132)
```python
    is_amend = bool(_GIT_AMEND_RE.search(command))
    commit_invocations = len(_GIT_COMMIT_RE.findall(command))
    pre_amend_sha = None
    if (is_amend and not _reflog_shas and len(all_shas) <= 1
            and commit_invocations <= 1):
        pre_amend_sha = _resolve_amend_pre_sha(repo_root, expected_post_sha=shas[0])
    if is_amend and pre_amend_sha:
        _base = {**_base, "amend_delta_review": True}
        debug_log(
            f"Commit review: --amend detected; reviewing delta "
            f"{pre_amend_sha[:12]}..{shas[-1][:12]}"
        )

    # --no-color: `color.ui=always` would emit ANSI escapes that corrupt
    # parse_diff_into_files' header match. Bytes + errors='replace': commits
    # can contain non-UTF8 source (latin-1, cp1252) and text=True would raise
    # UnicodeDecodeError outside the except clause.
    diff_files = []
    resolved = 0
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
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1248-1265)
```python
    # push-sweep state: record this commit as reviewed (full 40-hex sha) so a
    # later `git push` can advance its diff base past it. Recorded here — after
    # the review ran but before any exit path — so it's marked regardless of
    # whether findings were emitted. `shas` holds abbreviated refs from
    # `[branch sha]`; resolve to full so set-membership in the push-sweep is
    # exact. Best-effort; failures here never block the review result.
    try:
        full_shas = []
        for s in shas:
            r = subprocess.run(
                [*GIT_CMD, "rev-parse", "--verify", "-q", s],
                cwd=repo_root, capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                full_shas.append(r.stdout.strip())
        _append_reviewed_shas(repo_root, full_shas, vulns_found=len(vulns or []))
    except Exception:
        pass
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1570-1623)
```python
    # push-sweep ranges are net diffs over many commits so they hit the cap
    # more often; reviewing the riskiest MAX_PUSH_SWEEP_FILES is strictly
    # better than reviewing none. We still mark `tail` reviewed afterward —
    # the dropped files are by construction the low-risk ones (config, .gen,
    # tests, migrations), and NOT advancing the base would make the next
    # push re-hit the same overflow with an even larger range. Per-commit
    # review remains the primary surface for those files. The 10×
    # pathological guard stays so a 500-file vendored-dir push doesn't burn
    # a counter slot.
    if len(diff_files) > 10 * MAX_PUSH_SWEEP_FILES:
        emit_metrics({**_base, "pushed": len(push_range),
                      "unreviewed": len(tail), "skip_reason": 31,
                      "diff_files_count": len(diff_files)})
        sys.exit(0)
    diff_files, _dropped = _prioritize_diff_files(diff_files, MAX_PUSH_SWEEP_FILES)
    if _dropped:
        _base = {**_base, "diff_files_dropped": _dropped}

    _allowed, _rate_n = atomic_check_rate_limit(
        session_id, "PushSweep",
        MAX_COMMIT_REVIEWS_PER_HOUR, COMMIT_REVIEW_RATE_WINDOW_S)
    _base = {**_base, "rate_count": _rate_n}
    if not _allowed:
        emit_metrics({"skipped": True, "skip_reason": 23, **_base})
        sys.exit(0)

    import time as _time
    now = _time.time()
    previous_findings = with_locked_state(
        session_id,
        lambda s: list(s.get("previous_findings", []))
        if (now - s.get("previous_findings_ts", 0)) <= PREVIOUS_FINDINGS_TTL_SEC
        else []
    ) or []

    review_start = _time.time()
    rel_touched = [fp for fp, _ in diff_files]
    if _agentic_commit_review_enabled():
        concrete_guidance, vulns, agentic_metrics = _agentic_review_with_race(
            repo_root, diff_files, rel_touched, previous_findings
        )
        if agentic_metrics.get("agentic_fallback"):
            concrete_guidance, vulns = analyze_code_security(
                diff_files, is_diff=True, previous_findings=previous_findings
            )
    else:
        concrete_guidance, vulns = analyze_code_security(
            diff_files, is_diff=True, previous_findings=previous_findings
        )
        agentic_metrics = {}
    review_ms = int((_time.time() - review_start) * 1000)

    # The tail is now covered by this net-diff review.
    _append_reviewed_shas(repo_root, tail, vulns_found=len(vulns or []))
```
