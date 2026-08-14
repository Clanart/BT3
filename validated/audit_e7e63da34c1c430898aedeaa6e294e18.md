### Title
Session-scoped (not repo-scoped) `previous_findings` dedup key lets a dangerous finding be silently suppressed across repos/worktrees in `handle_push_sweep_posttooluse` - (File: `plugins/security-guidance/hooks/security_reminder_hook.py`)

### Summary
`handle_push_sweep_posttooluse` (and its sibling `handle_commit_review_posttooluse`) record and dedup findings using the key `(filePath, category)` against `state["previous_findings"]`, where state is looked up solely by `session_id` via `with_locked_state`/`get_state_file`. Nothing in the key or the state-file path incorporates the repository root, so if the same Claude Code session operates across more than one repository/worktree (a legitimate flow — e.g. a monorepo with submodules, a session that `cd`s between two cloned repos, or resumes via `CLAUDE_CODE_REMOTE_SESSION_ID` across processes), a genuinely new and dangerous vulnerability in repo B can be silently deduped and never surfaced because an unrelated, already-reported finding in repo A happened to share the same relative file path and vulnerability category.

### Finding Description
`handle_push_sweep_posttooluse` computes `new_vulns, n_deduped = _dedup_against_state(session_id, vulns or [], prompted=_finding_keys(previous_findings))` [1](#0-0) , and `_dedup_against_state`/`_finding_keys` build/compare sets purely from `(filePath, category)` tuples read from `state.get("previous_findings", [])` [2](#0-1) . The `_record` closure that writes accepted findings back into shared state also keys only on `(filePath, category)`, with no repo identifier in the tuple or in the snapshot dict (`filePath`, `category`, `vulnerableCode`) [3](#0-2) .

That state is fetched/persisted purely by `session_id` through `get_state_file`/`get_lock_file`, which derive the on-disk filename from `_state_key(session_id)` — and `_state_key` explicitly prefers `CLAUDE_CODE_REMOTE_SESSION_ID` over the passed `session_id` specifically so state "survives" process restarts across turns [4](#0-3) . Nowhere in `handle_push_sweep_posttooluse`, `handle_commit_review_posttooluse`, or the `_record`/`_dedup_against_state` path is `repo_root`/`cwd` folded into the dedup key or the state-file key — in contrast to `.git/sg-reviewed-shas`, which is correctly scoped per-repo via `repo_root` [5](#0-4) .

Exploit flow: within one Claude Code session (same `session_id`/`CLAUDE_CODE_REMOTE_SESSION_ID`), the agent operates on two different git repositories/worktrees that share a relative path convention (common for monorepos, template-derived services, or an attacker who structures a second repo to mirror common paths like `src/auth.py`). A first push/commit in repo A triggers a review that finds and reports a vulnerability at `(filePath="src/auth.py", category="Authorization")`; this gets written into the session's `previous_findings`. Later, a real dangerous change is pushed in repo B at the same relative path and category (attacker fully controls diff content of ordinary edits/commits, satisfying the stated attacker model). `_dedup_against_state`'s `race_delta` check only removes vulns that raced in *during* this LLM call, but the *initial* `previous_findings` snapshot fed as `prompted`/context to the LLM already contains repo A's entry — and the LLM's `prev_section` instructs it to only re-flag entries in `previous_findings` when the "fix" is incomplete, otherwise treating them as already-known and not raising them again. Since the model has no reliable signal to distinguish "same path in a different repo" from "same path, already-fixed in this repo", the dangerous repo-B finding can be omitted from `concrete_guidance`/`vulns` entirely, and even if reported, an identical-shaped subsequent finding at the same path/category is deduped by the `(filePath, category)` set-membership check with no repo qualifier.

### Impact Explanation
This breaks the stated invariant that "dangerous edits and commands must stay reviewable and blockable even across retries, amends, and pushes." A cross-repo (or cross-worktree) dedup collision means `handle_push_sweep_posttooluse` can emit no warning and no `asyncRewake`/exit-2 stderr guidance for a real vulnerability in a target repo, purely because an unrelated earlier finding in a different repo shared the same relative path and category string. This matches "Cross-repo, cross-session … mutation with real security impact" — a dangerous, exploitable code change (e.g., SQL injection, missing authorization check) is pushed and never surfaced to the reviewing agent/user because the session's shared, repo-agnostic `previous_findings` state falsely treats it as already-known.

### Likelihood Explanation
Requires: (1) a single Claude Code session that touches more than one git repository/worktree — a common, non-privileged workflow (monorepos with nested `.git` dirs, multi-repo automation scripts, or sessions resumed across processes sharing `CLAUDE_CODE_REMOTE_SESSION_ID`); and (2) two findings — one benign/earlier, one dangerous/later — that happen to share `(filePath, category)`. An attacker who controls repo content (the stated threat model) can deliberately engineer this collision by naming files/vulnerability shapes to match common categories (`Authorization`, `Command Injection`, etc.) at predictable relative paths. This is fully repeatable and does not depend on any race condition — it is a deterministic consequence of the session-scoped, repo-agnostic key design.

### Recommendation
Include the repository identity (e.g., `repo_root` or its resolved absolute path / a hash of it) as part of the `previous_findings` dedup key and as part of the `_finding_keys`/`_dedup_against_state` tuple, so findings are never deduped across different repositories or worktrees. Alternatively, scope the `previous_findings` (and related warning/counter) state per-repo rather than purely per-session, mirroring how `.git/sg-reviewed-shas` is already correctly scoped to `repo_root`.

### Proof of Concept
Integration test plan:
1. Create two temp git repos, `repoA` and `repoB`, each containing a file at the same relative path, e.g. `src/auth.py`.
2. Using one fixed `session_id`, invoke the equivalent of `handle_push_sweep_posttooluse` (or directly call `with_locked_state`/`_record`-style write) against `repoA` such that a finding `{"filePath": "src/auth.py", "category": "Authorization"}` is written into `previous_findings` for that `session_id`.
3. With the same `session_id`, simulate a push in `repoB` that introduces a genuinely dangerous, distinct vulnerability also at `{"filePath": "src/auth.py", "category": "Authorization"}` (different `vulnerableCode`, different repo).
4. Call `_dedup_against_state(session_id, vulns_from_repoB, prompted=set())` and assert that the repoB finding is incorrectly dropped (empty `new_vulns`), or, without even needing the race-delta path, assert that the LLM-prompt-construction step (`prev_section`) for repoB includes a `previous_findings` entry sourced from `repoA`, demonstrating cross-repo leakage of the "already known" fingerprint.
5. Expected (buggy) result: repoB's dangerous finding is either never surfaced (LLM treats it as previously-known due to the polluted `prev_section`) or filtered out by `_dedup_against_state`, with no warning emitted (`sys.exit(0)` path instead of `sys.exit(2)` with guidance) — confirming the invariant violation.

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1521-1567)
```python
    prev_upstream = _detect_prev_upstream(repo_root, bash_output)
    if not prev_upstream:
        debug_log("Push sweep: could not determine prev_upstream")
        emit_metrics({"skipped": True, "skip_reason": 41, **_base})
        sys.exit(0)

    push_range = _git_rev_list_range(repo_root, prev_upstream, "HEAD")
    if not push_range:
        emit_metrics({"skipped": True, "skip_reason": 42, **_base, "pushed": 0})
        sys.exit(0)
    if len(push_range) > MAX_PUSH_SWEEP_RANGE:
        # Huge first-push of a long-lived branch — Stop hook is the backstop.
        emit_metrics({"skipped": True, "skip_reason": 43, **_base,
                      "pushed": len(push_range)})
        sys.exit(0)

    reviewed = _load_reviewed_shas(repo_root)
    base, tail = _compute_push_sweep_base(prev_upstream, push_range, reviewed)
    prefix_advanced = len(push_range) - len(tail)
    if base is None:
        debug_log("Push sweep: every pushed commit already reviewed")
        emit_metrics({**_base, "pushed": len(push_range), "unreviewed": 0,
                      "prefix_advanced": prefix_advanced})
        sys.exit(0)

    debug_log(f"Push sweep: range={len(push_range)} prefix_advanced="
              f"{prefix_advanced} base={base[:12]} tail={len(tail)}")

    diff_text = _git_diff_range(repo_root, base, "HEAD")
    if diff_text is None:
        # Diff failed (non-zero exit / 30s timeout / git missing). Do NOT
        # mark `tail` reviewed — we did not actually review it. Marking
        # them would silently advance the prefix past unreviewed commits
        # forever (the whole point of push-sweep is to catch outside-CC
        # commits, and a 50-commit range over large files can hit the
        # 30s timeout). skip_reason=45 lets a retry / smaller subsequent
        # push still cover them, mirroring how skip_reason=31 handles
        # too-many-files without recording the tail.
        emit_metrics({**_base, "pushed": len(push_range),
                      "unreviewed": len(tail), "skip_reason": 45})
        sys.exit(0)
    diff_files = parse_diff_into_files(diff_text)
    if not diff_files:
        emit_metrics({**_base, "pushed": len(push_range),
                      "unreviewed": len(tail), "skip_reason": 30})
        # Still mark tail reviewed — there's nothing to review.
        _append_reviewed_shas(repo_root, tail, vulns_found=0)
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1625-1627)
```python
    new_vulns, n_deduped = _dedup_against_state(
        session_id, vulns or [], prompted=_finding_keys(previous_findings)
    )
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L1665-1681)
```python
    snapshots = [
        {"filePath": v.get("filePath", ""),
         "category": v.get("category", "Unknown"),
         "vulnerableCode": v.get("vulnerableCode", "")}
        for v in reported
    ]
    def _record(state):
        existing = [f for f in state.get("previous_findings", [])
                    if isinstance(f, dict)]
        seen = {(f.get("filePath", ""), f.get("category", "")) for f in existing}
        for f in snapshots:
            k = (f["filePath"], f["category"])
            if k not in seen:
                seen.add(k); existing.append(f)
        state["previous_findings"] = existing
        state["previous_findings_ts"] = _time.time()
    with_locked_state(session_id, _record)
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

**File:** plugins/security-guidance/hooks/session_state.py (L25-46)
```python
def _state_key(session_id):
    # In CCR each user turn is a new CC process with a fresh session_id; the
    # remote session ID is stable across those restarts. Prefer it so the
    # pending-warnings sweep and any unprocessed touched_paths survive.
    key = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID") or session_id
    # The key becomes a filename component under the state dir. CC session ids
    # are UUIDs (sanitization is a no-op for them), but nothing in the hook
    # protocol guarantees that, so strip path separators and anything else
    # that could escape the state dir, and bound the length.
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(key))[:128]


def get_state_file(session_id):
    """Get session-specific state file path."""
    state_dir = os.environ.get("SECURITY_WARNINGS_STATE_DIR", os.path.expanduser("~/.claude/security"))
    return os.path.join(state_dir, f"security_warnings_state_{_state_key(session_id)}.json")


def get_lock_file(session_id):
    """Get session-specific lock file path."""
    state_dir = os.environ.get("SECURITY_WARNINGS_STATE_DIR", os.path.expanduser("~/.claude/security"))
    return os.path.join(state_dir, f"security_warnings_state_{_state_key(session_id)}.lock")
```
