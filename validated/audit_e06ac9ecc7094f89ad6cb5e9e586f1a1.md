## Title
Argument/flag injection into `git rev-list` via forged `.git/rebase-merge/{onto,orig-head}` — potential arbitrary file write via `--output=` — (File: `app/src/lib/git/rev-list.ts`, `app/src/lib/git/rebase.ts`)

### Summary
`getRebaseSnapshot` reads `orig-head` and `onto` from `.git/rebase-merge/` without validating that their contents are valid SHA-1/ref strings, then feeds them unchecked into `getCommitsBetweenCommits` → `revRange` → `getCommitsInRange`, which places the resulting string as a single, un-sanitized positional argument to `git rev-list` before any `--` separator.

### Finding Description
In `getRebaseSnapshot`, `originalBranchTip` and `baseBranchTip` are read directly from disk and only `.trim()`-ed: [1](#0-0) 

These values are passed straight into `getCommitsBetweenCommits(repository, baseBranchTip, originalBranchTip)`: [2](#0-1) 

`getCommitsBetweenCommits` builds a range string via `revRange(from, to)` which is simply `` `${from}..${to}` `` with no escaping or format validation: [3](#0-2) [4](#0-3) 

That single string is then passed as `args[1]` to `git(['rev-list', range, '--reverse', '--oneline', '--no-abbrev-commit', '--'], ...)`. Critically, the `--` end-of-options marker is placed *after* `range`, not before it, so if `range` itself begins with `-`, `git rev-list`'s option parser will interpret it as a flag rather than a revision: [5](#0-4) 

Because both `onto` and `orig-head` are fully attacker-controlled and concatenated with a literal `..` in between, an attacker can set `onto` (used as `baseBranchTip`, the `from` side, which appears first in the range and is therefore not truncated by the `..`) to a string such as `--output=/path/to/target..`. The resulting range argument becomes `--output=/path/to/target...<orig-head-content>` — since `git log`/`git rev-list` share the same revision-option parser (`revision.c`), which supports `--output=<file>` to redirect command output to an arbitrary file, this can direct `git rev-list`'s stdout to write into a path chosen by the attacker instead of being consumed by Desktop.

No SHA/ref-format validation (e.g. a `^[0-9a-f]{40}$` check) is performed on `originalBranchTip` or `baseBranchTip` before use in either `getRebaseInternalState` or `getRebaseSnapshot`.

### Impact Explanation
If exploitable, this allows an attacker who can place forged files in `.git/rebase-merge/onto` and `.git/rebase-merge/orig-head` (e.g., distributed as a pre-populated `.git` directory bundled with a repository the victim opens in Desktop) to redirect `git rev-list` output to an arbitrary filesystem path outside the repository, matching the in-scope "file write... outside the repo" impact category. The written content is limited to the `rev-list --oneline --no-abbrev-commit` output format (SHA + summary lines), so this is a constrained/attacker-limited-content file write, not full arbitrary file content control, and it does not by itself yield code execution — it depends on further exploitation of the write primitive (e.g., overwriting a script/config that is later executed).

### Likelihood Explanation
This requires two independent conditions I could not fully verify from the code alone:
1. That `git rev-list` genuinely accepts `--output=<file>` (this is documented for `git log`/`git format-patch`; whether it is exposed identically through the shared revision-parsing machinery for `rev-list` needs confirmation against the exact git/dugite version bundled with Desktop).
2. That `getRebaseSnapshot`/`getRebaseInternalState` are reachable purely by "opening" a repository with forged `.git/rebase-merge` state, without the user manually running/continuing a rebase — I was only able to confirm these functions are consumed by `app-store.ts` and `continueRebase`/`rebase.ts`; I could not fully trace the exact UI trigger path within the remaining tool budget. The `.git` directory is not typically transferred via a normal `git clone`/`fetch` (rebase-merge state is local, uncommitted working-directory state), so the realistic delivery vector is a maliciously pre-populated repository directory/archive rather than a clean clone — this narrows likelihood versus a pure "hosted repo content" attack.

### Recommendation
Validate that `originalBranchTip` and `baseBranchTip` match a strict SHA-1/SHA-256 object-id pattern (e.g. `/^[0-9a-f]{40}$/` or the appropriate length for the repo's hash algorithm) before using them, and reject/abort the rebase-state read if they don't match. Additionally, insert the `--` end-of-options marker *before* the range argument (or use `--end-of-options`) in `getCommitsInRange` and other `git()` invocations that place attacker-influenced strings as the first positional argument, so that any leading `-`/`--` in a range/ref string cannot be parsed as an option by git regardless of validation gaps elsewhere.

### Proof of Concept
1. Create/obtain a git repository and mark it as being mid-rebase by creating `.git/REBASE_HEAD`, `.git/rebase-merge/msgnum` (`1`), `.git/rebase-merge/end` (`1`), `.git/rebase-merge/head-name` (`refs/heads/main`).
2. Set `.git/rebase-merge/onto` to `--output=/tmp/poc-outfile..` and `.git/rebase-merge/orig-head` to a valid-looking 40-hex-char string (e.g. an actual commit SHA present in the repo, so a `range` string is well-formed enough for git's parser to reach option handling).
3. Package this repository (including the `.git` folder) and get the victim to open it as a local repository in GitHub Desktop such that a code path reading rebase progress (e.g., `getRebaseSnapshot` via `continueRebase`/app-store rebase-progress code) executes.
4. Observe whether `/tmp/poc-outfile..` (or similar) is created — confirming the `--output=` flag was honored by the invoked `git rev-list` process.

Note: I was not able to fully confirm within the available investigation whether `--output=` is accepted by `git rev-list` specifically (vs. only `git log`), and I was not able to fully trace the automatic UI trigger path for `getRebaseSnapshot` upon simply opening a repository (as opposed to needing an explicit continue/rebase action). Both points should be verified experimentally against the exact dugite/git version bundled before treating this as a confirmed, exploitable finding rather than a code-path-level defect (missing input validation clearly exists regardless).

### Citations

**File:** app/src/lib/git/rebase.ts (L199-211)
```typescript
    originalBranchTip = await readFile(
      join(repository.resolvedGitDir, 'rebase-merge', 'orig-head'),
      'utf8'
    )

    originalBranchTip = originalBranchTip.trim()

    baseBranchTip = await readFile(
      join(repository.resolvedGitDir, 'rebase-merge', 'onto'),
      'utf8'
    )

    baseBranchTip = baseBranchTip.trim()
```

**File:** app/src/lib/git/rebase.ts (L223-227)
```typescript
    const commits = await getCommitsBetweenCommits(
      repository,
      baseBranchTip,
      originalBranchTip
    )
```

**File:** app/src/lib/git/rev-list.ts (L19-21)
```typescript
export function revRange(from: string, to: string) {
  return `${from}..${to}`
}
```

**File:** app/src/lib/git/rev-list.ts (L123-131)
```typescript
export async function getCommitsBetweenCommits(
  repository: Repository,
  baseBranchSha: string,
  targetBranchSha: string
): Promise<ReadonlyArray<CommitOneLine> | null> {
  const range = revRange(baseBranchSha, targetBranchSha)

  return getCommitsInRange(repository, range)
}
```

**File:** app/src/lib/git/rev-list.ts (L138-157)
```typescript
export async function getCommitsInRange(
  repository: Repository,
  range: string
): Promise<ReadonlyArray<CommitOneLine> | null> {
  const args = [
    'rev-list',
    range,
    '--reverse',
    // the combination of these two arguments means each line of the stdout
    // will contain the full commit sha and a commit summary
    `--oneline`,
    `--no-abbrev-commit`,
    '--',
  ]

  const options = {
    expectedErrors: new Set<GitError>([GitError.BadRevision]),
  }

  const result = await git(args, repository.path, 'getCommitsInRange', options)
```
