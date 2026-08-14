### Title
Untracked filenames containing git pathspec magic (`:(...)`) bypass `--` separator in `_temp_index`'s `git add --intent-to-add` call, expanding the staged/diffed file set beyond the caller-supplied set - ([File: plugins/security-guidance/hooks/gitutil.py])

### Summary
`_temp_index` (`plugins/security-guidance/hooks/gitutil.py:91-141`) builds the `git add --intent-to-add -- <paths>` argument list directly from caller-supplied `untracked_paths` strings without neutralizing git's pathspec "magic" syntax (`:(glob)…`, `:(icase)…`, etc.). The `--` separator only stops option parsing; it does not disable magic-pathspec interpretation of arguments that start with `:`. A file whose literal on-disk name is a valid magic pathspec (e.g. `:(glob)**`) survives the `os.path.lexists` filter and is then handed to git as a pathspec instead of a literal filename, letting it match and intent-to-add other untracked files that were never part of the caller's intended set.

### Finding Description
In `_temp_index`: [1](#0-0) 

`surviving` is computed by checking `os.path.lexists(os.path.join(cwd, p))` — a pure filesystem existence check that treats `p` as a literal path component. If `p` equals `:(glob)**`, the filesystem check correctly finds the literal file (since attacker created a file with that exact name), so it passes the filter. That same raw string is then passed as a git pathspec argument to `git add --intent-to-add -- :(glob)**`. Git's pathspec magic syntax (`:(...)pattern`) is recognized regardless of the `--` separator — `--` only disambiguates flags from operands, it does not switch git into literal-pathspec mode (that requires `--literal-pathspecs` or a `./` prefix). As a result, the single crafted filename is expanded by git into a glob matching arbitrary files in the worktree.

Because `untracked_paths` is intended by design to be a *targeted*, often narrower-than-full-worktree set (the module doc explicitly frames the perf optimization as adding "only those paths" instead of scanning the whole tree, and `security_reminder_hook.py`'s UPS-snapshot diffing computes untracked deltas per tool call rather than the full untracked set), a magic pathspec can pull other untracked files — outside the caller's intended per-call scope — into the `--intent-to-add` operation on the temporary index. Since `get_git_diff` subsequently runs `git diff` against that same temp index, any unrelated untracked file matched by the magic pathspec becomes visible as a "new file" in the diff fed to the LLM security reviewer.

### Impact Explanation
The scoped impact is content-scope confinement bypass in the diff-generation pipeline: files the caller did not intend to expose (previously-existing, unrelated untracked files elsewhere in the worktree) can be surfaced into the LLM-reviewed diff. This does not grant code execution, credential exposure of secrets outside the workspace, or the ability to affect the user's real index (a copy is used and discarded), but it is an unintended-scope-expansion / workspace-confinement violation of the diff-generation invariant the function is documented to guarantee ("only those paths" should be added).

### Likelihood Explanation
Exploitation requires the attacker to get a file with an exact magic-pathspec name (e.g. `:(glob)**`) checked out into the victim's working tree as an untracked file — feasible via a malicious branch/PR checkout, a malicious dependency/script that drops such a file, or any workflow where attacker-controlled repository content lands in the tree before the hook runs. Colons are valid in POSIX filenames, so this is trivially reproducible on Linux/macOS (not on Windows, where `:` is invalid in filenames). No other privilege is required beyond the ability to place a file in the checked-out tree, matching the rules' "unprivileged, ordinary repository content" bar.

### Recommendation
Disable pathspec magic for these programmatically-constructed arguments: pass `--literal-pathspecs` to the `git add --intent-to-add` invocation (or prefix every path with `./` and use `git -c core.literalPathspecs=1` / `--literal-pathspecs` consistently for all git invocations in `gitutil.py` that splice caller-derived filenames after `--`, including `_diff_pathspec`'s consumer in `get_git_diff`). Alternatively, reject/skip any untracked path whose first character is `:` before adding it to `add_args`.

### Proof of Concept
Unit test sketch (pytest, POSIX only):
```python
def test_temp_index_does_not_expand_via_pathspec_magic(tmp_git_repo):
    # tmp_git_repo: fixture with an initialized repo, one committed file
    # "keep/secret.txt" untracked at test time to simulate "outside intended scope".
    (tmp_git_repo / "keep").mkdir()
    (tmp_git_repo / "keep" / "secret.txt").write_text("SECRET-CONTENT")

    # Attacker-controlled file with pathspec magic as its literal name.
    magic_name = ":(glob)**"
    (tmp_git_repo / magic_name).write_text("attacker file")

    # Caller only intends to add the magic-named file, NOT keep/secret.txt.
    with _temp_index(str(tmp_git_repo), untracked_paths=[magic_name]) as env:
        result = subprocess.run(
            [*GIT_CMD, "diff", "--name-only", "HEAD"],
            cwd=str(tmp_git_repo), capture_output=True, text=True, env=env,
        )
    surfaced = set(result.stdout.split())
    # Expected (if fixed): only the magic-named file itself is surfaced.
    assert surfaced == {magic_name}
    # Vulnerable behavior: "keep/secret.txt" also appears, proving scope
    # escaped beyond the caller-supplied untracked_paths list.
    assert "keep/secret.txt" not in surfaced
```
Expected result on the current code: `keep/secret.txt` (and potentially other untracked files) appear in `surfaced`, demonstrating the pathspec-magic expansion beyond the single file the caller passed in `untracked_paths`.

### Citations

**File:** plugins/security-guidance/hooks/gitutil.py (L119-135)
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
```
