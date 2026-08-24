## Title
Pathspec-magic injection via unsanitized file paths in `ensureRelativePath` allows a crafted repository file/commit to expand git diff scope beyond the selected file — (File: `app/src/lib/git/diff.ts`)

### Summary
`ensureRelativePath` only guards against **absolute** paths, not paths that begin with git pathspec magic syntax (`:(...)`). Since `CommittedFileChange.path`/`oldPath` and `WorkingDirectoryFileChange` paths come directly from `git log`/`git status` output (i.e., from repository content an attacker controls), a maliciously named tracked file can be passed unwrapped as the trailing pathspec argument to `git diff`/`git log -- <path>` calls, letting git interpret it as pathspec magic (e.g. `:(exclude)…`) instead of a literal filename.

### Finding Description
`ensureRelativePath` is defined as: [1](#0-0) 

It only special-cases `isAbsolute(path)` (POSIX leading `/` or Windows drive letter/UNC forms). It does not check whether `path` begins with `:` — the character that triggers git's pathspec "magic signature" parsing (`:(top,literal)`, `:(exclude)`, `:(glob)`, etc.). Git parses pathspec magic on any argument starting with `:` regardless of whether it follows `--`; `--` only ends option parsing, it does not force literal/verbatim pathspec interpretation (that requires `--literal-pathspecs`, `core.literalPathspecs`, or `GIT_LITERAL_PATHSPECS`, none of which Desktop sets here).

This unguarded value is used as the sole "select this one file" pathspec in multiple diff-building call sites, e.g. `getCommitDiff`: [2](#0-1) 

and `getBranchMergeBaseDiff`: [3](#0-2) 

and `getCommitRangeDiff` follows the same pattern: [4](#0-3) 

If a repository (which an attacker can fully control the content of, e.g. via a crafted clone/fetch source) contains a tracked path whose name literally begins with a pathspec magic token — for example a file named `:(exclude)secret.txt` — then when a user opens that specific `CommittedFileChange` in Desktop's history view, the resulting git invocation becomes effectively:

```
git log <sha> -m -1 --first-parent --patch-with-raw --format= -z --no-color -- :(exclude)secret.txt
```

Because no other positive pathspec is supplied, git's pathspec semantics treat a lone `exclude` pathspec as "match everything except this pattern," not "match nothing." The practical effect is that the diff scope silently expands from "only the file the user clicked" to "every other file changed in that commit," which is exactly the caller's broken assumption described in the question (`-- <path>` is assumed to select only the named file).

### Impact Explanation
`buildDiff` (the consumer of this git output, called immediately after each of the above) is written under the assumption that the returned patch corresponds to a single selected `FileChange`. If the underlying git command instead returns diff hunks for other files in the commit (due to the exclude-magic pathspec), those unrelated files' contents get parsed and surfaced through the same code path that is supposed to be scoped to the one file the user explicitly selected — a scope violation consistent with the "diff including… unintended repository files" impact described in the question. This could expose file contents the user never chose to inspect (in the diff viewer, and potentially onward into any downstream consumer of that diff object, such as conflict-resolution or context-building flows), all sourced from the same repository so it is not a cross-repository/filesystem read, but it is a violation of the intended single-file scoping guarantee.

Note: I was not able to fully trace `buildDiff`'s exact parsing behavior for a multi-file raw diff blob in this pass (it's plausible it would either mis-render or partially render extra file sections), so the precise UI-facing manifestation is not fully confirmed and would need direct testing against a crafted repository.

### Likelihood Explanation
Exploitability requires only that the attacker control repository content the victim opens/clones/fetches — no local access, credentials, or unusual user action beyond normal browsing of commit history/diffs in Desktop, which matches the in-scope threat model. Filenames containing `:` and `(`/`)` are valid on Linux/macOS filesystems and are valid git tree entry names on all platforms (git's object database does not enforce host filesystem naming rules), so a malicious commit containing such a path is straightforward to craft. Actual on-disk checkout of such a file may fail on Windows (colon is invalid in NTFS filenames), but `getCommitDiff`/`getCommitRangeDiff` operate against git's object database via `git log`/`git diff` and do not require the path to exist as a real file in the working tree, so likelihood is not strictly limited to POSIX checkouts.

### Recommendation
Extend `ensureRelativePath` (or add a dedicated guard) to detect and neutralize any path beginning with `:` before it is used as a `--` pathspec argument — e.g., always prefix such paths with `:(literal)` (or `:(top,literal)`) regardless of `isAbsolute`, so git never interprets user/repo-controlled filenames as pathspec magic. Alternatively, invoke git with `--literal-pathspecs` (or set `GIT_LITERAL_PATHSPECS=1`) for all diff/log commands that take file-selecting arguments derived from repository data.

### Proof of Concept
1. Create a repository containing a commit that adds a tracked file named `:(exclude)decoy.txt` alongside another file `secret.txt` in the same commit.
2. Clone/fetch this repository into GitHub Desktop and open the commit in history view, selecting the `:(exclude)decoy.txt` entry to view its diff (`getCommitDiff` is invoked with `file.path === ':(exclude)decoy.txt'`).
3. Because `isAbsolute(':(exclude)decoy.txt')` is `false`, `ensureRelativePath` returns the string unchanged, and the resulting `git log … -- :(exclude)decoy.txt` command is executed.
4. Inspect the raw stdout: confirm the diff output contains hunks for `secret.txt` (and any other files changed in that commit) rather than being empty/limited to `decoy.txt`, demonstrating the pathspec scope escaped the intended single-file selection. [1](#0-0) [2](#0-1)

### Citations

**File:** app/src/lib/git/diff.ts (L121-141)
```typescript
  const args = [
    'log',
    commitish,
    ...(hideWhitespaceInDiff ? ['-w'] : []),
    '-m',
    '-1',
    '--first-parent',
    '--patch-with-raw',
    '--format=',
    '-z',
    '--no-color',
    '--',
    ensureRelativePath(file.path),
  ]

  if (
    file.status.kind === AppFileStatusKind.Renamed ||
    file.status.kind === AppFileStatusKind.Copied
  ) {
    args.push(ensureRelativePath(file.status.oldPath))
  }
```

**File:** app/src/lib/git/diff.ts (L162-180)
```typescript
  const args = [
    'diff',
    '--merge-base',
    baseBranchName,
    comparisonBranchName,
    ...(hideWhitespaceInDiff ? ['-w'] : []),
    '--patch-with-raw',
    '-z',
    '--no-color',
    '--',
    ensureRelativePath(file.path),
  ]

  if (
    file.status.kind === AppFileStatusKind.Renamed ||
    file.status.kind === AppFileStatusKind.Copied
  ) {
    args.push(ensureRelativePath(file.status.oldPath))
  }
```

**File:** app/src/lib/git/diff.ts (L207-218)
```typescript
  const args = [
    'diff',
    oldestCommitRef,
    latestCommit,
    ...(hideWhitespaceInDiff ? ['-w'] : []),
    '--patch-with-raw',
    '--format=',
    '-z',
    '--no-color',
    '--',
    ensureRelativePath(file.path),
  ]
```

**File:** app/src/lib/git/diff.ts (L999-1004)
```typescript
// Prefix absolute path with `:(top,literal)` to ensure that git treats it as a
// literal path. This is important for paths that appear to be absolute paths on
// some platforms and not others. See
// https://git-scm.com/docs/gitglossary#Documentation/gitglossary.txt-top
const ensureRelativePath = (path: string) =>
  isAbsolute(path) ? `:(top,literal)${path}` : path
```
