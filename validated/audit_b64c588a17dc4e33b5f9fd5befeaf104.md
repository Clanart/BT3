### Title
Unescaped git pathspec magic in `_diff_pathspec`/`get_git_diff` lets crafted filenames silently drop from the restricted review diff - (File: `plugins/security-guidance/hooks/gitutil.py`)

### Summary
`_diff_pathspec` converts touched/review-set file paths into a literal `git diff -- <path...>` pathspec without disabling git's pathspec "magic" (no `--literal-pathspecs` / `core.literalPathspecs=true`, no `:(literal)` prefix). A repo file whose name contains characters that are magic to git pathspec matching (`[...]` character classes, `*`, `?`, or a leading `:` triggering `:(exclude)`/`:(glob)`/etc.) is not matched literally, so `get_git_diff` can silently fail to include that file's hunk in the diff handed to the Stop-hook/commit-review LLM reviewer, even though `git diff` still exits 0.

### Finding Description
`_diff_pathspec` (`plugins/security-guidance/hooks/gitutil.py`, lines 70-88) realpath-normalizes and computes `os.path.relpath` for each touched/review path, then returns `["--"] + rel`, which `get_git_diff` (lines 391-427) splices directly onto `[*GIT_CMD, "diff", ..., baseline_sha] + pathspec`. `GIT_CMD` (lines 25-29) sets `core.fsmonitor=false` and `core.hooksPath=/dev/null` but never sets `core.literalPathspecs=true`, and no call site passes `--literal-pathspecs`.

Git treats path arguments after `--` as pathspecs, not plain strings, unless literal-pathspec mode is enabled. Pathspec magic includes:
- a leading `:` opening a magic signature, e.g. `:(exclude)`, `:(glob)`, `:(icase)`
- glob wildcards `*`, `?`, and `[...]` character classes

All of these are legal characters in POSIX/git filenames. If the review set (`compute_v2_review_set` in `plugins/security-guidance/hooks/diffstate.py`, feeding `get_git_diff`'s `paths` argument) contains a file such as `foo[bar].py`, the pathspec emitted is the literal string `foo[bar].py`, which git interprets as "match `foo` + one of `a/b/r` + nothing further" — it does not match the real file `foo[bar].py` at all. `git diff -- 'foo[bar].py'` then exits 0 with empty output for that path (no error, no non-matching-pathspec failure), so `get_git_diff` returns a diff that is silently missing that file's hunk, while `parse_diff_into_files`/`extract_file_paths_from_diff` (which parse whatever diff text comes back) never see it either. The invariant that "reviewable-source filtering must not let attacker-crafted filenames hide dangerous source changes" is broken at the pathspec layer, upstream of the extension/basename filtering in `_is_reviewable_source`.

This is reachable because `paths`/`untracked_paths` passed into `get_git_diff` originate from git-derived, repo-content-influenced state (`record_touched_path`, `compute_v2_review_set`'s `tracked_dirty`/`untracked` sets) — an attacker who can get Claude Code to create, rename, or edit a file with such a name (e.g. via prompt-injected instructions in repo content, an issue/PR description, or a script Claude is asked to run) controls the exact string that becomes the pathspec.

### Impact Explanation
If the crafted filename's diff is silently excluded from the text handed to the Stop-hook/commit-review LLM reviewer, dangerous code changes in that file bypass the automated security review entirely while still being committed/pushed by Claude Code's normal git automation. This matches "Unauthorized local command execution that bypasses Claude Code approval or deny controls" in the sense that a malicious change (e.g. a backdoored script or command) can reach the repository and subsequent execution without ever being surfaced to the reviewer gate that is supposed to catch it.

### Likelihood Explanation
Feasibility depends entirely on an attacker being able to influence the *name* of a file that Claude Code creates/edits during a session (e.g., via repo content that instructs Claude to write to a specifically-named path, or a filename already present in a cloned malicious repo that gets modified). This is a plausible but non-trivial precondition — it requires either prompt-injection influence over tool calls or a pre-existing oddly-named file being the vector for change, not attacker-controlled shell access. Given git filenames with `[`, `]`, `*`, `?`, or leading `:` are rare but fully legal, this is a real but narrow-window bug class rather than a trivially always-exploitable one.

### Recommendation
Force literal pathspec interpretation everywhere `_diff_pathspec`'s output is used: add `-c core.literalPathspecs=true` to `GIT_CMD` (or pass `--literal-pathspecs` explicitly on every `git diff`/`git add`/`git status` invocation in `gitutil.py`), so pathspec arguments are matched as exact literal strings and glob/exclude magic can never be triggered by attacker-controlled filenames.

### Proof of Concept
Unit/integration test plan (pytest, using a temp git repo):
1. Init a repo, commit an initial file, capture baseline SHA.
2. Create and stage a file named `foo[bar].py` containing an obviously dangerous line (e.g. `os.system(...)`).
3. Call `get_git_diff(cwd, baseline_sha, paths=[os.path.join(cwd, "foo[bar].py")])`.
4. Assert (failing today): the returned diff text does **not** contain `foo[bar].py` or the dangerous line, despite the file being genuinely changed — i.e. `"foo[bar].py" not in diff_output` even though `git diff` (unrestricted, no pathspec) shows the change.
5. After applying the fix (`core.literalPathspecs=true`), rerun the same test and assert the diff **does** contain `foo[bar].py` and the dangerous line, confirming literal matching restores the file to the reviewable set.