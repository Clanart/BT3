### Title
Colon-prefixed untracked filenames trigger git pathspec-magic parsing and an unchecked `git add --intent-to-add` failure that silently drops files from security review - (File: `plugins/security-guidance/hooks/gitutil.py`)

### Summary
`_temp_index` builds a `git add --intent-to-add -- <paths>` pathspec list directly from real filesystem/`git status` output without neutralizing git's pathspec "magic" syntax (`:(...)`/leading `:`), and it never checks the subprocess's return code. A repo-controlled untracked file whose name starts with `:` can make the whole `add --intent-to-add` invocation fail, and because the failure is silently ignored, every untracked file for that review turn is dropped from the temp index and therefore from the diff shown to the LLM security reviewer.

### Finding Description
`_temp_index` (`plugins/security-guidance/hooks/gitutil.py:91-141`) computes `surviving` paths only by existence (`os.path.lexists`) and passes them straight to git after `--`: [1](#0-0) 

Git's `--` end-of-options marker stops flag parsing but does **not** disable pathspec "magic": any argument beginning with `:` (e.g. `:(exclude)`, `:(icase)`, or simply a malformed `:xyz`) is parsed as pathspec magic unless the caller passes `--literal-pathspecs` / sets `GIT_LITERAL_PATHSPECS=1` or prefixes the path with `./`. Neither is done anywhere pathspecs are built in this module (`_temp_index` here, and `_diff_pathspec` at lines 70-88, which has the same issue since it also emits raw relative path strings after `--`) [2](#0-1) .

The untracked filenames themselves come from real, attacker-influenced filesystem state — `_git_status_porcelain`/`_list_untracked` list whatever files actually exist untracked in the worktree, including files created by repo-controlled automation (build scripts, generators, postinstall hooks, etc.) that Claude Code executes as part of normal workflow [3](#0-2) [4](#0-3) . If such a file's name begins with `:` and forms invalid/unexpected pathspec magic, `git add --intent-to-add -- ... :bad ...` is atomic and exits non-zero on the malformed pathspec — the same "one bad path poisons the whole call" behavior the code's own comment already documents for the *missing-path* case: [5](#0-4) 

But the subprocess result is never checked: [6](#0-5) 

So when the call fails, execution proceeds as if it succeeded, `env` still points at the temp index (which now contains none of the intent-to-add entries), and `get_git_diff` runs `git diff` against that index [7](#0-6) . Every untracked file for that turn — including genuinely new, Claude-authored files that should be reviewed — is invisible in the diff handed to the LLM reviewer.

### Impact Explanation
This is a security-control bypass: the diff-based LLM security review can be silently and completely defeated for a whole review turn by the mere presence of one attacker-influenced, oddly-named untracked file, without any error surfaced to the user or the review pipeline. New/malicious files written during that turn are never shown to the reviewer, matching the "Security-control bypass that silently disables or routes around blocking/review" impact category. Note: the impact is confined to within-repo review scoping/omission — no evidence supports a literal escape of `git add`/`git diff` outside the repo's worktree via pathspec magic, since git subcommands are always bound to the working tree/index they operate on.

### Likelihood Explanation
Preconditions: the attacker must be able to get a colon-prefixed filename created as an untracked file in the victim's working tree during a Claude Code session (e.g. via a checked-out malicious branch/PR whose build tooling, generators, or scripts drop such a file — a realistic outcome of normal repo automation that Claude Code runs). No elevated privilege is required beyond ordinary repo content control. The bug is fully deterministic and repeatable: any turn with such a file present will exhibit the omission, since `_temp_index` has no error handling at all for this path.

### Recommendation
- In `_temp_index`, check `result.returncode` from the `git add --intent-to-add` call and, on failure, fall back to per-path `add -N -- <single-path>` retries (so one bad entry doesn't zero out the whole set), and/or set `GIT_LITERAL_PATHSPECS=1` in `env` (or pass `--literal-pathspecs` to the `git` invocation) so filenames are never interpreted as pathspec magic.
- Apply the same literal-pathspec hardening to `_diff_pathspec`/`get_git_diff`'s pathspec construction.
- Log/telemetry on add failures so silently-skipped reviews are observable instead of invisible.

### Proof of Concept
Unit/integration test plan (pytest, using a real temp git repo):
1. Init a repo with one commit, `HEAD` present.
2. Create two untracked files: `normal.py` (benign) and `:weird` (colon-prefixed, chosen to be invalid/unexpected pathspec magic, e.g. `:(nonsense)x`).
3. Call `gitutil.get_git_diff(cwd, baseline_sha=head_sha, untracked_paths=["normal.py", ":weird"])`.
4. Assert expectation without fix: the returned diff does **not** contain `normal.py` content (bug reproduced — `git add --intent-to-add` failed atomically and silently, so no untracked file appears in the diff at all).
5. Assert expectation after fix: the diff contains `normal.py`'s content (added lines), proving the fallback/hardening isolates the bad pathspec instead of poisoning the whole batch.
6. Additional fuzz test: iterate over a small corpus of colon-prefixed and other git-pathspec-magic-triggering names (`:(exclude)`, `:(icase)`, `:/`, `:!`) as untracked filenames and assert that in every case the diff for co-existing normal untracked files remains populated.

### Citations

**File:** plugins/security-guidance/hooks/gitutil.py (L70-88)
```python
def _diff_pathspec(cwd, paths):
    """Convert absolute touched-paths to repo-relative pathspec args for
    git diff. Paths outside cwd (e.g. ~/.claude/…) are dropped. Returns the
    list to splice after `--`, or [] for an unrestricted diff. realpath both
    sides so the macOS /var ↔ /private/var symlink doesn't make in-repo
    paths look external."""
    if not paths:
        return []
    cwd_abs = os.path.realpath(cwd)
    rel = []
    for p in paths:
        try:
            r = os.path.relpath(os.path.realpath(p), cwd_abs)
        except ValueError:
            continue
        if r.startswith(".."):
            continue
        rel.append(r)
    return ["--"] + rel if rel else []
```

**File:** plugins/security-guidance/hooks/gitutil.py (L119-136)
```python
        elif untracked_paths:
            # `git add -N -- a b nonexistent` is atomic — one missing path
            # makes it exit 128 and add NOTHING, so a file removed between
            # `git status` and here would silently drop ALL untracked files
            # from the diff. --ignore-missing only works with --dry-run, so
            # filter to surviving paths (lexists so dangling symlinks count).
            surviving = [p for p in untracked_paths
                         if os.path.lexists(os.path.join(cwd, p))]
            add_args = ["--"] + surviving if surviving else None
        else:
            add_args = None
        if add_args:
            subprocess.run(
                [*GIT_CMD, "add", "--intent-to-add"] + add_args,
                cwd=cwd, capture_output=True, text=True, timeout=10,
                env=env,
            )
        yield env
```

**File:** plugins/security-guidance/hooks/gitutil.py (L414-424)
```python
    cmd = [*GIT_CMD, "diff", "--no-color", "--no-ext-diff", baseline_sha] + (["--unified=99999"] if full_context else []) + pathspec
    try:
        with _temp_index(cwd, untracked_paths) as env:
            # env is None when no index could be found (bare repo / not a
            # repo) — diff still runs, just without untracked-file support.
            result = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=30, env=env)
        if result.returncode != 0:
            debug_log(f"git diff failed: {result.stderr[:200].decode('utf-8', errors='replace')}")
            return None
        # Decode with errors='replace' so binary diffs don't crash
        return result.stdout.decode("utf-8", errors="replace")
```

**File:** plugins/security-guidance/hooks/diffstate.py (L319-352)
```python
def _list_untracked(cwd):
    """Repo-root-relative untracked (and not-ignored) path → mtime_ns, or {}
    on error. Used at UPS to snapshot the pre-turn untracked set so the Stop
    hook can exclude unchanged pre-existing untracked files from review.
    mtime is captured so an in-place edit during the turn is still reviewed.

    Uses ls-files (not status) for the UPS path: the index diff isn't needed,
    and ls-files --others only walks the worktree against .gitignore."""
    try:
        repo = _git_toplevel(cwd) or cwd
        r = subprocess.run(
            [*GIT_CMD, "-c", "core.quotePath=false", "ls-files",
             "--others", "--exclude-standard", "-z"],
            cwd=repo, capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            debug_log(f"_list_untracked rc={r.returncode}: {r.stderr[:200]}")
            return {}
        out = {}
        for p in r.stdout.split("\0"):
            if not p:
                continue
            try:
                out[p] = os.stat(os.path.join(repo, p)).st_mtime_ns
            except OSError:
                out[p] = 0
            if len(out) >= UNTRACKED_BASELINE_CAP:
                debug_log(f"_list_untracked: capped at {UNTRACKED_BASELINE_CAP}")
                break
        return out
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        debug_log(f"_list_untracked error: {e}")
        return {}

```

**File:** plugins/security-guidance/hooks/diffstate.py (L386-401)
```python
    tracked_dirty, untracked = _git_status_porcelain(repo)
    if tracked_dirty is None:
        return [], "HEAD", repo, [], {"dirty_now_count": -1, "changed_since_count": -1, "review_set_count": 0}

    def _unchanged_since_baseline(p):
        base_mtime = untracked_at_baseline.get(p)
        if base_mtime is None:
            return False
        try:
            return os.stat(os.path.join(repo, p)).st_mtime_ns == base_mtime
        except OSError:
            return False

    preexisting_unchanged = {p for p in untracked if _unchanged_since_baseline(p)}
    new_untracked = untracked - preexisting_unchanged
    dirty_now = tracked_dirty | new_untracked
```
